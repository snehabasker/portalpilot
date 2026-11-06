# Architecture decision records

Short records of the decisions that shape PortalPilot: the context at the time, what was decided, what it costs,
and what else was on the table. One decision per file, numbered, never rewritten: a changed mind is a new ADR that
supersedes the old one.

ADRs 0002–0008 were **recorded retroactively** on 2026-11-06 for decisions made while building the first version
(2026-11-02). They describe the reasoning as it was, including the tradeoffs that were accepted.

| # | Decision | Status |
|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | accepted |
| [0002](0002-temporal-for-durable-orchestration.md) | Temporal for durable, long-running, human-in-the-loop work | accepted |
| [0003](0003-four-layers-against-double-submits.md) | Four independent layers against double submission | accepted |
| [0004](0004-session-affinity-via-private-task-queues.md) | Browser session affinity through per-worker task queues | accepted |
| [0005](0005-tiered-verified-grounding.md) | Tiered grounding, with every point verified against the DOM | accepted |
| [0006](0006-playbooks-in-code-models-for-perception.md) | Playbooks in code; models only for perception | accepted |
| [0007](0007-postgres-notify-for-live-updates.md) | Postgres LISTEN/NOTIFY for live updates, no Redis | accepted |
| [0008](0008-ecs-fargate-and-terraform-per-client.md) | ECS Fargate and one Terraform state per client | accepted |
| [0009](0009-event-driven-intake-and-notifications.md) | Event-driven intake and notifications (EventBridge, SQS, Lambda, outbox) | accepted |
| [0010](0010-helm-chart-alongside-terraform.md) | A Helm chart alongside the ECS Terraform | accepted |
| [0011](0011-slos-prometheus-and-incidents-in-product.md) | SLO-based alerting with Prometheus; incidents recorded in the product | accepted |
| [0012](0012-design-tokens-with-contrast-checks.md) | Design tokens as the source of truth, contrast checked in CI | accepted |
| [0013](0013-human-waits-do-not-hold-concurrency.md) | Tasks waiting on a human release their concurrency slot | proposed |

## Template

```markdown
# NNNN. Title in the imperative

* Status: proposed | accepted | superseded by NNNN
* Date: YYYY-MM-DD
* Deciders: …

## Context
What forces are at play: requirements, constraints, what we know and don't.

## Decision
What we will do, stated plainly.

## Consequences
What becomes easier, what becomes harder, what we must now watch.

## Alternatives considered
Each option with the reason it lost.
```
