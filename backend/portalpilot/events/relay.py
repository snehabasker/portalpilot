"""Outbox relay: publishes PortalPilot's domain events to an event bus (Amazon EventBridge in AWS).

    python -m portalpilot.events.relay

Every state change already writes a row to `task_events` (that table feeds the Studio's live view). The relay reads
that table in id order, keeps the events other systems care about, maps them to a stable public schema and publishes
them, then advances `outbox_cursor`. Nothing in the request path waits on the bus, and a bus outage only delays
delivery: the cursor stays put and the backlog shows up as `portalpilot_outbox_lag_events`.

Guarantees and their limits:
* **At-least-once.** A crash between publishing and advancing the cursor republishes a few events. Every event carries
  `event_id` (the task_events id) and consumers dedupe on it.
* **Single active relay.** Replicas take a Postgres advisory lock; the others stand by and take over if it dies.
* **No skipped events.** bigserial ids are assigned at insert, not commit, so a slow transaction could commit an id
  lower than one already relayed. The relay only reads rows older than `SETTLE_S`, which is far longer than any
  `task_events` insert takes (each is a single autocommitted statement).
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from prometheus_client import Counter, start_http_server

from .. import db
from ..config import settings

log = logging.getLogger("portalpilot.relay")

CURSOR = "eventbridge"
LOCK_KEY = 7332
SETTLE_S = 2
BATCH = 100
FINAL_TASK_STATUSES = {"succeeded", "failed", "rejected", "expired", "invalid"}

RELAYED = Counter("portalpilot_events_relayed_total", "Domain events published to the bus", ["type"])
RELAY_ERRORS = Counter("portalpilot_event_relay_errors_total", "Failed publish attempts")


@dataclass
class DomainEvent:
    event_id: int
    type: str
    occurred_at: str
    task_id: str | None
    batch_id: str | None
    data: dict

    def detail(self) -> dict:
        return {"event_id": self.event_id, "type": self.type, "occurred_at": self.occurred_at,
                "tenant": "demo", "task_id": self.task_id, "batch_id": self.batch_id, "data": self.data}


def to_domain(row: dict) -> DomainEvent | None:
    """Map an internal task_event to the public schema, or None for UI-only chatter (steps, stages, sessions).
    The public types are a contract with consumers: add new ones freely, never change the meaning of one."""
    t, payload = row["type"], row["payload"] or {}
    if t == "task.updated":
        status = payload.get("status")
        if not (payload.get("final") and status in FINAL_TASK_STATUSES):
            return None
        public = f"task.{status}"
        data = {k: payload[k] for k in ("confirmation_no", "error") if payload.get(k)}
    elif t in ("intervention.opened", "batch.started", "batch.finished") or t.startswith("incident."):
        public, data = t, dict(payload)
        if "prompt" in data:
            data["prompt"] = (data["prompt"] or "")[:200]
    else:
        return None
    return DomainEvent(row["id"], public, row["created_at"].isoformat(),
                       str(row["task_id"]) if row["task_id"] else None,
                       str(row["batch_id"]) if row["batch_id"] else None, data)


class Sink(Protocol):
    def publish(self, events: list[DomainEvent]) -> int:
        """Publish in order; return how many leading events were accepted (the cursor advances that far)."""


class LogSink:
    def publish(self, events: list[DomainEvent]) -> int:
        for e in events:
            log.info("event %s", json.dumps(e.detail()))
        return len(events)


class EventBridgeSink:
    """PutEvents takes at most 10 entries per call. Partial failures are reported per entry; the cursor only advances
    past the longest fully accepted prefix, so ordering is preserved for the retry."""

    def __init__(self, bus_name: str, client: Any = None):
        import boto3
        self.bus = bus_name
        self.client = client or boto3.client("events", region_name=settings().aws_region)

    def publish(self, events: list[DomainEvent]) -> int:
        done = 0
        for i in range(0, len(events), 10):
            chunk = events[i:i + 10]
            resp = self.client.put_events(Entries=[{
                "Source": "portalpilot", "DetailType": e.type, "Detail": json.dumps(e.detail()),
                "EventBusName": self.bus} for e in chunk])
            for entry in resp.get("Entries", []):
                if entry.get("ErrorCode"):
                    log.warning("event bus rejected an entry: %s %s", entry.get("ErrorCode"), entry.get("ErrorMessage"))
                    return done
                done += 1
        return done


def make_sink(kind: str | None = None) -> Sink | None:
    kind = kind or settings().event_sink
    if kind == "log":
        return LogSink()
    if kind == "eventbridge":
        return EventBridgeSink(settings().event_bus_name)
    return None


async def relay_once(sink: Sink, conn) -> int:
    """One pass: publish the next batch of settled events and advance the cursor. Returns rows consumed."""
    await conn.execute("INSERT INTO outbox_cursor (name) VALUES ($1) ON CONFLICT DO NOTHING", CURSOR)
    last = await conn.fetchval("SELECT last_id FROM outbox_cursor WHERE name = $1", CURSOR)
    rows = await conn.fetch(
        f"""SELECT e.id, e.task_id, coalesce(e.batch_id, t.batch_id) AS batch_id, e.type, e.payload, e.created_at
            FROM task_events e LEFT JOIN tasks t ON t.id = e.task_id
            WHERE e.id > $1 AND e.created_at < now() - interval '{SETTLE_S} seconds' ORDER BY e.id LIMIT {BATCH}""", last)
    if not rows:
        return 0
    mapped = [(r["id"], to_domain(dict(r))) for r in rows]
    events = [e for _, e in mapped if e]
    accepted = await asyncio.to_thread(sink.publish, events) if events else 0
    for e in events[:accepted]:
        RELAYED.labels(e.type).inc()
    if accepted < len(events):
        RELAY_ERRORS.inc()
        # advance to just before the first unaccepted event; filtered rows before it are consumed too
        new_last = events[accepted].event_id - 1
    else:
        new_last = rows[-1]["id"]
    if new_last > last:
        await conn.execute("UPDATE outbox_cursor SET last_id = $2, updated_at = now() WHERE name = $1", CURSOR, new_last)
    return sum(1 for rid, _ in mapped if rid <= new_last)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = settings()
    sink = make_sink()
    if sink is None:
        log.info("EVENT_SINK=none: relay idle")
        await asyncio.Event().wait()
    start_http_server(s.metrics_port)
    await db.migrate()
    p = await db.pool()
    async with p.acquire() as conn:
        while not await conn.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_KEY):
            await asyncio.sleep(5)  # standby: another replica is relaying
        log.info("relay active: sink=%s bus=%s", s.event_sink, s.event_bus_name)
        backoff = s.event_relay_interval_s
        while True:
            try:
                n = await relay_once(sink, conn)
                backoff = s.event_relay_interval_s
                if n == BATCH:
                    continue  # backlog: keep draining
            except Exception as e:
                RELAY_ERRORS.inc()
                log.warning("relay pass failed: %s", e)
                backoff = min(backoff * 2, 60)
            await asyncio.sleep(backoff)


if __name__ == "__main__":
    asyncio.run(main())
