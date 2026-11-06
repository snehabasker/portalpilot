# SubmitOutcomeUnreconciled (page)

**Signal:** `portalpilot_ledger_stuck_in_flight > 0` for 5 min: a ledger row has been `in_flight` for more than
15 minutes. The agent pressed submit, and nothing has yet established whether the claim exists on the portal.

**Normally** a submit that times out goes straight to `dedupe_check`, which settles it within a minute. A row stuck
`in_flight` means that reconcile is not running: the task is parked, its worker died, or the portal search itself
is failing.

## Steps

1. Find the tasks:
   ```sql
   SELECT t.id, t.status, t.current_stage, l.intent_at FROM submit_ledger l JOIN tasks t ON t.id = l.task_id
   WHERE l.state = 'in_flight' AND l.intent_at < now() - interval '15 minutes';
   ```
2. Open each task in the Studio. Its state says why:
   * **waiting_human** (a takeover or `unexpected_page` after the submit): answer it. The agent runs
     `dedupe_check` before anything else on hand-back.
   * **running**, but no new steps: check `NoWorkers` and the worker logs. The workflow reopens a session when a
     worker is available.
   * **failed**: a reconcile was never possible. **Do not resubmit.** Search the portal for the idempotency key by
     hand and record the outcome on the incident: found means the claim exists; not found means the row can be
     re-run from a new batch, and the row-level key keeps it safe.
3. Resolve when no row is `in_flight` for over 15 minutes.
