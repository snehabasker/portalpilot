# PortalDegraded / StuckTasksBurst (ticket)

`StuckTasksBurst` (3+ tasks handed to a human in 5 min) is usually first, after about 1–2 min. `PortalDegraded` can stay
silent in a hard outage, once every task has parked and no portal requests are left to measure.

**Signal:** over 30 % of stage attempts in the last 10 minutes failed with a retryable error (503 or maintenance
pages, timeouts, navigation races), with a minimum-traffic guard.

**Impact:** claims slow down. Stage retries back off (2 → 30 s, 3 attempts); after that a task hands itself to a
human as `unexpected_page`. No wrong clicks (the verifier) and no duplicates (ADR 0003).

## Steps

1. **Confirm it is the portal, not us.** Agent-internals dashboard → *Retryable stage errors*. Then open one stuck
   task in the Studio: the last screenshot usually shows the portal's error or maintenance page. If the screenshots
   look normal, see [perception-degraded](perception-degraded.md) instead.
2. **Tell the client's portal contact** (status template in the [on-call guide](../README.md)). Ask whether there is
   planned maintenance.
3. **Reduce the load while it is down.** Scale workers to 0 if the outage will be longer than about 15 minutes:
   stuck tasks otherwise pile up as human interventions that someone must hand back one by one.
4. **When the portal is back:** Studio → Needs attention. For each `unexpected_page` task: **Hand back: retry stage**.
   The agent re-checks the portal before any submit. Scale workers back up if you stopped them.
5. Resolve when the batch is moving again (overview → *Tasks by status*).

**Known gap** (game day 2026-11-06): tasks waiting on a human hold the batch's concurrency slots, so queued rows
don't start even after the portal recovers until stuck tasks are handed back. ADR 0013 proposes the fix. Until then,
step 4 is required, not optional.
