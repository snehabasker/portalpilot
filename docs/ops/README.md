# Operating PortalPilot: on-call and incident response

Who this is for: the client's operators and the FDE on call during a rollout. Tooling: Prometheus rules
([`deploy/observability/prometheus/rules`](../../deploy/observability/prometheus/rules)), Alertmanager, Grafana
dashboards (*service overview* for on-call, *agent internals* for debugging), and the Studio's **Incidents** page,
where every alert becomes an incident with a timeline.

## Severities

| Severity | Meaning | Response | Examples |
|---|---|---|---|
| **page** | Correctness or availability is at risk now | acknowledge within 15 min, any hour | `DoubleSubmitSuspected`, `SubmitOutcomeUnreconciled`, `NoWorkers`, `ApiDown`, fast error-budget burn |
| **ticket** | Degraded, and work is safe but slow or costly | next business hour | `PortalDegraded`, `PerceptionBudgetBurn`, `InterventionWaitingTooLong`, `OutboxLagging` |

A page is never "probably fine". If you can't explain it, escalate.

## SLOs

| SLO | Objective | Why this number |
|---|---|---|
| API availability | 99.5 % / 30 d | Operators work business hours. 3.6 h/month of errors is noticeable but not costly; the work itself is durable. |
| Perception (actions grounded) | 99 % / 30 d | Each miss costs about 1.5 min of a human (ENGAGEMENT § 3). At 25 actions per claim, 1 % ≈ one human touch per 4 claims, which is the budget the ROI model assumes. |
| Human response | 90 % of interventions answered in 15 min (business hours) | MFA codes and portal sessions expire after about 10–20 min. |
| Correctness | no claim filed twice | Not a budget. Any suspect pages. |

## Responding

1. **Acknowledge** in the Studio (Incidents → Acknowledge). It stops repeat notifications and tells others someone
   has it.
2. **Open the runbook** linked on the incident. Check the dashboards it names.
3. **Write as you go**: add a note to the incident for each thing you check or change. The postmortem is written
   from this timeline, and timestamps you write down later are guesses.
4. **Mitigate first, diagnose second.** For PortalPilot, the safe mitigation is nearly always to *stop the
   workers* (`scale worker to 0`): workflows wait durably and nothing is lost or double-submitted.
5. **Resolve** when the alert has cleared *and* any stuck work is moving again. Alertmanager auto-resolves the
   incident if the alert clears first, so add a closing note anyway.
6. **Postmortem** for every page and for any ticket that cost real time: blameless, within 3 business days, using
   the [template](postmortems/TEMPLATE.md). Action items get an owner and a date.

## Status update template (client channel)

> **[PortalPilot] Investigating: <one-line symptom>** (started HH:MM)
> Impact: <what operators/claims are affected; whether any claim is at risk of duplication>
> Doing now: <current step>. Next update by HH:MM.

## Common commands

| | docker compose | ECS (`deploy.sh` stack) | Kubernetes (Helm) |
|---|---|---|---|
| stop all agent work safely | `docker compose stop worker` | `aws ecs update-service --cluster pp-<client> --service worker --desired-count 0` | `kubectl scale deploy/<rel>-portalpilot-worker --replicas 0` |
| restart workers | `docker compose start worker` | `--desired-count <n>` | `--replicas <n>` |
| API logs | `docker compose logs -f api` | CloudWatch `/portalpilot/<client>/api` | `kubectl logs deploy/<rel>-portalpilot-api` |
| who is stuck on what | Studio → Needs attention | same | same |
| a task's full history | Studio → task → Replay; Temporal UI `task-<id>` | same | same |

## Runbooks

[double-submit-suspected](runbooks/double-submit-suspected.md) ·
[submit-outcome-unreconciled](runbooks/submit-outcome-unreconciled.md) · [api-down](runbooks/api-down.md) ·
[no-workers](runbooks/no-workers.md) · [api-errors](runbooks/api-errors.md) ·
[perception-degraded](runbooks/perception-degraded.md) · [portal-degraded](runbooks/portal-degraded.md) ·
[tasks-stalled](runbooks/tasks-stalled.md) · [intervention-waiting](runbooks/intervention-waiting.md) ·
[business-metrics-blind](runbooks/business-metrics-blind.md) · [outbox-lagging](runbooks/outbox-lagging.md) ·
[dlq-not-empty](runbooks/dlq-not-empty.md)

## Postmortems

* [2026-11-06: game day (portal outage, worker loss)](postmortems/2026-11-06-game-day.md)
