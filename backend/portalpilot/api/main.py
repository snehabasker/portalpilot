"""Operator API: onboarding, batches, task replay, human-in-the-loop signals, live event stream."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

import asyncpg
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Response, UploadFile, WebSocket, \
    WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest
from pydantic import BaseModel
from starlette.requests import Request
from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from .. import db, incidents, storage, vault
from ..config import settings
from ..observability import HTTP_REQUESTS, HTTP_SECONDS, BusinessCollector
from ..onboarding import claude_header_mapper, parse_csv
from ..orchestration.shared import ApprovalDecision, BatchInput, InputProvided, OperatorAction, Release
from ..orchestration.workflows import BatchWorkflow, ClaimTaskWorkflow

log = logging.getLogger("portalpilot.api")


# ------------------------------------------------------------------------------------------- live fan-out
class Hub:
    """One LISTEN connection per API replica; events fan out to the sockets subscribed to that task / batch / all."""

    def __init__(self) -> None:
        self.subs: dict[WebSocket, dict] = {}
        self.conn: asyncpg.Connection | None = None
        self.queue: asyncio.Queue[int] = asyncio.Queue()

    async def start(self) -> None:
        self.conn = await asyncpg.connect(settings().database_url)
        await self.conn.add_listener("task_events", lambda *a: self.queue.put_nowait(int(a[3])))
        asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        p = await db.pool()
        while True:
            eid = await self.queue.get()
            row = await p.fetchrow("SELECT id, task_id, batch_id, type, payload, created_at FROM task_events WHERE id = $1", eid)
            if row is None:
                continue
            msg = json.dumps({"id": row["id"], "task_id": str(row["task_id"]) if row["task_id"] else None,
                              "batch_id": str(row["batch_id"]) if row["batch_id"] else None, "type": row["type"],
                              "payload": row["payload"], "at": row["created_at"].isoformat()})
            for ws, f in list(self.subs.items()):
                if (f.get("task_id") and f["task_id"] != (str(row["task_id"]) if row["task_id"] else None)) or \
                   (f.get("batch_id") and f["batch_id"] != (str(row["batch_id"]) if row["batch_id"] else None)):
                    continue
                try:
                    await ws.send_text(msg)
                except Exception:
                    self.subs.pop(ws, None)


hub = Hub()
_temporal: Client | None = None


async def temporal() -> Client:
    global _temporal
    if _temporal is None:
        s = settings()
        _temporal = await Client.connect(s.temporal_address, namespace=s.temporal_namespace)
    return _temporal


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO)
    await db.migrate()
    await asyncio.to_thread(storage.ensure_bucket)
    await hub.start()
    collector = BusinessCollector(asyncio.get_running_loop(), db.pool)
    REGISTRY.register(collector)
    yield
    REGISTRY.unregister(collector)
    await db.close()


app = FastAPI(title="PortalPilot", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def http_metrics(request: Request, call_next):
    t0, code = time.perf_counter(), 500
    try:
        resp = await call_next(request)
        code = resp.status_code
        return resp
    finally:
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")  # the template (/api/tasks/{task_id}), never the raw id
        if path != "/metrics":
            HTTP_REQUESTS.labels(request.method, path, str(code)).inc()
            HTTP_SECONDS.labels(request.method, path).observe(time.perf_counter() - t0)


def operator(authorization: Optional[str] = Header(None), token: Optional[str] = Query(None),
             x_operator: Optional[str] = Header(None)) -> str:
    supplied = (authorization or "").removeprefix("Bearer ").strip() or token
    if supplied != settings().operator_token:
        raise HTTPException(401, "bad operator token")
    return (x_operator or "operator").strip()[:64]


async def tenant_id() -> Any:
    return await (await db.pool()).fetchval("SELECT id FROM tenants WHERE slug = 'demo'")


def _jsonable(r: asyncpg.Record | dict) -> dict:
    out = {}
    for k, v in dict(r).items():
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        elif v.__class__.__name__ in ("UUID", "Decimal"):
            v = str(v) if v.__class__.__name__ == "UUID" else float(v)
        elif isinstance(v, (bytes, bytearray)):
            continue
        out[k] = v
    return out


# ------------------------------------------------------------------------------------------- health
@app.get("/api/healthz")
async def healthz():
    await (await db.pool()).fetchval("SELECT 1")
    return {"ok": True}


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics():
    # The business collector queries Postgres through this loop, so render off-loop to avoid a deadlock.
    body = await asyncio.to_thread(generate_latest, REGISTRY)
    return Response(body, media_type=CONTENT_TYPE_LATEST)


# ------------------------------------------------------------------------------------------- credentials
class CredentialIn(BaseModel):
    portal: str = "acme-claims"
    label: str
    username: str
    password: str


@app.post("/api/credentials")
async def create_credential(c: CredentialIn, op: str = Depends(operator)):
    sealed = vault.seal(c.password)
    p = await db.pool()
    tid = await tenant_id()
    cid = await p.fetchval(
        """INSERT INTO credentials (tenant_id, portal, label, username, secret_ct, dek_ct, key_provider, key_id)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
           ON CONFLICT (tenant_id, portal, label) DO UPDATE SET username = EXCLUDED.username,
               secret_ct = EXCLUDED.secret_ct, dek_ct = EXCLUDED.dek_ct, revoked_at = NULL
           RETURNING id""",
        tid, c.portal, c.label, c.username, sealed.secret_ct, sealed.dek_ct, sealed.provider, sealed.key_id)
    await db.audit(f"operator:{op}", "credential.stored", "credential", cid, {"portal": c.portal, "label": c.label}, tid)
    return {"id": str(cid)}


@app.get("/api/credentials")
async def list_credentials(op: str = Depends(operator)):
    rows = await (await db.pool()).fetch(
        "SELECT id, portal, label, username, key_provider, created_at, last_used_at FROM credentials WHERE revoked_at IS NULL")
    return [_jsonable(r) for r in rows]


# ------------------------------------------------------------------------------------------- onboarding + batches
def _mapper():
    import os
    return claude_header_mapper(settings().claude_model) if os.environ.get("ANTHROPIC_API_KEY") else None


@app.post("/api/batches/preview")
async def preview_batch(file: UploadFile = File(...), day_first: bool = Form(True), portal: str = Form("acme-claims"),
                        op: str = Depends(operator)):
    mapping, rows = parse_csv(await file.read(), "demo", portal, day_first, llm=_mapper())
    return {"mapping": [m.__dict__ for m in mapping],
            "rows": [{"row_number": r.row_number, "valid": r.valid, "payload": r.payload, "errors": r.errors,
                      "warnings": r.warnings, "idempotency_key": r.idempotency_key} for r in rows],
            "summary": {"total": len(rows), "valid": sum(r.valid for r in rows)}}


@app.post("/api/batches")
async def create_batch(file: UploadFile = File(...), name: str = Form(...), credential_id: str = Form(...),
                       approval_threshold: Optional[float] = Form(None), max_concurrency: int = Form(3),
                       day_first: bool = Form(True), portal: str = Form("acme-claims"),
                       source_ref: Optional[str] = Form(None), op: str = Depends(operator)):
    """`source_ref` identifies where the file came from (the intake Lambda sends `s3://bucket/key#etag`). The same
    ref twice returns the first batch instead of creating another: event delivery is at-least-once."""
    p = await db.pool()
    tid = await tenant_id()
    if source_ref:
        existing = await p.fetchval("SELECT id FROM batches WHERE tenant_id = $1 AND source_ref = $2", tid, source_ref)
        if existing:
            return await _redelivered(existing)
    mapping, rows = parse_csv(await file.read(), "demo", portal, day_first, llm=_mapper())
    runnable: list[str] = []
    skipped: list[dict] = []
    try:
        bid = await _insert_batch(p, tid, name, portal, credential_id, file.filename, mapping, rows, approval_threshold,
                                  max_concurrency, op, source_ref, runnable, skipped)
    except asyncpg.UniqueViolationError:
        if not source_ref:
            raise
        # a concurrent delivery of the same object won the insert
        existing = await p.fetchval("SELECT id FROM batches WHERE tenant_id = $1 AND source_ref = $2", tid, source_ref)
        return await _redelivered(existing)
    await _start_batch(bid, runnable, max_concurrency)
    return {"id": str(bid), "runnable": len(runnable), "invalid": sum(not r.valid for r in rows), "skipped": skipped}


async def _start_batch(bid, runnable: list[str], max_concurrency: int) -> None:
    p = await db.pool()
    wf_id = f"batch-{bid}"
    try:
        await (await temporal()).start_workflow(BatchWorkflow.run,
                                                BatchInput(str(bid), runnable, max(1, min(max_concurrency, 10))),
                                                id=wf_id, task_queue=settings().task_queue)
    except WorkflowAlreadyStartedError:
        pass  # a redelivery raced us here; the workflow id makes the start idempotent
    await p.execute("UPDATE batches SET workflow_id = $2 WHERE id = $1", bid, wf_id)
    for t in runnable:
        await p.execute("UPDATE tasks SET workflow_id = $2 WHERE id = $1", t, f"task-{t}")
    await db.publish("batch.started", {"runnable": len(runnable)}, batch_id=bid)


async def _redelivered(bid) -> dict:
    """The same source file again. If the first delivery stored the batch but died before starting it, start it
    now; otherwise report the existing batch."""
    p = await db.pool()
    b = await p.fetchrow("SELECT workflow_id, max_concurrency FROM batches WHERE id = $1", bid)
    resumed = 0
    if b["workflow_id"] is None:
        runnable = [str(r["id"]) for r in await p.fetch(
            "SELECT id FROM tasks WHERE batch_id = $1 AND status = 'queued' ORDER BY row_number", bid)]
        await _start_batch(bid, runnable, b["max_concurrency"])
        resumed = len(runnable)
    return {"id": str(bid), "duplicate": True, "runnable": resumed, "invalid": 0, "skipped": []}


async def _insert_batch(p, tid, name, portal, credential_id, filename, mapping, rows, approval_threshold,
                        max_concurrency, op, source_ref, runnable: list, skipped: list):
    async with p.acquire() as conn, conn.transaction():
        bid = await conn.fetchval(
            """INSERT INTO batches (tenant_id, name, portal, credential_id, source_filename, column_mapping,
                                    approval_threshold, max_concurrency, created_by, status, source_ref)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'running',$10) RETURNING id""",
            tid, name, portal, credential_id, filename, [m.__dict__ for m in mapping], approval_threshold,
            max(1, min(max_concurrency, 10)), op, source_ref)
        for r in rows:
            task_id = await conn.fetchval(
                """INSERT INTO tasks (tenant_id, batch_id, row_number, kind, idempotency_key, payload, raw_row,
                                      validation_errors, status)
                   VALUES ($1,$2,$3,'submit_claim',$4,$5,$6,$7,$8::task_status)
                   ON CONFLICT (tenant_id, idempotency_key) DO NOTHING RETURNING id""",
                tid, bid, r.row_number, r.idempotency_key, r.payload, r.raw, r.errors,
                "queued" if r.valid else "invalid")
            if task_id is None:
                skipped.append({"row_number": r.row_number, "reason": "already imported in an earlier batch",
                                "idempotency_key": r.idempotency_key})
            elif r.valid:
                runnable.append(str(task_id))
        await db.audit(f"operator:{op}", "batch.created", "batch", bid,
                       {"rows": len(rows), "runnable": len(runnable), "skipped": len(skipped)}, tid, conn)
    return bid


@app.get("/api/batches")
async def list_batches(op: str = Depends(operator)):
    rows = await (await db.pool()).fetch(
        """SELECT b.id, b.name, b.status, b.created_at, b.completed_at, b.approval_threshold, b.source_filename,
                  count(t.*) AS total,
                  count(*) FILTER (WHERE t.status = 'succeeded') AS succeeded,
                  count(*) FILTER (WHERE t.status IN ('failed', 'rejected', 'expired')) AS failed,
                  count(*) FILTER (WHERE t.status = 'invalid') AS invalid,
                  count(*) FILTER (WHERE t.status = 'waiting_human') AS waiting,
                  count(*) FILTER (WHERE t.status IN ('queued', 'running')) AS active,
                  coalesce(sum(t.cost_usd), 0) AS cost_usd
           FROM batches b LEFT JOIN tasks t ON t.batch_id = b.id GROUP BY b.id ORDER BY b.created_at DESC""")
    return [_jsonable(r) for r in rows]


@app.get("/api/batches/{batch_id}")
async def get_batch(batch_id: str, op: str = Depends(operator)):
    p = await db.pool()
    b = await p.fetchrow("SELECT * FROM batches WHERE id = $1", batch_id)
    if b is None:
        raise HTTPException(404)
    tasks = await p.fetch(
        """SELECT id, row_number, status, current_stage, idempotency_key, confirmation_no, error, cost_usd, payload,
                  validation_errors, attempts, updated_at FROM tasks WHERE batch_id = $1 ORDER BY row_number""", batch_id)
    return {"batch": _jsonable(b), "tasks": [_jsonable(t) for t in tasks]}


# ------------------------------------------------------------------------------------------- tasks / replay
@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str, op: str = Depends(operator)):
    p = await db.pool()
    t = await p.fetchrow("SELECT * FROM tasks WHERE id = $1", task_id)
    if t is None:
        raise HTTPException(404)
    steps = await p.fetch(
        """SELECT s.*, sb.width, sb.height FROM task_steps s LEFT JOIN screenshots sb ON sb.sha256 = s.screenshot_before
           WHERE task_id = $1 ORDER BY seq""", task_id)
    ivs = await p.fetch("SELECT * FROM interventions WHERE task_id = $1 ORDER BY requested_at", task_id)
    ledger = await p.fetchrow("SELECT * FROM submit_ledger WHERE task_id = $1", task_id)
    live = None
    if t["workflow_id"]:
        try:
            live = await (await temporal()).get_workflow_handle(t["workflow_id"]).query(ClaimTaskWorkflow.state)
        except Exception:
            live = None
    return {"task": _jsonable(t), "steps": [_jsonable(s) for s in steps],
            "interventions": [_jsonable(i) for i in ivs], "ledger": _jsonable(ledger) if ledger else None, "live": live}


@app.get("/api/tasks/{task_id}/events")
async def task_events(task_id: str, after: int = 0, op: str = Depends(operator)):
    rows = await (await db.pool()).fetch(
        "SELECT id, type, payload, created_at FROM task_events WHERE task_id = $1 AND id > $2 ORDER BY id LIMIT 500",
        task_id, after)
    return [_jsonable(r) for r in rows]


@app.get("/api/screenshots/{sha}")
async def screenshot(sha: str, op: str = Depends(operator)):
    png = await storage.get_screenshot(sha)
    if png is None:
        raise HTTPException(404)
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=31536000, immutable"})


async def _handle(task_id: str):
    wf = await (await db.pool()).fetchval("SELECT workflow_id FROM tasks WHERE id = $1", task_id)
    if not wf:
        raise HTTPException(409, "task has no running workflow")
    return (await temporal()).get_workflow_handle(wf)


@app.post("/api/tasks/{task_id}/takeover")
async def takeover(task_id: str, op: str = Depends(operator)):
    await (await _handle(task_id)).signal(ClaimTaskWorkflow.request_takeover, op)
    await db.audit(f"operator:{op}", "takeover.requested", "task", task_id)
    return {"ok": True}


class ReleaseIn(BaseModel):
    mode: str = "retry"


@app.post("/api/tasks/{task_id}/release")
async def release(task_id: str, body: ReleaseIn, op: str = Depends(operator)):
    if body.mode not in ("retry", "skip"):
        raise HTTPException(422, "mode must be retry or skip")
    await (await _handle(task_id)).signal(ClaimTaskWorkflow.release, Release(op, body.mode))
    await db.audit(f"operator:{op}", "takeover.released", "task", task_id, {"mode": body.mode})
    return {"ok": True}


class OperatorActionIn(BaseModel):
    kind: str
    x: Optional[int] = None
    y: Optional[int] = None
    text: Optional[str] = None


@app.post("/api/tasks/{task_id}/operator-action")
async def operator_action(task_id: str, a: OperatorActionIn, op: str = Depends(operator)):
    if a.kind not in ("click", "type", "key", "navigate", "refresh"):
        raise HTTPException(422, "bad action kind")
    await (await _handle(task_id)).signal(ClaimTaskWorkflow.operator_action, OperatorAction(a.kind, a.x, a.y, a.text, op))
    return {"ok": True}


# ------------------------------------------------------------------------------------------- interventions
@app.get("/api/interventions")
async def list_interventions(status: str = "open", op: str = Depends(operator)):
    rows = await (await db.pool()).fetch(
        """SELECT i.*, t.row_number, t.batch_id, t.idempotency_key FROM interventions i JOIN tasks t ON t.id = i.task_id
           WHERE i.status = $1::intervention_status ORDER BY i.requested_at""", status)
    return [_jsonable(r) for r in rows]


class ResolveIn(BaseModel):
    approved: Optional[bool] = None
    value: Optional[str] = None
    note: str = ""


@app.post("/api/interventions/{iid}/resolve")
async def resolve(iid: str, body: ResolveIn, op: str = Depends(operator)):
    p = await db.pool()
    iv = await p.fetchrow("SELECT id, task_id, kind, status FROM interventions WHERE id = $1", iid)
    if iv is None:
        raise HTTPException(404)
    if iv["status"] != "open":
        raise HTTPException(409, f"intervention is {iv['status']}")
    h = await _handle(str(iv["task_id"]))
    if iv["kind"] == "approval":
        if body.approved is None:
            raise HTTPException(422, "approved is required")
        await h.signal(ClaimTaskWorkflow.approve, ApprovalDecision(body.approved, op, body.note))
    elif iv["kind"] == "mfa_code":
        if not body.value:
            raise HTTPException(422, "value is required")
        sealed = vault.seal(body.value)
        await p.execute(
            "INSERT INTO intervention_secrets (intervention_id, secret_ct, dek_ct) VALUES ($1,$2,$3) "
            "ON CONFLICT (intervention_id) DO UPDATE SET secret_ct = EXCLUDED.secret_ct, dek_ct = EXCLUDED.dek_ct, consumed_at = NULL",
            iid, sealed.secret_ct, sealed.dek_ct)
        await h.signal(ClaimTaskWorkflow.input_provided, InputProvided(iid, op))
    else:
        raise HTTPException(422, "takeover interventions are resolved by releasing the takeover")
    await db.audit(f"operator:{op}", f"intervention.{iv['kind']}.answered", "intervention", iid,
                   {"approved": body.approved, "note": body.note})
    return {"ok": True}


# ------------------------------------------------------------------------------------------- metrics
@app.get("/api/metrics")
async def metrics(op: str = Depends(operator)):
    p = await db.pool()
    grounders = await p.fetch(
        """SELECT coalesce(grounder, 'none') AS grounder, count(*) AS steps, round(avg(latency_ms)) AS avg_ms,
                  percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms, sum(cost_usd) AS cost_usd
           FROM task_steps WHERE action IN ('click', 'type', 'type_secret', 'select') GROUP BY 1 ORDER BY 2 DESC""")
    escalations = await p.fetchrow(
        """SELECT count(*) FILTER (WHERE grounder_trace @> '[{"tier": "qwen", "verified": false}]'
                                      OR grounder_trace @> '[{"tier": "qwen", "found": false}]') AS qwen_rejected,
                  count(*) FILTER (WHERE grounder = 'claude') AS claude_rescues,
                  count(*) FILTER (WHERE status = 'failed' AND grounder IS NULL) AS unresolved
           FROM task_steps WHERE action IN ('click', 'type', 'type_secret', 'select')""")
    tasks = await p.fetch("SELECT status, count(*) AS n FROM tasks GROUP BY 1")
    ledger = await p.fetchrow(
        """SELECT count(*) AS submitted, coalesce(sum(clicks), 0) AS clicks,
                  count(*) FILTER (WHERE state = 'reconciled_found') AS recovered_unknown_outcomes FROM submit_ledger""")
    return {"grounders": [_jsonable(g) for g in grounders], "escalations": _jsonable(escalations),
            "tasks": {r["status"]: r["n"] for r in tasks}, "ledger": _jsonable(ledger)}


# ------------------------------------------------------------------------------------------- incidents
@app.post("/api/incidents/alertmanager")
async def alertmanager_webhook(payload: dict, op: str = Depends(operator)):
    """Alertmanager webhook receiver (authorization: Bearer <operator token> in its http_config)."""
    return {"changes": await incidents.ingest_alertmanager(payload)}


@app.get("/api/incidents")
async def list_incidents(status: Optional[str] = None, op: str = Depends(operator)):
    p = await db.pool()
    if status == "open":
        rows = await p.fetch("SELECT * FROM incidents WHERE status <> 'resolved' ORDER BY severity, started_at DESC")
    else:
        rows = await p.fetch("SELECT * FROM incidents ORDER BY created_at DESC LIMIT 100")
    return [_jsonable(r) for r in rows]


@app.get("/api/incidents/{iid}")
async def get_incident(iid: str, op: str = Depends(operator)):
    p = await db.pool()
    inc = await p.fetchrow("SELECT * FROM incidents WHERE id = $1", iid)
    if inc is None:
        raise HTTPException(404)
    events = await p.fetch("SELECT * FROM incident_events WHERE incident_id = $1 ORDER BY id", iid)
    return {"incident": _jsonable(inc), "events": [_jsonable(e) for e in events]}


class NoteIn(BaseModel):
    body: str = ""


@app.post("/api/incidents/{iid}/ack")
async def ack_incident(iid: str, op: str = Depends(operator)):
    if not await incidents.acknowledge(iid, op):
        raise HTTPException(409, "incident is not firing")
    return {"ok": True}


@app.post("/api/incidents/{iid}/notes")
async def note_incident(iid: str, body: NoteIn, op: str = Depends(operator)):
    if not body.body.strip():
        raise HTTPException(422, "empty note")
    await incidents.add_note(iid, op, body.body.strip()[:4000])
    return {"ok": True}


@app.post("/api/incidents/{iid}/resolve")
async def resolve_incident(iid: str, body: NoteIn, op: str = Depends(operator)):
    if not await incidents.resolve(iid, op, body.body.strip()[:4000]):
        raise HTTPException(409, "incident is already resolved")
    return {"ok": True}


# ------------------------------------------------------------------------------------------- websocket
@app.websocket("/api/ws")
async def ws(websocket: WebSocket, token: str = "", task_id: Optional[str] = None, batch_id: Optional[str] = None):
    if token != settings().operator_token:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    hub.subs[websocket] = {"task_id": task_id, "batch_id": batch_id}
    try:
        while True:
            await websocket.receive_text()  # keepalive pings from the client
    except WebSocketDisconnect:
        pass
    finally:
        hub.subs.pop(websocket, None)
