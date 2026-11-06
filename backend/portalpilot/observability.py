"""Prometheus metrics.

Two kinds of signal:

* **Process counters and histograms**, incremented where things happen (stage runs, grounding attempts, ledger
  transitions, HTTP requests). Each process (api, worker) exposes its own; Prometheus sums them.
* **System-of-record gauges**, computed from Postgres at scrape time by the API (`BusinessCollector`). These answer
  the questions an on-call engineer asks first ("are claims stuck?", "is anyone waiting on a human?", "did we ever
  click submit twice?") and stay correct across worker restarts, because Postgres is the source of truth.

Label values are kept to small fixed sets (stage names, tier names, states), never ids, so cardinality is bounded.
"""
from __future__ import annotations

import asyncio
import logging

from prometheus_client import Counter, Gauge, Histogram
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

log = logging.getLogger("portalpilot.metrics")

# ------------------------------------------------------------------------------------------- worker
STAGE_SECONDS = Histogram(
    "portalpilot_stage_duration_seconds", "Wall time of one playbook stage attempt",
    ["stage", "outcome"],  # outcome: ok | needs_human | retryable_error | fatal_error
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300))
GROUNDING_ATTEMPTS = Counter(
    "portalpilot_grounding_attempts_total", "Grounding attempts by tier and verdict",
    ["tier", "result"])  # result: verified | rejected | miss | error
GROUNDING_SECONDS = Histogram(
    "portalpilot_grounding_seconds", "Latency of one grounding tier attempt", ["tier"],
    buckets=(0.001, 0.005, 0.02, 0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8))
GROUNDING_FAILED = Counter(
    "portalpilot_grounding_failed_total", "Actions no tier could ground (escalated to a human)")
GROUNDING_COST = Counter("portalpilot_grounding_cost_usd_total", "Model spend on grounding", ["tier"])
LEDGER_TRANSITIONS = Counter(
    "portalpilot_ledger_transitions_total", "Submit ledger transitions", ["to_state"])
SUBMIT_CLICKS = Counter("portalpilot_submit_clicks_total", "Times the agent pressed the portal's submit button")
INTERVENTIONS_OPENED = Counter("portalpilot_interventions_opened_total", "Human interventions requested", ["kind"])
TASKS_FINISHED = Counter("portalpilot_tasks_finished_total", "Tasks reaching a final state", ["status"])
BROWSER_SESSIONS = Gauge("portalpilot_browser_sessions", "Browser contexts held by this worker")

# Labelled counters only exist after their first increment, so a burst that lands between two scrapes appears as a
# brand-new series already at N, and increase()/rate() read that as no change. Create every known label value at 0
# up front (game day 2026-11-06: StuckTasksBurst missed 3 stuck tasks because of this).
for _k in ("approval", "mfa_code", "takeover", "grounding_failure", "unexpected_page"):
    INTERVENTIONS_OPENED.labels(_k)
for _s in ("succeeded", "failed", "rejected", "expired", "invalid", "cancelled"):
    TASKS_FINISHED.labels(_s)
for _s in ("intent", "confirmed", "reconciled_found", "reconciled_absent"):
    LEDGER_TRANSITIONS.labels(_s)

# ------------------------------------------------------------------------------------------- api
HTTP_REQUESTS = Counter("portalpilot_http_requests_total", "API requests", ["method", "route", "code"])
HTTP_SECONDS = Histogram(
    "portalpilot_http_request_seconds", "API request latency", ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))


# ------------------------------------------------------------------------------------------- scrape-time gauges
_BUSINESS_SQL = {
    "tasks": "SELECT status::text AS k, count(*) AS v FROM tasks GROUP BY 1",
    "open_interventions": """SELECT kind::text AS k, count(*) AS v,
                                    coalesce(max(extract(epoch FROM now() - requested_at)), 0) AS age
                             FROM interventions WHERE status = 'open' GROUP BY 1""",
    "ledger": """SELECT count(*) FILTER (WHERE state IN ('confirmed', 'reconciled_found')) AS submitted,
                        coalesce(sum(clicks), 0) AS clicks,
                        count(*) FILTER (WHERE clicks > 1) AS repeat_click_rows,
                        count(*) FILTER (WHERE state = 'in_flight'
                                           AND intent_at < now() - interval '15 minutes') AS stuck_in_flight
                 FROM submit_ledger""",
    "running_oldest": """SELECT coalesce(max(extract(epoch FROM now() - updated_at)), 0)
                         FROM tasks WHERE status = 'running'""",
    "outbox_lag": """SELECT coalesce((SELECT max(id) FROM task_events), 0)
                          - coalesce((SELECT last_id FROM outbox_cursor WHERE name = 'eventbridge'), 0)""",
}


