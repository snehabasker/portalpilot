# BusinessMetricsBlind (ticket)

**Signal:** `portalpilot_business_scrape_ok == 0` for 5 minutes. The API's scrape-time collector could not read
Postgres within 3 s.

**Why it matters:** `DoubleSubmitSuspected`, `SubmitOutcomeUnreconciled`, `TasksStalled` and
`InterventionWaitingTooLong` all depend on these gauges. While this fires, **the correctness alerts cannot fire**.

## Steps

1. API logs: `business metrics scrape failed: …` gives the reason.
2. Usual causes: Postgres slow or overloaded (the queries are aggregates over `tasks`, `interventions`,
   `submit_ledger`, so check for long-running locks), or the API pool is exhausted by real traffic.
3. While it is blind, run the invariant check by hand every hour:
   `SELECT count(*) FROM submit_ledger WHERE clicks > 1;` (must be 0, or every row explained).
