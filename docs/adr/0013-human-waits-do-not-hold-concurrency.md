# 0013. Tasks waiting on a human release their concurrency slot

* Status: proposed
* Date: 2026-11-06
* Deciders: Sneha Basker
* Origin: [postmortem 2026-11-06, portal outage game day](../ops/postmortems/2026-11-06-game-day.md), action item 1

## Context

`BatchWorkflow` caps a batch at `max_concurrency` running children, to protect a fragile portal and the tenant's
rate limit. A child counts as running for its whole life, *including while it is parked on a human* (approval,
MFA, a takeover after a stuck stage). Parked children use no browser and make no portal requests.

In the game day, a portal outage pushed 3 of 8 tasks into takeover within about a minute. With
`max_concurrency = 3`, those 3 parked tasks held every slot, and the other 4 rows sat `queued` for the whole
incident and until an operator handed the stuck tasks back, although the portal had recovered. On a real Monday
batch of 400 rows, three unanswered approvals would freeze the whole batch.

## Decision (proposed)

The concurrency cap counts tasks that are **using the portal**, not tasks that exist:

* `ClaimTaskWorkflow` signals its parent `parked(task_id)` when it begins a human wait, and `unparked(task_id)` before
  it resumes browser work.
* `BatchWorkflow` keeps `active` (using the portal) and `parked` sets. It starts a new child when
  `len(active) < max_concurrency`. An unparking child waits for a free slot (`wait_condition`) before it continues,
  so the cap holds at every moment.
* A second cap, `max_parked` (default 3 × `max_concurrency`), stops a broken portal from sending the whole batch
  into human queues: past it, the batch pauses and raises one intervention ("portal looks down; N tasks waiting").

## Consequences

* A batch keeps making progress while humans are slow, which is the common case.
* There are more signals and more state in the batch workflow. It needs Temporal tests for park/unpark ordering,
  for the cap under racing unparks, and for `max_parked`.
* Pausing at `max_parked` also turns "portal down" into one decision for a human, not N separate takeovers. That
  was the other pain in the game day: the operator had to hand back each stuck task one by one.

## Alternatives considered

* **Raise `max_concurrency`.** It hides the problem until more tasks park, and it loosens the protection for the
  portal.
* **Fail parked tasks after a short timeout.** It loses the human-in-the-loop design: approvals legitimately take
  hours.
* **Separate pools per stage.** Too fine-grained. The resource being protected is the portal, so the cap belongs on
  portal use.
