# TasksStalled (ticket)

**Signal:** a task in status `running` has not changed in 15 minutes while workers are up.

## Steps

1. Find it: `SELECT id, current_stage, updated_at FROM tasks WHERE status = 'running' ORDER BY updated_at LIMIT 5;`
2. Temporal UI → workflow `task-<id>`. Pending activities tell you:
   * an activity **scheduled but never started** on a private queue (`portalpilot-browser-<worker>`): that worker is
     gone. `schedule_to_start` (30 s) should have fired. If it didn't, check that the worker id in the queue name
     matches no live worker, and terminate the activity attempt so the workflow reopens a session.
   * an activity **started, heartbeating, and slow**: the portal is very slow. See
     [portal-degraded](portal-degraded.md).
   * **no pending activity, workflow waiting on a timer or condition**: the task row status is out of date with the
     workflow. Check the last `task.updated` event; the DB activity may be failing (Postgres).
3. Resolve when it moves, or record why it is expected (for example a 72 h approval that the task status does not
   show).
