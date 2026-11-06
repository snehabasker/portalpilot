# 0007. Postgres LISTEN/NOTIFY for live updates, no Redis

* Status: accepted (recorded retroactively)
* Date: 2026-11-02
* Deciders: Sneha Basker

## Context

The Studio shows steps, screenshots and interventions live, across several API replicas. Workers produce the events.

## Decision

Workers insert each event into `task_events` and `pg_notify('task_events', <id>)`. Each API replica holds one
`LISTEN` connection, fetches the row by id (NOTIFY payloads are capped at 8 kB) and fans it out to subscribed
WebSockets.

## Consequences

* There is no extra infrastructure: one less service per client deployment.
* Every event is also persisted, so a reconnecting client can catch up from `task_events`. The same table later
  became the transactional outbox for the event bus (ADR 0009).
* NOTIFY is delivered only to connected listeners. A replica that is disconnected misses events until the client
  reloads. That is acceptable for a UI and not acceptable for integrations, which is why integrations read the
  outbox and not NOTIFY.
* At very high event rates the single LISTEN connection and per-event fetch would need batching.

## Alternatives considered

* **Redis pub/sub.** It is the standard answer, but it adds a stateful service per client for no new capability.
* **Polling.** Simpler, but it is laggy at useful intervals and wasteful at short ones.
