"""Outbox relay, event mapping and metrics. No database: a small fake connection stands in for asyncpg."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest
from prometheus_client import REGISTRY

from portalpilot.events import relay
from portalpilot.grounding.base import Candidate, Observation, Target, Verification
from portalpilot.grounding.router import GroundingRouter
from portalpilot.observability import BusinessCollector

NOW = datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc)


def ev(id_, type_, payload=None, task="t1"):
    return {"id": id_, "task_id": task, "batch_id": "b1", "type": type_, "payload": payload or {}, "created_at": NOW}


# ------------------------------------------------------------------------------------------- event mapping
def test_internal_chatter_is_not_published():
    for t in ("step.started", "step.finished", "stage.started", "session.opened", "intervention.closed"):
        assert relay.to_domain(ev(1, t)) is None
    assert relay.to_domain(ev(1, "task.updated", {"status": "running"})) is None
    assert relay.to_domain(ev(1, "task.updated", {"status": "waiting_human", "final": True})) is None


def test_final_task_updates_become_typed_events():
    e = relay.to_domain(ev(5, "task.updated", {"task_id": "t1", "status": "succeeded", "final": True,
                                               "confirmation_no": "CLM-1", "stage": "submit"}))
    assert e.type == "task.succeeded" and e.data == {"confirmation_no": "CLM-1"}
    d = e.detail()
    assert d["event_id"] == 5 and d["tenant"] == "demo" and d["occurred_at"].startswith("2026-11-06")


def test_intervention_prompt_is_truncated():
    e = relay.to_domain(ev(2, "intervention.opened", {"kind": "approval", "prompt": "x" * 900}))
    assert len(e.data["prompt"]) == 200 and e.data["kind"] == "approval"


# ------------------------------------------------------------------------------------------- relay loop
class FakeConn:
    def __init__(self, rows):
        self.rows, self.last_id = rows, 0

    async def execute(self, sql, *a):
        if sql.startswith("UPDATE outbox_cursor"):
            self.last_id = a[1]

    async def fetchval(self, sql, *a):
        return self.last_id

    async def fetch(self, sql, last):
        return [r for r in self.rows if r["id"] > last][:relay.BATCH]


class RecordingSink:
    def __init__(self, accept=None):
        self.got, self.accept = [], accept

    def publish(self, events):
        n = len(events) if self.accept is None else min(self.accept, len(events))
        self.got.extend(events[:n])
        return n


ROWS = [ev(1, "step.started"), ev(2, "intervention.opened", {"kind": "mfa_code"}), ev(3, "step.finished"),
        ev(4, "task.updated", {"status": "succeeded", "final": True}), ev(5, "stage.started")]


async def test_relay_publishes_in_order_and_advances_past_filtered_rows():
    conn, sink = FakeConn(ROWS), RecordingSink()
    assert await relay.relay_once(sink, conn) == 5
    assert [e.event_id for e in sink.got] == [2, 4] and conn.last_id == 5
    assert await relay.relay_once(sink, conn) == 0           # nothing new: nothing republished


async def test_relay_stops_at_first_rejected_event_and_resumes_there():
    conn = FakeConn(ROWS)
    assert await relay.relay_once(RecordingSink(accept=1), conn) == 3   # event 2 in; event 4 rejected
    assert conn.last_id == 3
    sink = RecordingSink()
    await relay.relay_once(sink, conn)
    assert [e.event_id for e in sink.got] == [4] and conn.last_id == 5


def test_eventbridge_sink_chunks_by_ten_and_honours_partial_failure():
    class Client:
        def __init__(self):
            self.calls = []

        def put_events(self, Entries):
            self.calls.append(Entries)
            if len(self.calls) == 2:  # second chunk: 3rd entry throttled
                return {"Entries": [{}, {}, {"ErrorCode": "ThrottlingException"}] + [{}] * (len(Entries) - 3)}
            return {"Entries": [{} for _ in Entries]}
    c = Client()
    sink = relay.EventBridgeSink("pp-demo", client=c)
    events = [relay.to_domain(ev(i, "batch.started", {"runnable": 1})) for i in range(1, 24)]
    assert sink.publish(events) == 12
    assert [len(x) for x in c.calls] == [10, 10]
    first = c.calls[0][0]
    assert first["Source"] == "portalpilot" and first["DetailType"] == "batch.started"
    assert first["EventBusName"] == "pp-demo" and json.loads(first["Detail"])["event_id"] == 1


# ------------------------------------------------------------------------------------------- metrics
def sample(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_router_counts_attempts_by_verdict():
    target = Target("Policy number", "input")
    obs = Observation(b"", 1280, 800, "h", "about:blank")

    class T:
        def __init__(self, pt):
            self.pt = pt

        async def locate(self, o, t, page):
            return Candidate(*self.pt)

    async def verifier(page, x, y, t):
        return Verification((x, y) == (5, 5), "ok" if (x, y) == (5, 5) else "label cell")
    before = (sample("portalpilot_grounding_attempts_total", tier="qwen", result="rejected"),
              sample("portalpilot_grounding_attempts_total", tier="dom", result="verified"))
    r = GroundingRouter(chain=("qwen", "dom"), tiers={"qwen": T((1, 1)), "dom": T((5, 5))}, cache=None, verifier=verifier)
    await r.ground(None, obs, target)
    assert sample("portalpilot_grounding_attempts_total", tier="qwen", result="rejected") == before[0] + 1
    assert sample("portalpilot_grounding_attempts_total", tier="dom", result="verified") == before[1] + 1


class FakePool:
    def __init__(self, fail=False):
        self.fail = fail

    async def fetch(self, sql):
        if self.fail:
            raise ConnectionError("db down")
        if "FROM tasks" in sql:
            return [{"k": "running", "v": 3}, {"k": "succeeded", "v": 9}]
        return [{"k": "mfa_code", "v": 1, "age": 420.0}]

    async def fetchrow(self, sql):
        return {"submitted": 9, "clicks": 10, "repeat_click_rows": 1, "stuck_in_flight": 0}

    async def fetchval(self, sql):
        return 12 if "outbox" in sql else 33.0


def collect(pool):
    async def getter():
        return pool
    loop = asyncio.new_event_loop()
    try:
        return {m.name: m for m in BusinessCollector(loop, getter).collect()}
    finally:
        loop.close()


def test_business_gauges_expose_the_invariants():
    m = collect(FakePool())
    assert m["portalpilot_business_scrape_ok"].samples[0].value == 1
    assert {s.labels["status"]: s.value for s in m["portalpilot_tasks"].samples} == {"running": 3, "succeeded": 9}
    assert m["portalpilot_double_submit_suspects"].samples[0].value == 1
    assert m["portalpilot_intervention_oldest_open_seconds"].samples[0].value == 420.0
    assert m["portalpilot_outbox_lag_events"].samples[0].value == 12


def test_business_scrape_failure_is_a_metric_not_an_exception():
    m = collect(FakePool(fail=True))
    assert list(m) == ["portalpilot_business_scrape_ok"] and m["portalpilot_business_scrape_ok"].samples[0].value == 0


def test_alerting_counters_exist_at_zero_before_first_event():
    # increase() cannot see the first increments of a series that did not exist at the previous scrape
    for kind in ("unexpected_page", "grounding_failure"):
        assert REGISTRY.get_sample_value("portalpilot_interventions_opened_total", {"kind": kind}) is not None
