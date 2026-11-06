"""Generates the Grafana dashboards from code, so they are reviewed and diffed like everything else.

    python deploy/observability/grafana/build_dashboards.py      # writes dashboards/*.json

Two dashboards, for two audiences:
* **Service overview**: the on-call view. The invariants first (double submits, unreconciled submits), then SLOs and
  error budgets, then what humans are waiting on.
* **Agent internals**: for the engineer debugging a degraded run: stage outcomes, grounding tiers, latency, spend.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).parent / "dashboards"
DS = {"type": "prometheus", "uid": "prometheus"}

# Status colours match the Studio's design tokens (frontend/src/design-system/tokens.json).
OK, WARN, BAD, ACCENT = "#3fb97b", "#e3a33b", "#ef6461", "#5b9dff"
TIER = {"cache": "#4fc1c9", "qwen": "#b18cff", "claude": "#e8915a", "dom": "#8b95a5"}


class Board:
    def __init__(self, uid: str, title: str, description: str):
        self.uid, self.title, self.description = uid, title, description
        self.panels: list[dict] = []
        self.y = 0
        self.x = 0
        self.row_h = 0

    def _place(self, w: int, h: int) -> dict:
        if self.x + w > 24:
            self.x, self.y, self.row_h = 0, self.y + self.row_h, 0
        pos = {"x": self.x, "y": self.y, "w": w, "h": h}
        self.x += w
        self.row_h = max(self.row_h, h)
        return pos

    def section(self, title: str) -> None:
        if self.x:
            self.x, self.y, self.row_h = 0, self.y + self.row_h, 0
        self.panels.append({"type": "row", "title": title, "collapsed": False,
                            "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1}, "id": len(self.panels) + 1})
        self.y += 1

    def stat(self, title, expr, w=4, h=4, unit="short", steps=None, desc="", decimals=None):
        steps = steps or [{"color": OK, "value": None}]
        p = {"type": "stat", "title": title, "description": desc, "datasource": DS,
             "targets": [{"expr": expr, "refId": "A", "instant": True}],
             "options": {"colorMode": "background", "graphMode": "none", "reduceOptions": {"calcs": ["lastNotNull"]}},
             "fieldConfig": {"defaults": {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps},
                                          **({"decimals": decimals} if decimals is not None else {})},
                             "overrides": []}}
        self._add(p, w, h)

    def series(self, title, targets, w=12, h=8, unit="short", stack=False, desc="", colors=None, kind="timeseries"):
        overrides = [{"matcher": {"id": "byName", "options": k},
                      "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]}
                     for k, c in (colors or {}).items()]
        p = {"type": kind, "title": title, "description": desc, "datasource": DS,
             "targets": [{"expr": e, "legendFormat": lf, "refId": chr(65 + i)} for i, (e, lf) in enumerate(targets)],
             "fieldConfig": {"defaults": {"unit": unit, "custom": {
                 "fillOpacity": 25 if stack else 8, "lineWidth": 2, "showPoints": "never",
                 "stacking": {"mode": "normal" if stack else "none"}}}, "overrides": overrides},
             "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}}}
        self._add(p, w, h)

    def table(self, title, expr, w=12, h=8, desc=""):
        p = {"type": "table", "title": title, "description": desc, "datasource": DS,
             "targets": [{"expr": expr, "refId": "A", "instant": True, "format": "table"}],
             "transformations": [{"id": "organize", "options": {"excludeByName": {"Time": True}}}]}
        self._add(p, w, h)

    def _add(self, p, w, h):
        p["gridPos"] = self._place(w, h)
        p["id"] = len(self.panels) + 1
        self.panels.append(p)

    def json(self) -> dict:
        return {"uid": self.uid, "title": self.title, "description": self.description, "tags": ["portalpilot"],
                "timezone": "browser", "schemaVersion": 41, "version": 1, "refresh": "15s",
                "time": {"from": "now-3h", "to": "now"}, "panels": self.panels,
                "links": [{"title": "Runbooks", "type": "link", "url": "https://github.com/snehabasker/portalpilot/tree/main/docs/ops/runbooks",
                           "targetBlank": True}]}


def overview() -> Board:
    b = Board("pp-overview", "PortalPilot: service overview",
              "On-call view: correctness invariants, SLOs and error budgets, and what humans are waiting on.")
    b.section("Correctness (any non-zero value pages)")
    red_if_any = [{"color": OK, "value": None}, {"color": BAD, "value": 1}]
    b.stat("Double-submit suspects", "portalpilot_double_submit_suspects", steps=red_if_any,
           desc="Ledger rows whose submit button was pressed more than once. Each is checked on the portal.")
    b.stat("Submits unreconciled > 15 min", "portalpilot_ledger_stuck_in_flight", steps=red_if_any)
    b.stat("Claims on the portal", "portalpilot_claims_submitted", steps=[{"color": ACCENT, "value": None}])
    b.stat("Submit clicks recorded", "portalpilot_submit_clicks_recorded", steps=[{"color": ACCENT, "value": None}],
           desc="Equal to claims on the portal when no click was ever repeated.")
    b.stat("Workers up", 'sum(up{job="worker"})', steps=[{"color": BAD, "value": None}, {"color": OK, "value": 1}])
    b.stat("Business metrics readable", "portalpilot_business_scrape_ok",
           steps=[{"color": BAD, "value": None}, {"color": OK, "value": 1}],
           desc="0 means Postgres could not be read and the correctness panels above are stale.")

    b.section("SLOs")
    b.stat("API availability (30 d, objective 99.5 %)",
           '1 - (sum(increase(portalpilot_http_requests_total{code=~"5.."}[30d])) '
           '/ clamp_min(sum(increase(portalpilot_http_requests_total[30d])), 1))',
           w=6, unit="percentunit", decimals=3,
           steps=[{"color": BAD, "value": None}, {"color": WARN, "value": 0.995}, {"color": OK, "value": 0.998}])
    b.stat("API error budget left (30 d)",
           '1 - (sum(increase(portalpilot_http_requests_total{code=~"5.."}[30d])) '
           '/ clamp_min(sum(increase(portalpilot_http_requests_total[30d])), 1)) / (1 - 0.995)',
           w=6, unit="percentunit", decimals=1,
           steps=[{"color": BAD, "value": None}, {"color": WARN, "value": 0.25}, {"color": OK, "value": 0.5}])
    b.stat("Actions grounded (30 d, objective 99 %)",
           '1 - sum(increase(portalpilot_grounding_failed_total[30d])) / clamp_min('
           'sum(increase(portalpilot_grounding_attempts_total{result="verified"}[30d])) '
           '+ sum(increase(portalpilot_grounding_failed_total[30d])), 1)',
           w=6, unit="percentunit", decimals=2,
           steps=[{"color": BAD, "value": None}, {"color": WARN, "value": 0.99}, {"color": OK, "value": 0.995}])
    b.stat("Oldest open intervention", "max(portalpilot_intervention_oldest_open_seconds) or vector(0)", w=6, unit="s",
           steps=[{"color": OK, "value": None}, {"color": WARN, "value": 600}, {"color": BAD, "value": 900}])
    b.series("API error ratio vs fast-burn threshold",
             [("slo:api_errors:ratio_rate5m", "5 m"), ("slo:api_errors:ratio_rate1h", "1 h"),
              ("vector(14.4 * (1 - 0.995))", "page threshold")], unit="percentunit",
             colors={"page threshold": BAD})
    b.series("Grounding failure ratio vs objective",
             [("slo:grounding_failures:ratio_rate5m", "5 m"), ("slo:grounding_failures:ratio_rate1h", "1 h"),
              ("vector(1 - 0.99)", "objective")], unit="percentunit", colors={"objective": WARN})

    b.section("Work")
    b.series("Tasks by status", [("portalpilot_tasks", "{{status}}")], stack=True,
             colors={"succeeded": OK, "running": ACCENT, "queued": "#8b95a5", "waiting_human": WARN,
                     "failed": BAD, "invalid": "#a35a58"})
    b.series("Open interventions by kind", [("portalpilot_interventions_open", "{{kind}}")], stack=True)
    b.series("API request rate by route", [('sum by (route) (rate(portalpilot_http_requests_total[5m]))', "{{route}}")],
             unit="reqps")
    b.series("API p95 latency by route",
             [('histogram_quantile(0.95, sum by (le, route) (rate(portalpilot_http_request_seconds_bucket[5m])))',
               "{{route}}")], unit="s")
    b.series("Outbox lag (events not yet on the bus)", [("portalpilot_outbox_lag_events", "lag")], w=12)
    b.series("Domain events published", [("sum by (type) (rate(portalpilot_events_relayed_total[5m]))", "{{type}}")],
             unit="ops", w=12)
    return b


def internals() -> Board:
    b = Board("pp-agent", "PortalPilot: agent internals",
              "Where time and money go inside a claim: stages, grounding tiers, escalations.")
    b.section("Stages")
    b.series("Stage attempts by outcome", [("sum by (outcome) (portalpilot:stage_attempts:rate10m)", "{{outcome}}")],
             stack=True, unit="ops",
             colors={"ok": OK, "needs_human": WARN, "retryable_error": "#e8915a", "fatal_error": BAD})
    b.series("Retryable stage errors (portal health)", [("portalpilot:stage_retryable_errors:ratio_rate10m", "ratio"),
                                                        ("vector(0.3)", "ticket threshold")],
             unit="percentunit", colors={"ticket threshold": WARN})
    b.series("p95 stage duration",
             [('histogram_quantile(0.95, sum by (le, stage) (rate(portalpilot_stage_duration_seconds_bucket{outcome="ok"}[10m])))',
               "{{stage}}")], unit="s", w=24)

    b.section("Grounding")
    b.series("Verified actions by tier", [('sum by (tier) (rate(portalpilot_grounding_attempts_total{result="verified"}[5m]))',
                                           "{{tier}}")], stack=True, unit="ops", colors=TIER)
    b.series("Rejected by the verifier, by tier",
             [('sum by (tier) (rate(portalpilot_grounding_attempts_total{result="rejected"}[5m]))', "{{tier}}")],
             unit="ops", colors=TIER,
             desc="A rejection is a near-miss caught before clicking; the router escalates to the next tier.")
    b.series("p95 grounding latency by tier",
             [('histogram_quantile(0.95, sum by (le, tier) (rate(portalpilot_grounding_seconds_bucket[10m])))', "{{tier}}")],
             unit="s", colors=TIER)
    b.series("Model spend per hour", [("sum by (tier) (rate(portalpilot_grounding_cost_usd_total[1h])) * 3600", "{{tier}}")],
             unit="currencyUSD", colors=TIER)
    b.series("Browser sessions per worker", [("portalpilot_browser_sessions", "{{instance}}")], w=12)
    b.series("Ledger transitions", [("sum by (to_state) (rate(portalpilot_ledger_transitions_total[10m]))", "{{to_state}}")],
             unit="ops", w=12)
    return b


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for board in (overview(), internals()):
        path = OUT / f"{board.uid}.json"
        path.write_text(json.dumps(board.json(), indent=1) + "\n", encoding="utf-8")
        print(f"wrote {path} ({len(board.panels)} panels)")
