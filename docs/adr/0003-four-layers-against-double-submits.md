# 0003. Four independent layers against double submission

* Status: accepted (recorded retroactively)
* Date: 2026-11-02
* Deciders: Sneha Basker

## Context

A duplicate insurance claim is a payout risk and a compliance finding, and the cost of an error is asymmetric: one
duplicate outweighs many saved minutes. The usual failure is mundane. The submit times out, the claim did save, and
a retry files it again. The legacy portal has no idempotency key in its API, because it has no API. Any single
mechanism (a DB constraint, a workflow id) can be defeated by a bug in the layer above it.

## Decision

Use four layers, each sufficient on its own for the case it covers:

1. `tasks UNIQUE (tenant_id, idempotency_key)`: a row can never become two tasks, even across uploads.
2. Workflow id `task-<id>`: Temporal refuses to start the same task twice.
3. **The portal is the source of truth.** The key is typed into the portal's "Your reference" field, and every
   path to the submit button runs `dedupe_check`, a search of the portal for that key. That includes the paths
   after a crash, a session loss, a takeover, or an unknown outcome.
4. Submit is a **one-shot activity**, and before clicking, the worker must win the SQL transition
   `submit_ledger.state: intent → in_flight`. An unknown outcome leaves `in_flight`, and only a reconcile moves it on.

## Consequences

* The e2e chaos run (a submit that commits and then hangs for 90 s) files 4 claims with 4 clicks, and the hung one is
  recovered by search, not by a second click.
* Discovery has a hard requirement: **the portal must have a search screen that finds a claim by our reference.**
  Without it, layer 3 is gone, and we must say so to the client before building (ENGAGEMENT § 1).
* The ledger records clicks, which made it possible to alert on the invariant itself (`DoubleSubmitSuspected`,
  ADR 0011) rather than on a proxy.
* Every submit costs one extra portal search.

## Alternatives considered

* **Retry the submit with a short timeout.** That is the default behaviour of most RPA tools, and it is how
  duplicates happen.
* **Rely on Temporal's exactly-once workflow semantics.** Activities are at-least-once, and the side effect lives
  in a system we don't control.
* **Ask a human to confirm every unknown outcome.** It is safe, but at about 1 in 50 submits it turns into a manual
  job, and the reconcile search answers the same question automatically.
