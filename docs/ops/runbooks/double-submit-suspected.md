# DoubleSubmitSuspected (page)

**Signal:** `portalpilot_double_submit_suspects > 0`, meaning a `submit_ledger` row has `clicks > 1`.

**Why it pages:** the agent pressed the portal's submit button more than once for one claim. That is legitimate
only if a reconcile proved the first click never landed (`reconciled_absent`, then a new intent). Either way, a human
checks the portal, because a duplicate claim is a payout risk.

## 1. Stop new submits (1 minute)

Scale workers to 0 (see [common commands](../README.md#common-commands)). In-flight workflows wait durably.

## 2. Find the rows

```sql
SELECT t.id, t.idempotency_key, l.state, l.clicks, l.confirmation_no, l.intent_at
FROM submit_ledger l JOIN tasks t ON t.id = l.task_id WHERE l.clicks > 1;
```

## 3. Check each on the portal

Search the portal for the `idempotency_key` (it is in the claim's "Your reference" field).

* **One claim:** the earlier click never landed. Note it on the incident with the audit trail
  (`SELECT * FROM audit_log WHERE entity_id = '<task id>' ORDER BY id`): you should see `ledger.reconciled_absent`
  between the two `submit.armed` rows. That is not a duplicate.
* **Two claims:** a real duplicate. Ask the client's claims team to void the later one, and record both claim
  numbers on the incident. Then treat it as a correctness bug: open the task replay, find both submit steps, and
  look for what let the second one through (the ledger must have been reset to `intent` without a reconcile).

## 4. Resume

Scale workers back up once every suspect is explained. The gauge stays non-zero while those ledger rows exist, so
**silence the alert in Alertmanager for those specific rows** (matcher on the incident) instead of letting it
re-page.

**Escalate:** any confirmed duplicate goes to the engineering owner the same day, with a postmortem.
