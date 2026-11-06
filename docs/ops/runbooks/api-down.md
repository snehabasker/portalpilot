# ApiDown (page)

**Signal:** the API has not answered a Prometheus scrape for 2 minutes.

**Impact:** operators cannot use the Studio (no approvals, MFA or takeovers), new batches cannot start, and the
Postgres-derived correctness alerts go blind. **Workers keep filing claims** that need no human, because they don't
depend on the API.

## Steps

1. `curl -sf <api>/api/healthz`. That endpoint runs `SELECT 1`, so a failure here usually means Postgres.
2. Logs: compose `docker compose logs api --tail 200` · ECS `/portalpilot/<client>/api` · k8s `kubectl logs`.
3. Usual causes:
   * **Postgres unreachable or out of connections**: check RDS/Postgres health and connection count. Each API
     replica uses up to 10 pool connections plus 1 LISTEN connection.
   * **Startup migration blocked**: `SELECT * FROM pg_locks WHERE locktype = 'advisory'`. A replica holding lock
     7331 for minutes is stuck mid-migration; its logs show which file.
   * **Deploy rolled out a broken image**: ECS's circuit breaker rolls back by itself; on k8s,
     `helm rollback <rel>`.
4. If approvals or MFA prompts are waiting, tell operators the Studio is down. MFA codes expire, and those tasks
   will need a new code afterwards.
