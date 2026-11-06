"""Incidents: Alertmanager webhooks become incidents with a timeline that operators acknowledge, annotate and resolve.

Why in the product and not only in a pager tool: the people who answer PortalPilot alerts are the same operators who
answer MFA prompts and approvals, and the fix is usually in the Studio (take over a stuck task, check a claim on the
portal). The incident page links each alert to its runbook and to the tasks it concerns. A client with PagerDuty or
Opsgenie points Alertmanager there as well; this table is then the shared record.

Semantics:
* one open incident per alert fingerprint (partial unique index); a firing alert that is already open adds a
  `refired` event only if it had been acknowledged, so repeated webhooks are not noise
* Alertmanager's `resolved` closes the incident (`auto_resolved`); an operator may resolve it earlier
* every change is also published as a `incident.*` task_event, so it reaches the Studio over WebSocket and the
  event bus through the outbox relay (where the notifier Lambda turns `incident.opened` into a chat message)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import asyncpg

from . import db


def _ts(s: str | None) -> datetime:
    if not s or s.startswith("0001-"):
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


async def ingest_alertmanager(payload: dict[str, Any]) -> list[dict]:
    """Apply one Alertmanager webhook (version 4). Returns what changed, for the caller and for tests."""
    changes: list[dict] = []
    p = await db.pool()
    for a in payload.get("alerts", []):
        labels, ann = a.get("labels", {}), a.get("annotations", {})
        fp = a.get("fingerprint") or labels.get("alertname", "unknown")
        async with p.acquire() as conn, conn.transaction():
            open_row = await conn.fetchrow(
                "SELECT id, status FROM incidents WHERE fingerprint = $1 AND status <> 'resolved' FOR UPDATE", fp)
            if a.get("status") == "resolved":
                if open_row:
                    await _close(conn, open_row["id"], "alertmanager", "auto_resolved", "alert stopped firing",
                                 _ts(a.get("endsAt")))
                    changes.append({"id": str(open_row["id"]), "change": "auto_resolved"})
                continue
            if open_row:
                if open_row["status"] == "acknowledged":
                    await _event(conn, open_row["id"], "refired", "alertmanager", ann.get("summary"))
                    changes.append({"id": str(open_row["id"]), "change": "refired"})
                continue
            severity = labels.get("severity", "ticket")
            iid = await conn.fetchval(
                """INSERT INTO incidents (fingerprint, alertname, severity, summary, runbook_url, labels, started_at)
                   VALUES ($1, $2, $3::incident_severity, $4, $5, $6, $7) RETURNING id""",
                fp, labels.get("alertname", "unknown"), severity if severity in ("page", "ticket") else "ticket",
                ann.get("summary") or labels.get("alertname", "alert"), ann.get("runbook_url"), labels,
                _ts(a.get("startsAt")))
            await _event(conn, iid, "fired", "alertmanager", ann.get("description") or ann.get("summary"))
            changes.append({"id": str(iid), "change": "opened", "alertname": labels.get("alertname"),
                            "severity": severity})
    for c in changes:
        await db.publish(f"incident.{c['change']}", c)
    return changes


async def acknowledge(iid: str, operator: str) -> bool:
    p = await db.pool()
    async with p.acquire() as conn, conn.transaction():
        ok = await conn.fetchval(
            """UPDATE incidents SET status = 'acknowledged', acknowledged_at = now(), acknowledged_by = $2
               WHERE id = $1 AND status = 'firing' RETURNING true""", iid, operator)
        if ok:
            await _event(conn, iid, "acknowledged", f"operator:{operator}", None)
    if ok:
        await db.publish("incident.acknowledged", {"id": iid, "by": operator})
    return bool(ok)


async def add_note(iid: str, operator: str, body: str) -> None:
    async with (await db.pool()).acquire() as conn:
        await _event(conn, iid, "note", f"operator:{operator}", body)
    await db.publish("incident.note", {"id": iid, "by": operator})


async def resolve(iid: str, operator: str, note: str = "") -> bool:
    p = await db.pool()
    async with p.acquire() as conn, conn.transaction():
        ok = await _close(conn, iid, f"operator:{operator}", "resolved", note or None, datetime.now(timezone.utc))
    if ok:
        await db.publish("incident.resolved", {"id": iid, "by": operator})
    return ok


async def _close(conn: asyncpg.Connection, iid: Any, actor: str, kind: str, body: str | None, at: datetime) -> bool:
    ok = await conn.fetchval(
        "UPDATE incidents SET status = 'resolved', resolved_at = $2 WHERE id = $1 AND status <> 'resolved' RETURNING true",
        iid, at)
    if ok:
        await _event(conn, iid, kind, actor, body)
    return bool(ok)


async def _event(conn: asyncpg.Connection, iid: Any, kind: str, actor: str, body: str | None) -> None:
    await conn.execute("INSERT INTO incident_events (incident_id, kind, actor, body) VALUES ($1, $2, $3, $4)",
                       iid, kind, actor, body)
