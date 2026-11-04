"""End-to-end: real API, real Temporal, real browsers, hostile portal.

Plays the human operator (reads the "SMS" code from the portal's admin endpoint, approves large claims) and then
checks the portal's own records, not the agent's: every valid row filed exactly once, nothing filed twice.

    python e2e/run_e2e.py [--api http://localhost:8000] [--portal http://localhost:8080] [--chaos]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--portal", default="http://localhost:8080")
    ap.add_argument("--token", default="dev-operator-token")
    ap.add_argument("--portal-admin", default="portal-admin")
    ap.add_argument("--chaos", action="store_true", help="pop-ups, 503s, MFA and one slow (committed-then-hung) submit")
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args()

    api = httpx.Client(base_url=a.api, headers={"Authorization": f"Bearer {a.token}", "X-Operator": "e2e-bot"}, timeout=60)
    portal = httpx.Client(base_url=a.portal, headers={"X-Admin-Token": a.portal_admin}, timeout=30)

    portal.post("/admin/reset").raise_for_status()
    chaos = {"popup_rate": 0.0, "flaky_rate": 0.0, "mfa": False, "slow_submit_next": 0, "seed": 7}
    if a.chaos:
        chaos.update(popup_rate=0.3, flaky_rate=0.1, mfa=True, slow_submit_next=1, slow_submit_seconds=90)
    portal.post("/admin/chaos", json=chaos).raise_for_status()
    print("portal chaos:", chaos)

    cred = api.post("/api/credentials", json={"label": "acme-e2e", "username": "demo", "password": "demo-pass"}).json()["id"]
    csv = (HERE / "claims_messy.csv").read_bytes()
    pv = api.post("/api/batches/preview", files={"file": ("claims_messy.csv", csv)}).json()
    print(f"preview: {pv['summary']['valid']}/{pv['summary']['total']} rows valid")
    expected_keys = {r["idempotency_key"] for r in pv["rows"] if r["valid"]}

    created = api.post("/api/batches", files={"file": ("claims_messy.csv", csv)},
                       data={"name": f"e2e-{int(time.time())}", "credential_id": cred, "approval_threshold": "5000",
                             "max_concurrency": "3"})
    if created.status_code >= 400 and "already" not in created.text:
        print(created.text)
        created.raise_for_status()
    b = created.json()
    print(f"batch {b['id']}: runnable={b['runnable']} invalid={b['invalid']} skipped={len(b['skipped'])}")
    if b["skipped"]:
        print("rows already imported by an earlier run; reset the database (docker compose down -v) for a clean e2e")
        return 2

    deadline = time.time() + a.timeout
    handled: set[str] = set()
    while time.time() < deadline:
        for iv in api.get("/api/interventions").json():
            if iv["id"] in handled:
                continue
            if iv["kind"] == "mfa_code":
                code = portal.get("/admin/mfa-code", params={"username": "demo"}).json()["code"]
                r = api.post(f"/api/interventions/{iv['id']}/resolve", json={"value": code})
                print(f"  operator: MFA code for row {iv['row_number']} -> {r.status_code}")
            elif iv["kind"] == "approval":
                r = api.post(f"/api/interventions/{iv['id']}/resolve", json={"approved": True, "note": "e2e"})
                print(f"  operator: approved row {iv['row_number']} ({iv['prompt']}) -> {r.status_code}")
            else:
                print(f"  !! unexpected intervention {iv['kind']} on row {iv['row_number']}: {iv['prompt']}")
            handled.add(iv["id"])
        batch = api.get(f"/api/batches/{b['id']}").json()
        st = batch["batch"]["status"]
        counts: dict[str, int] = {}
        for t in batch["tasks"]:
            counts[t["status"]] = counts.get(t["status"], 0) + 1
        print(f"  {time.strftime('%H:%M:%S')} batch={st} {counts}")
        if st.startswith("completed"):
            break
        time.sleep(3)
    else:
        print("TIMEOUT")
        return 1

    # ---------------------------------------------------------------- verdict from the portal's own records
    claims = portal.get("/admin/claims").json()
    refs = [c["reference"] for c in claims["claims"]]
    metrics = api.get("/api/metrics").json()
    tasks = batch["tasks"]
    ok_tasks = [t for t in tasks if t["status"] == "succeeded"]
    checks = {
        "every valid row succeeded": len(ok_tasks) == len(expected_keys),
        "portal holds each valid row exactly once": sorted(refs) == sorted(expected_keys),
        "no duplicate submissions on the portal": len(refs) == len(set(refs)) and claims["submit_count"] == len(refs),
        "invalid rows were not attempted": all(t["status"] == "invalid" for t in tasks if t["idempotency_key"] not in expected_keys),
        "claim numbers recorded": all(t["confirmation_no"] for t in ok_tasks),
    }
    if a.chaos:
        checks["hung submit recovered by reconcile, not a re-click"] = metrics["ledger"]["recovered_unknown_outcomes"] >= 1
        checks["submit clicks == claims"] = metrics["ledger"]["clicks"] == len(refs)

    # idempotency across uploads: the same file again must start nothing
    again = api.post("/api/batches", files={"file": ("claims_messy.csv", csv)},
                     data={"name": "e2e-reupload", "credential_id": cred, "max_concurrency": "3"}).json()
    checks["re-upload of the same file starts nothing"] = again["runnable"] == 0

    print("\nportal claims:", [(c["claim_no"], c["reference"], c["amount"]) for c in claims["claims"]])
    print("grounders:", [(g["grounder"], g["steps"]) for g in metrics["grounders"]])
    print("ledger:", metrics["ledger"])
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
