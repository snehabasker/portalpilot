# ApiErrorBudgetBurnFast (page) / ApiErrorBudgetBurnSlow (ticket)

**Signal:** the share of 5xx API responses is burning the 30-day 99.5 % budget 14.4x too fast (1 h and 5 min windows
both over 7.2 %) or 6x too fast (6 h and 30 min windows over 3 %).

## Steps

1. Overview dashboard → *API request rate by route* and *API p95 latency by route*. Which route is failing?
   (Routes are the templates, e.g. `/api/tasks/{task_id}`.)
2. Logs for that route (see [api-down](api-down.md) for where).
3. By route:
   * `/api/batches` (POST): onboarding of an unusual CSV. Reproduce with the preview endpoint, which
     uses the same parser, and fix forward. The intake Lambda retries 5xx and dead-letters after 3 tries.
   * `/api/tasks/{task_id}`: usually the live Temporal query timing out. Check Temporal health.
   * `/api/screenshots/{sha}`: object storage (S3 or s3mock) unreachable or credentials expired.
   * `/api/incidents/alertmanager`: our own alert path is failing, and alerts are not reaching the Studio.
     Alertmanager's UI still shows them: http://localhost:9093 (compose).
4. If a recent deploy is the cause, roll back first and diagnose after.
