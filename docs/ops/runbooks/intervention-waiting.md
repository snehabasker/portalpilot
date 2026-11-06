# InterventionWaitingTooLong (ticket)

**Signal:** the oldest open intervention of some kind has waited more than 15 minutes for an operator.

**Impact:** that claim is not progressing. For `mfa_code`, the code the portal sent has probably expired, and the
login stage will ask again.

## Steps

1. Studio → **Needs attention**, oldest first.
2. By kind:
   * `approval`: find an approver. If none is available today, leave it: the deadline is 72 h, and the alert
     repeats every 4 h as a reminder, not a failure.
   * `mfa_code`: ask the holder of the service phone. If the code has expired, submit any value; the agent re-runs
     login and the portal sends a new one.
   * `unexpected_page` / `grounding_failure`: open the task, look at the screenshot, take over or hand back (see
     [portal-degraded](portal-degraded.md) or [perception-degraded](perception-degraded.md) if there are many).
3. If this fires every day, the staffing or the notification is wrong, not the system. Check that the notifier is
   delivering `intervention.opened` to the right channel (ADR 0009).
