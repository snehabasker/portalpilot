# 0009. Event-driven intake and notifications (EventBridge, SQS, Lambda, outbox)

* Status: accepted
* Date: 2026-11-06
* Deciders: Sneha Basker

## Context

Two requests came out of the engagement material (ENGAGEMENT § 1, workshop outputs):

* **Intake without the UI.** The client's claims system already exports a nightly CSV to S3. Asking someone to
  download it and upload it into the Studio is exactly the manual step we are paid to remove.
* **Tell people when they are needed.** Approvals and MFA prompts sat unanswered because nobody watches the Studio
  all day. Operators live in Slack or Teams.

Both are asynchronous and bursty (40 files at once on a Monday), so delivery must be at-least-once, and neither may
create duplicate work or duplicate messages.

## Decision

**Inbound:** S3 `incoming/*.csv` → EventBridge (S3 "Object Created") → **SQS** with a DLQ (3 receives) → **intake
Lambda** → `POST /api/batches` with `source_ref = s3://bucket/key#etag`. `batches.source_ref` is unique per tenant,
so a redelivery returns the existing batch and starts it if the first attempt died before doing so. Permanent
errors (4xx) are written back as `rejected/<key>.error.json` and acknowledged, not retried.

**Outbound:** `task_events` is already written for every state change (ADR 0007), so it doubles as an **outbox**. An
**outbox relay** (one active ECS task, guarded by a Postgres advisory lock) maps the externally relevant events to a
small public schema (`task.succeeded`, `task.failed`, `intervention.opened`, `batch.finished`, `incident.*`) and
publishes them to a custom EventBridge bus, which has a 7-day archive for replay. A rule sends the operator-facing
types to a **notifier Lambda** that dedupes on `event_id` in DynamoDB (conditional put, TTL) and posts to the chat
webhook. Failed deliveries retry for an hour and then go to a DLQ.

Locally the relay uses `EVENT_SINK=log`. The handlers are tested against moto, the relay against fakes.

## Consequences

* Nothing in the request path waits for the bus. A bus outage shows up as `portalpilot_outbox_lag_events` and
  delays notifications. No work is lost.
* Consumers must dedupe on `event_id`: delivery is at-least-once by construction, from both the relay and
  EventBridge.
* Adding a consumer (say, a callback to the client's claims system) is a new rule, with no change to PortalPilot.
* `bigserial` ids are assigned at insert, not at commit, so the relay only reads rows older than 2 s to avoid
  skipping a late-committing id. That is a documented, bounded delay.
* The intake Lambda calls the API through the ALB, so it needs no VPC and no NAT for Secrets Manager. A client
  whose API must stay private puts the function in the VPC and adds an interface endpoint for Secrets Manager.
* Idle cost is close to zero: EventBridge, SQS, Lambda and DynamoDB on-demand, plus one 0.25 vCPU Fargate task.

## Alternatives considered

* **S3 → Lambda directly (S3 notification).** It has no buffer, no per-message DLQ and no partial batch failure, and
  a burst of files hits the API all at once. SQS also lets us cap concurrency at 2 (the portal is the bottleneck).
* **Publish to EventBridge inline from activities.** It couples task progress to the bus being up, and a crash
  between the DB write and the publish loses the event. The outbox does not.
* **Postgres logical replication / Debezium (CDC).** It is the textbook outbox transport, but too much
  infrastructure for this volume. The polling relay is a few hundred lines and is enough well beyond current scale.
* **SNS instead of EventBridge.** It lacks content-based routing on the event type, the archive and replay, and
  schema discovery.
