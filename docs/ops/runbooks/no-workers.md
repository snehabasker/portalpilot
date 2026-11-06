# NoWorkers (page)

**Signal:** no worker target has answered a Prometheus scrape for 3 minutes.

**Impact:** no claim makes progress. **Nothing is lost**: workflows wait in Temporal, and in-flight tasks reopen a
session (and re-check the portal) on the next worker that starts (ADR 0004). Pages because a client batch is
stalled, not because data is at risk.

## Steps

1. **Is the worker running?** compose: `docker compose ps worker` · ECS: service `worker` events tab · k8s:
   `kubectl get pods -l app.kubernetes.io/component=worker`.
2. **Not running:** start it (compose `docker compose start worker`; ECS and k8s restart on their own, so look at
   *why* they don't: image pull errors, OOMKilled (raise `worker_memory`), failing secret injection).
3. **Running but not scraped:** the metrics port (9100) is unreachable, so check the security group or
   NetworkPolicy. Meanwhile confirm in the Temporal UI that the `portalpilot` task queue has pollers. If it does,
   work is flowing and this is a monitoring problem: downgrade to a ticket.
4. **Crash-looping:** `docker compose logs worker --tail 200`. Usual causes: Temporal unreachable at startup
   (it retries for 2 min, then exits), Chromium failing for lack of shared memory (`shm_size`, or `/dev/shm` on k8s).
5. After it recovers, watch *Tasks by status* on the overview dashboard. Running tasks should start producing steps
   within about a minute (the sessions are reopened).

Measured in the 2026-11-06 game day: detection 3 min 31 s after the worker was stopped, and all 8 tasks completed
after a restart. See [the postmortem](../postmortems/2026-11-06-game-day.md).
