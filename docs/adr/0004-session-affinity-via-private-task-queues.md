# 0004. Browser session affinity through per-worker task queues

* Status: accepted (recorded retroactively)
* Date: 2026-11-02
* Deciders: Sneha Basker

## Context

A logged-in browser context (cookies, MFA state, a half-filled form) lives in one worker process. Temporal schedules
activities on any worker polling a queue, so a stage could land on a worker that has never seen the session.
Serializing browser state between workers is unreliable: many portals bind sessions to more than cookies.

## Decision

`open_session` runs on the shared queue. The worker that wins it creates the context and returns the name of its
own **private task queue** (`portalpilot-browser-<worker_id>`), and the workflow sends every later browser activity
for that task to that queue. Private-queue activities use `schedule_to_start = 30 s` and `heartbeat = 45 s`. If the
worker is gone, one of those timeouts fires, the workflow opens a new session elsewhere, and it restarts at `login`,
which leads into `dedupe_check` (ADR 0003).

## Consequences

* Workers stay interchangeable and stateless from the platform's point of view. ECS and Kubernetes can kill them
  freely, and a lost worker costs a re-login, never a duplicate. This was verified by SIGKILLing a worker mid-batch,
  and again in the 2026-11-06 game day.
* A worker's capacity is capped by its private worker's `max_concurrent_activities` (`MAX_BROWSER_SESSIONS`).
* Recovery takes about 30–45 s, the length of the timeouts, so a crash is visible as a pause.
* Private queues are dynamic. Temporal handles that fine, but dashboards should group them by prefix.

## Alternatives considered

* **Sticky execution only.** It applies to workflow tasks, not activities.
* **One workflow per worker that owns its sessions.** That inverts the model and loses per-task durability.
* **A remote browser service (CDP over the network).** It is viable later and decouples browsers from workers, but
  it adds a service to run and a network hop on every action.
