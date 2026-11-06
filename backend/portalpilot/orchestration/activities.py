"""Temporal activities. Two groups:

* ``DbActivities``: bookkeeping in Postgres; run on the shared queue, safe to retry (all writes are idempotent).
* ``BrowserActivities``: everything that touches a browser. ``open_session`` runs on the shared queue; the rest run
  on the private queue of the worker that holds the session.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from playwright.async_api import TimeoutError as PWTimeout
from temporalio import activity
from temporalio.exceptions import ApplicationError

from .. import db, vault
from ..browser.agent import Agent, PortalUnavailable
from ..browser.session import SessionLost, SessionManager
from ..config import settings
from ..grounding import GroundingFailed, GroundingRouter
from ..observability import INTERVENTIONS_OPENED, LEDGER_TRANSITIONS, STAGE_SECONDS, SUBMIT_CLICKS, TASKS_FINISHED
from ..playbooks import claims
from .shared import (InterventionRequest, LedgerOp, OperatorAction, SessionInfo, StageRequest, StageResult,
                     TaskContext, TaskUpdate)

log = logging.getLogger(__name__)

NON_RETRYABLE = {
    SessionLost: "SessionLost",
    claims.BadCredentials: "BadCredentials",
    claims.InvalidData: "InvalidData",
    claims.SubmitOutcomeUnknown: "SubmitOutcomeUnknown",
    GroundingFailed: "GroundingFailed",
}


class DbActivities:
    @activity.defn
    async def load_task(self, task_id: str) -> TaskContext:
        r = await (await db.pool()).fetchrow(
            """SELECT t.id, t.tenant_id, t.batch_id, t.idempotency_key, t.payload, b.credential_id,
                      b.approval_threshold, b.portal
               FROM tasks t JOIN batches b ON b.id = t.batch_id WHERE t.id = $1""", task_id)
        if r is None:
            raise ApplicationError(f"task {task_id} not found", non_retryable=True)
        portal_url = _portal_url(r["portal"])
        return TaskContext(str(r["id"]), str(r["tenant_id"]), str(r["batch_id"]), portal_url,
                           str(r["credential_id"]) if r["credential_id"] else None, r["idempotency_key"],
                           float(r["payload"]["amount"]),
                           float(r["approval_threshold"]) if r["approval_threshold"] is not None else None)

    @activity.defn
    async def update_task(self, u: TaskUpdate) -> None:
        p = await db.pool()
        await p.execute(
            """UPDATE tasks SET status = coalesce($2::task_status, status), current_stage = coalesce($3::text, current_stage),
                      error = CASE WHEN $2::task_status IS NOT NULL OR $4::text IS NOT NULL THEN $4::text ELSE error END,
                      confirmation_no = coalesce($5::text, confirmation_no), attempts = attempts + $6::int,
                      completed_at = CASE WHEN $7::boolean THEN now() ELSE completed_at END
               WHERE id = $1""",
            u.task_id, u.status, u.stage, u.error, u.confirmation_no, u.attempts_inc, u.final)
        if u.final and u.status and activity.info().attempt == 1:
            TASKS_FINISHED.labels(u.status).inc()
        await db.publish("task.updated", {k: v for k, v in u.__dict__.items() if v not in (None, 0, False)},
                         task_id=u.task_id)

    @activity.defn
    async def ledger(self, op: LedgerOp) -> str:
        """Transitions of the submit ledger. Returns the resulting state; raises if the transition is illegal."""
        p = await db.pool()
        if op.op == "intent":
            row = await p.fetchrow(
                """INSERT INTO submit_ledger (task_id, idempotency_key, state) VALUES ($1, $2, 'intent')
                   ON CONFLICT (task_id) DO UPDATE SET state = 'intent', intent_at = now(), resolved_at = NULL
                       WHERE submit_ledger.state IN ('reconciled_absent', 'intent')
                   RETURNING state""", op.task_id, op.idempotency_key)
        elif op.op == "confirmed":
            row = await p.fetchrow(
                """UPDATE submit_ledger SET state = 'confirmed', confirmation_no = $2, resolved_at = now()
                   WHERE task_id = $1 AND state = 'in_flight' RETURNING state""", op.task_id, op.confirmation_no)
        elif op.op in ("reconciled_found", "reconciled_absent"):
            # A reconcile may also run with no ledger row at all (e.g. the claim was submitted by an earlier,
            # since-reset workflow run): record what the portal says.
            row = await p.fetchrow(
                """INSERT INTO submit_ledger (task_id, idempotency_key, state, confirmation_no, resolved_at)
                   VALUES ($1, $2, $3::submit_state, $4, now())
                   ON CONFLICT (task_id) DO UPDATE SET state = EXCLUDED.state,
                       confirmation_no = coalesce(EXCLUDED.confirmation_no, submit_ledger.confirmation_no), resolved_at = now()
                       WHERE submit_ledger.state IN ('in_flight', 'intent')
                   RETURNING state""", op.task_id, op.idempotency_key, op.op, op.confirmation_no)
        else:
            raise ApplicationError(f"unknown ledger op {op.op}", non_retryable=True)
        if row is None:
            cur = await p.fetchval("SELECT state FROM submit_ledger WHERE task_id = $1", op.task_id)
            if cur == op.op or (op.op == "reconciled_found" and cur == "confirmed"):
                return cur  # replayed activity: already applied
            raise ApplicationError(f"illegal ledger transition {cur} -> {op.op}", non_retryable=True, type="LedgerViolation")
        await db.audit("system", f"ledger.{op.op}", "task", op.task_id, {"confirmation_no": op.confirmation_no})
        LEDGER_TRANSITIONS.labels(op.op).inc()
        return row["state"]

    @activity.defn
    async def ledger_state(self, task_id: str) -> str | None:
        return await (await db.pool()).fetchval("SELECT state FROM submit_ledger WHERE task_id = $1", task_id)

    @activity.defn
    async def open_intervention(self, req: InterventionRequest) -> str:
        p = await db.pool()
        # idempotent per (task, kind) while one is open: a retried activity returns the same intervention
        existing = await p.fetchval(
            "SELECT id FROM interventions WHERE task_id = $1 AND kind = $2 AND status = 'open'", req.task_id, req.kind)
        if existing:
            return str(existing)
        expires = datetime.now(timezone.utc) + timedelta(seconds=req.expires_in_s) if req.expires_in_s else None
        iid = await p.fetchval(
            """INSERT INTO interventions (task_id, kind, stage, prompt, screenshot, context, expires_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id""",
            req.task_id, req.kind, req.stage, req.prompt, req.screenshot, req.context, expires)
        INTERVENTIONS_OPENED.labels(req.kind).inc()
        await p.execute("UPDATE tasks SET status = 'waiting_human' WHERE id = $1", req.task_id)
        await db.publish("intervention.opened", {"intervention_id": str(iid), "kind": req.kind, "prompt": req.prompt,
                                                 "stage": req.stage}, task_id=req.task_id)
        return str(iid)

    @activity.defn
    async def close_intervention(self, intervention_id: str, status: str, by: str, resolution: dict) -> None:
        p = await db.pool()
        task_id = await p.fetchval(
            """UPDATE interventions SET status = $2::intervention_status, resolved_at = coalesce(resolved_at, now()),
                      resolved_by = coalesce(resolved_by, $3), resolution = coalesce(resolution, $4)
               WHERE id = $1 RETURNING task_id""", intervention_id, status, by, resolution)
        await p.execute(
            """UPDATE tasks SET status = 'running' WHERE id = $1 AND status = 'waiting_human'
               AND NOT EXISTS (SELECT 1 FROM interventions WHERE task_id = $1 AND status = 'open')""", task_id)
        await db.publish("intervention.closed", {"intervention_id": intervention_id, "status": status, "by": by},
                         task_id=task_id)

    @activity.defn
    async def finalize_batch(self, batch_id: str) -> str:
        p = await db.pool()
        status = await p.fetchval(
            """UPDATE batches SET completed_at = now(), status = CASE
                   WHEN EXISTS (SELECT 1 FROM tasks WHERE batch_id = $1 AND status NOT IN ('succeeded', 'invalid'))
                     OR EXISTS (SELECT 1 FROM tasks WHERE batch_id = $1 AND status = 'invalid')
                   THEN 'completed_with_errors'::batch_status ELSE 'completed'::batch_status END
               WHERE id = $1 RETURNING status""", batch_id)
        await db.publish("batch.finished", {"status": status}, batch_id=batch_id)
        return status


def _portal_url(portal: str) -> str:
    import os
    return os.environ.get(f"PORTAL_URL_{portal.upper().replace('-', '_')}", os.environ.get("PORTAL_URL", "http://portal:8080"))


class BrowserActivities:
    def __init__(self, sessions: SessionManager, router: GroundingRouter, private_queue: str):
        self.sessions, self.router, self.private_queue = sessions, router, private_queue

    @activity.defn
    async def open_session(self, task_id: str) -> SessionInfo:
        sid = await self.sessions.open(task_id)
        # steps that were mid-flight on a worker that died will never finish: say so in the replay
        await (await db.pool()).execute(
            "UPDATE task_steps SET status = 'failed', error = 'interrupted: worker or browser lost', finished_at = now() "
            "WHERE task_id = $1 AND status = 'started'", task_id)
        await db.publish("session.opened", {"worker": settings().worker_id}, task_id=task_id)
        return SessionInfo(sid, self.private_queue, settings().worker_id)

    @activity.defn
    async def close_session(self, session_id: str) -> None:
        await self.sessions.close(session_id)

    @activity.defn
    async def run_stage(self, req: StageRequest) -> StageResult:
        t0, outcome = time.perf_counter(), "fatal_error"
        try:
            res = await self._run_stage(req)
            outcome = "needs_human" if res.outcome == "needs_input" else "ok"
            return res
        except ApplicationError:  # typed, non-retryable: the workflow decides what happens next
            raise
        except Exception:
            outcome = "retryable_error"
            raise
        finally:
            STAGE_SECONDS.labels(req.stage, outcome).observe(time.perf_counter() - t0)

    async def _run_stage(self, req: StageRequest) -> StageResult:
        attempt = activity.info().attempt
        try:
            page = self.sessions.page(req.session_id)
            agent = Agent(page, req.task_id, req.stage, f"{req.run_key}/a{attempt}", self.router,
                          heartbeat=activity.heartbeat)
            ctx = claims.StageContext(req.ctx.portal_url, await _payload(req.task_id), req.ctx.idempotency_key)
            if req.stage == "login":
                await _load_credentials(ctx, req)
            if req.stage == "submit":
                await _arm_submit(req.task_id)
            await db.publish("stage.started", {"stage": req.stage, "attempt": attempt}, task_id=req.task_id)
            out = await claims.RUNNERS[req.stage](agent, ctx)
            res = StageResult(outcome=out["outcome"], kind=out.get("kind"), prompt=out.get("prompt"),
                              screenshot=out.get("screenshot"), confirmation_no=out.get("confirmation_no"),
                              cost_usd=agent.cost_usd, note=out.get("note"))
            await db.publish("stage.finished", {"stage": req.stage, "outcome": res.outcome}, task_id=req.task_id)
            return res
        except tuple(NON_RETRYABLE) as e:
            kind = next(v for k, v in NON_RETRYABLE.items() if isinstance(e, k))
            await db.publish("stage.failed", {"stage": req.stage, "error": str(e)[:500], "type": kind}, task_id=req.task_id)
            raise ApplicationError(str(e)[:1000], type=kind, non_retryable=True) from e
        except (PortalUnavailable, claims.UnexpectedPage, PWTimeout) as e:
            await db.publish("stage.failed", {"stage": req.stage, "error": str(e)[:500], "retrying": True},
                             task_id=req.task_id)
            raise

    @activity.defn
    async def operator_action(self, task_id: str, session_id: str, action: OperatorAction) -> str | None:
        page = self.sessions.page(session_id)
        agent = Agent(page, task_id, "takeover", f"takeover/{activity.info().activity_id}", self.router,
                      actor=f"operator:{action.operator}")
        if action.kind == "refresh":
            return (await agent.observe()).screenshot_sha
        await agent.operator(action.kind, action.x, action.y, action.text)
        await db.audit(f"operator:{action.operator}", f"takeover.{action.kind}", "task", task_id,
                       {"x": action.x, "y": action.y, "typed_chars": len(action.text or "") if action.kind == "type" else None})
        return (await agent.observe()).screenshot_sha


async def _payload(task_id: str) -> dict:
    return await (await db.pool()).fetchval("SELECT payload FROM tasks WHERE id = $1", task_id)


async def _load_credentials(ctx: claims.StageContext, req: StageRequest) -> None:
    """Decrypt just in time, inside the activity. Secrets never enter workflow history or logs."""
    p = await db.pool()
    if req.ctx.credential_id:
        c = await p.fetchrow("SELECT username, secret_ct, dek_ct FROM credentials WHERE id = $1 AND revoked_at IS NULL",
                             req.ctx.credential_id)
        if c is None:
            raise claims.BadCredentials("credential missing or revoked")
        ctx.username, ctx.password = c["username"], vault.unseal(c["secret_ct"], c["dek_ct"])
        await p.execute("UPDATE credentials SET last_used_at = now() WHERE id = $1", req.ctx.credential_id)
        await db.audit(f"worker:{settings().worker_id}", "credential.decrypted", "credential", req.ctx.credential_id,
                       {"task_id": req.task_id})
    if req.mfa_intervention_id:
        s = await p.fetchrow(
            "UPDATE intervention_secrets SET consumed_at = now() WHERE intervention_id = $1 AND consumed_at IS NULL "
            "RETURNING secret_ct, dek_ct", req.mfa_intervention_id)
        if s:
            ctx.mfa_code = vault.unseal(s["secret_ct"], s["dek_ct"])
            await db.audit(f"worker:{settings().worker_id}", "mfa_code.consumed", "intervention", req.mfa_intervention_id)


async def _arm_submit(task_id: str) -> None:
    """The last line of defence against a double submit: atomically intent -> in_flight, or refuse to click."""
    p = await db.pool()
    ok = await p.fetchval(
        "UPDATE submit_ledger SET state = 'in_flight', clicks = clicks + 1 WHERE task_id = $1 AND state = 'intent' "
        "RETURNING clicks", task_id)
    if ok is None:
        state = await p.fetchval("SELECT state FROM submit_ledger WHERE task_id = $1", task_id)
        raise ApplicationError(f"submit not armed (ledger state {state}); refusing to click", type="LedgerViolation",
                               non_retryable=True)
    SUBMIT_CLICKS.inc()
    await db.audit(f"worker:{settings().worker_id}", "submit.armed", "task", task_id, {"click_number": ok})
