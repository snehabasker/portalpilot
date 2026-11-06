"""Game day: inject two real failures into the local stack and check that detection, response and recovery work.

    docker compose --profile ops up --build -d
    python e2e/game_day.py                      # ~25 minutes; writes e2e/results/game_day.json

Scenario A, portal outage: the client's portal starts answering most page loads with 503 while a batch runs.
  expected: stage retries back off, stuck tasks hand themselves to a human (no wrong clicks, no lost work),
  PortalDegraded fires and opens an incident in the Studio, the on-call acknowledges it, and after the portal recovers
  the operator hands the tasks back and every claim is filed exactly once.

Scenario B, worker loss: the only browser worker is stopped mid-batch.
  expected: NoWorkers pages, the incident opens, the worker is restarted, in-flight tasks resume on it, the alert
  resolves and closes the incident by itself.

Every step is timestamped, so the postmortem's timeline (docs/ops/postmortems/) is measured, not reconstructed.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

HERE = Path(__file__).parent
T0 = time.time()
TIMELINE: list[dict] = []


def mark(event: str, **extra) -> None:
    t = time.time()
    TIMELINE.append({"t_s": round(t - T0, 1), "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "event": event, **extra})
    print(f"[{time.strftime('%H:%M:%S')} +{t - T0:6.0f}s] {event} {json.dumps(extra) if extra else ''}", flush=True)


def fresh_csv(n: int, seed: int) -> bytes:
    """Rows that have never been imported (new policy numbers), all valid, all under the approval threshold."""
    rng = random.Random(seed)
    perils = ["Rear ended", "Burst pipe", "Stolen", "Fire", "Collision"]  # loss types the onboarding maps
    lines = ["Pol #,Insured Name,DOL,Peril,Amount Claimed,What happened,Tel,E-mail,Source ID"]
    for i in range(n):
        pol = rng.randint(100000, 899999)  # the mock portal treats 9xxxxx as an unknown policy, by design
        lines.append(f"{pol},Game Day {i},{rng.randint(1, 28):02d}/0{rng.randint(1, 9)}/2026,{rng.choice(perils)},"
                     f"{rng.randint(100, 4000)}.00,Game day row {i},07700 9{rng.randint(10000, 99999)},"
                     f"gd{i}@example.com,GD-{seed}-{i}")
    return ("\n".join(lines) + "\n").encode()


class Stack:
    def __init__(self, a):
        h = {"Authorization": f"Bearer {a.token}", "X-Operator": "oncall"}
        self.api = httpx.Client(base_url=a.api, headers=h, timeout=30)
        self.portal = httpx.Client(base_url=a.portal, headers={"X-Admin-Token": "portal-admin"}, timeout=30)
        self.am = httpx.Client(base_url=a.alertmanager, timeout=10)

    def chaos(self, **kw) -> None:
        base = {"popup_rate": 0.0, "flaky_rate": 0.0, "mfa": False, "slow_submit_next": 0}
        self.portal.post("/admin/chaos", json={**base, **kw}).raise_for_status()

    def incident(self, alertname: str, status: str | None = None) -> dict | None:
        # only incidents opened during this run: earlier game days leave resolved ones with the same name
        for inc in self.api.get("/api/incidents").json():
            opened = datetime.fromisoformat(inc["created_at"]).timestamp()
            if inc["alertname"] == alertname and opened >= T0 and (status is None or inc["status"] == status):
                return inc
        return None

    def wait(self, what: str, fn, timeout: float, every: float = 5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            v = fn()
            if v:
                return v
            time.sleep(every)
        raise TimeoutError(what)

    def batch(self, csv: bytes, name: str, cred: str) -> str:
        r = self.api.post("/api/batches", files={"file": (f"{name}.csv", csv)},
                          data={"name": name, "credential_id": cred, "max_concurrency": "3"})
        r.raise_for_status()
        return r.json()["id"]

    def counts(self, bid: str) -> tuple[str, dict]:
        b = self.api.get(f"/api/batches/{bid}").json()
        c: dict[str, int] = {}
        for t in b["tasks"]:
            c[t["status"]] = c.get(t["status"], 0) + 1
        return b["batch"]["status"], c


def scenario_a(s: Stack, cred: str, rows: int) -> dict:
    mark("A: portal starts failing (85% of page loads answer 503)")
    s.chaos(flaky_rate=0.85, seed=11)
    bid = s.batch(fresh_csv(rows, seed=int(T0) % 10000), f"gameday-a-{int(T0)}", cred)
    mark("A: batch started", batch=bid, rows=rows)

    # Either alert counts as detection. StuckTasksBurst is the leading signal; PortalDegraded can stay silent when
    # every task parks early, because then there are no stage attempts left to measure (postmortem 2026-11-06).
    inc = s.wait("outage incident", lambda: s.incident("StuckTasksBurst") or s.incident("PortalDegraded"),
                 timeout=900, every=5)
    mark("A: incident opened in Studio", incident=inc["id"], alert=inc["alertname"], severity=inc["severity"])
    s.api.post(f"/api/incidents/{inc['id']}/ack").raise_for_status()
    mark("A: on-call acknowledged")
    _, c = s.counts(bid)
    stuck = s.api.get("/api/interventions").json()
    s.api.post(f"/api/incidents/{inc['id']}/notes", json={
        "body": f"Portal answering 503 on most loads. {len(stuck)} task(s) handed to a human, task states {c}. "
                "No submits in flight. Following portal-degraded runbook: wait for portal, then hand tasks back."})
    mark("A: diagnosis noted", stuck_tasks=len(stuck), task_states=c)

    time.sleep(60)  # 'contact the client's portal team' - the outage ends
    s.chaos(flaky_rate=0.0)
    mark("A: portal recovered")
    released = 0
    for iv in s.api.get("/api/interventions").json():
        if iv["kind"] in ("unexpected_page", "grounding_failure", "takeover"):
            s.api.post(f"/api/tasks/{iv['task_id']}/release", json={"mode": "retry"}).raise_for_status()
            released += 1
    mark("A: operator handed stuck tasks back (retry)", released=released)

    def done():
        st, c = s.counts(bid)
        # tasks that hit the outage after the release loop can still land in takeover: hand them back too
        for iv in s.api.get("/api/interventions").json():
            if iv["batch_id"] == bid and iv["kind"] in ("unexpected_page", "grounding_failure"):
                s.api.post(f"/api/tasks/{iv['task_id']}/release", json={"mode": "retry"})
        return (st, c) if st.startswith("completed") else None
    st, c = s.wait("batch A completes", done, timeout=900)
    mark("A: batch completed", status=st, task_states=c)

    s.api.post(f"/api/incidents/{inc['id']}/resolve", json={"body": "Portal healthy, all tasks filed."})
    mark("A: on-call resolved the incident")
    return {"batch": bid, "incident": inc["id"], "status": st, "task_states": c}


def scenario_b(s: Stack, cred: str, rows: int) -> dict:
    bid = s.batch(fresh_csv(rows, seed=int(T0) % 10000 + 1), f"gameday-b-{int(T0)}", cred)
    mark("B: batch started", batch=bid, rows=rows)
    s.wait("tasks running", lambda: s.counts(bid)[1].get("running"), timeout=120, every=2)
    subprocess.run(["docker", "compose", "stop", "-t", "0", "worker"], check=True, capture_output=True)
    mark("B: worker stopped (no grace period)")

    inc = s.wait("NoWorkers incident", lambda: s.incident("NoWorkers"), timeout=600, every=10)
    mark("B: incident opened in Studio", incident=inc["id"], severity=inc["severity"])
    s.api.post(f"/api/incidents/{inc['id']}/ack").raise_for_status()
    mark("B: on-call acknowledged")
    subprocess.run(["docker", "compose", "start", "worker"], check=True, capture_output=True)
    mark("B: worker restarted (runbook step 2)")

    st, c = s.wait("batch B completes", lambda: (lambda st_c: st_c if st_c[0].startswith("completed") else None)(
        s.counts(bid)), timeout=900)
    mark("B: batch completed", status=st, task_states=c)
    resolved = s.wait("NoWorkers auto-resolves", lambda: s.incident("NoWorkers", "resolved"), timeout=600, every=10)
    mark("B: alert stopped firing; incident auto-resolved", incident=resolved["id"])
    return {"batch": bid, "incident": inc["id"], "status": st, "task_states": c}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--portal", default="http://localhost:8080")
    ap.add_argument("--alertmanager", default="http://localhost:9093")
    ap.add_argument("--token", default="dev-operator-token")
    ap.add_argument("--rows", type=int, default=8)
    ap.add_argument("--only", choices=["a", "b"])
    a = ap.parse_args()
    s = Stack(a)
    cred = s.api.post("/api/credentials", json={"label": "gameday", "username": "demo", "password": "demo-pass"}).json()["id"]
    s.chaos()
    out: dict = {}
    if a.only in (None, "a"):
        out["portal_outage"] = scenario_a(s, cred, a.rows)
    if a.only in (None, "b"):
        out["worker_loss"] = scenario_b(s, cred, a.rows)

    claims = s.portal.get("/admin/claims").json()
    refs = [c["reference"] for c in claims["claims"]]
    m = s.api.get("/api/metrics").json()
    prom = httpx.get(a.api + "/metrics").text
    suspects = next(float(l.split()[-1]) for l in prom.splitlines() if l.startswith("portalpilot_double_submit_suspects"))
    checks = {
        "no reference filed twice on the portal": len(refs) == len(set(refs)),
        "submit clicks == claims on the portal": m["ledger"]["clicks"] == claims["submit_count"] == len(refs),
        "double-submit suspects gauge is 0": suspects == 0,
        "every game-day task succeeded": all(set(v["task_states"]) <= {"succeeded"} for v in out.values()),
    }
    for k, v in checks.items():
        mark(f"check: {k}", ok=v)
    result = {"started_at": datetime.fromtimestamp(T0, timezone.utc).isoformat(timespec="seconds"),
              "scenarios": out, "checks": checks, "timeline": TIMELINE,
              "incidents": [s.api.get(f"/api/incidents/{v['incident']}").json() for v in out.values()]}
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "game_day.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
