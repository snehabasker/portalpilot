# 0001. Record architecture decisions

* Status: accepted
* Date: 2026-11-06
* Deciders: Sneha Basker

## Context

PortalPilot is deployed into client environments by forward-deployed engineers. The person extending it at a client
site is often not the person who built it, and the client's architects and risk team will ask *why*: why Temporal,
why not retry the submit, why screenshots are kept for 90 days. The answers existed only in commit messages and in
the architecture document, which describes what the system *is* and not what was rejected.

## Decision

Keep architecture decision records in `docs/adr/`, one decision per file, in the short format in the
[README](README.md): context, decision, consequences, alternatives. A decision is recorded when it is expensive to
reverse, when it is surprising, or when a client is likely to ask about it. ADRs are immutable once accepted. A new
ADR supersedes an old one, and the old one is marked as superseded.

Decisions made before this ADR are recorded retroactively and say so.

## Consequences

* An engagement can start with "read the ADRs" instead of a long walkthrough, and client reviewers get the
  tradeoffs in writing.
* Writing the alternatives down sometimes shows that a decision was weaker than it felt. That is the point.
* It costs about half an hour per significant decision, and needs the discipline to write the ADR *before* merging.

## Alternatives considered

* **Architecture doc only.** It describes the current state well, but it loses the rejected options and their reasons.
* **A wiki.** Separate from the code, unreviewed, and gone when the client's wiki is migrated.