class BusinessCollector(Collector):
    """Reads Postgres on each scrape. Prometheus scrapes from a worker thread while the API's pool lives on the event
    loop, so the queries are submitted to that loop and awaited with a timeout."""

    def __init__(self, loop: asyncio.AbstractEventLoop, pool_getter, timeout_s: float = 3.0):
        self.loop, self.pool_getter, self.timeout_s = loop, pool_getter, timeout_s

    async def _read(self) -> dict:
        p = await self.pool_getter()
        out: dict = {}
        out["tasks"] = await p.fetch(_BUSINESS_SQL["tasks"])
        out["open_interventions"] = await p.fetch(_BUSINESS_SQL["open_interventions"])
        out["ledger"] = await p.fetchrow(_BUSINESS_SQL["ledger"])
        out["running_oldest"] = await p.fetchval(_BUSINESS_SQL["running_oldest"])
        try:
            out["outbox_lag"] = await p.fetchval(_BUSINESS_SQL["outbox_lag"])
        except Exception:  # the outbox table arrives with migration 002; never fail a scrape over it
            out["outbox_lag"] = None
        return out

    def collect(self):
        up = GaugeMetricFamily("portalpilot_business_scrape_ok", "1 if the Postgres-derived gauges were read")
        try:
            if self.loop.is_running():
                data = asyncio.run_coroutine_threadsafe(self._read(), self.loop).result(self.timeout_s)
            else:  # tests / direct calls with no running loop
                data = asyncio.run(self._read())
        except Exception as e:
            log.warning("business metrics scrape failed: %s", e)
            up.add_metric([], 0)
            yield up
            return
        up.add_metric([], 1)
        yield up

        tasks = GaugeMetricFamily("portalpilot_tasks", "Tasks by current status", labels=["status"])
        for r in data["tasks"]:
            tasks.add_metric([r["k"]], r["v"])
        yield tasks

        iv = GaugeMetricFamily("portalpilot_interventions_open", "Open human interventions", labels=["kind"])
        age = GaugeMetricFamily("portalpilot_intervention_oldest_open_seconds",
                                "Age of the oldest open intervention", labels=["kind"])
        for r in data["open_interventions"]:
            iv.add_metric([r["k"]], r["v"])
            age.add_metric([r["k"]], float(r["age"]))
        yield iv
        yield age

        led = data["ledger"]
        yield GaugeMetricFamily("portalpilot_claims_submitted", "Claims the portal holds (confirmed or found by "
                                "reconcile)", value=led["submitted"])
        yield GaugeMetricFamily("portalpilot_submit_clicks_recorded", "Submit clicks recorded in the ledger",
                                value=led["clicks"])
        # The correctness invariant. A ledger row with more than one click is legitimate only if a reconcile proved
        # the first click never landed; every one is checked against the portal by a human, so it pages.
        yield GaugeMetricFamily("portalpilot_double_submit_suspects",
                                "Ledger rows whose submit button was pressed more than once (expected 0)",
                                value=led["repeat_click_rows"])
        yield GaugeMetricFamily("portalpilot_ledger_stuck_in_flight",
                                "Ledger rows in_flight for over 15 minutes (outcome never reconciled)",
                                value=led["stuck_in_flight"])
        yield GaugeMetricFamily("portalpilot_running_task_oldest_update_seconds",
                                "Seconds since the least recently updated running task changed",
                                value=float(data["running_oldest"]))
        if data["outbox_lag"] is not None:
            yield GaugeMetricFamily("portalpilot_outbox_lag_events",
                                    "task_events not yet relayed to the event bus", value=data["outbox_lag"])
