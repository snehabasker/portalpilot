# 0002. Temporal for durable, long-running, human-in-the-loop work

* Status: accepted (recorded retroactively)
* Date: 2026-11-02
* Deciders: Sneha Basker

## Context

One claim is a multi-minute sequence of browser stages against a fragile portal. Along the way the task may wait
**hours or days** for a human (an approval has a 72 h deadline, MFA 24 h), survive worker crashes and deploys, and
must never repeat the submit step blindly. We needed: durable state per task, retries with backoff for each stage,
typed failure handling, timers measured in days, signals from the operator UI, and a queryable view of where every
task is.

## Decision

Use **Temporal**. Run one `ClaimTaskWorkflow` per CSV row, as a child of a `BatchWorkflow` that caps concurrency.
Each stage is an activity with its own retry policy, and the submit stage is a one-shot activity. Humans interact
through signals plus `wait_condition` with deadlines. Postgres stays the system of record for what people see;
Temporal owns execution state.

## Consequences

* Crash recovery, timers and retries come from the runtime instead of our code. The 12 workflow tests run in
  Temporal's time-skipping server, so a 72-hour approval timeout is tested in milliseconds.
* A waiting task costs nothing: no worker slot, no browser.
* Workflow code must be deterministic. All I/O goes in activities, and the team has to learn that rule.
* One more stateful service to run. The demo self-hosts it on ECS against the same RDS instance; production should
  use Temporal Cloud or a properly operated cluster (`temporal_address`).
* Secrets must be kept out of workflow history on purpose. That is done by reference (see ARCHITECTURE § 3).

## Alternatives considered

* **SQS + Lambda / Step Functions.** Step Functions handles waits and retries, but it is awkward for a long-lived
  browser session pinned to one machine, its signals (task tokens) are clumsy for a takeover protocol, and it
  ties the product to AWS while some clients run elsewhere (ADR 0010).
* **Celery / RQ with a state table.** That means hand-writing everything Temporal gives us (timers, durable retries,
  resumption after a crash, signals). This is where double-submit bugs usually come from.
* **A cron job polling a `tasks` table.** Simple at first, but multi-day human waits and per-stage retries turn it
  into a homemade workflow engine.
