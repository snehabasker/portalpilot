# OutboxLagging (ticket)

**Signal:** more than 500 `task_events` not yet published to the event bus, for 10 minutes.

**Impact:** chat notifications (approvals, MFA, incidents) and any client callbacks are delayed. Nothing is lost:
the relay resumes from `outbox_cursor` (ADR 0009).

## Steps

1. Is the relay running? compose `docker compose ps relay` · ECS service `relay` · k8s `deploy/<rel>-portalpilot-relay`.
2. Relay logs:
   * `event bus rejected an entry: ThrottlingException`: PutEvents quota. It retries with backoff. If it is
     sustained, raise the account quota.
   * `AccessDenied`: the task role lost `events:PutEvents` on the bus (an IAM change).
   * nothing logged, and `standby`: another replica holds the advisory lock. Find it:
     `SELECT pid, application_name FROM pg_stat_activity WHERE pid IN (SELECT pid FROM pg_locks WHERE objid = 7332);`
3. A large one-off backlog (after a long outage) drains at about 100 events per pass. Watch *Outbox lag* fall.
4. If consumers must not receive very old events, move the cursor forward on purpose and write down what was
   skipped: `UPDATE outbox_cursor SET last_id = <id> WHERE name = 'eventbridge';`. The EventBridge archive can
   replay anything that did reach the bus.
