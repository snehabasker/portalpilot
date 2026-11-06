# 0011. SLO-based alerting with Prometheus; incidents recorded in the product

* Status: accepted
* Date: 2026-11-06
* Deciders: Sneha Basker

## Context

The first version had per-request traces in the replay (grounding traces, step timings), two CloudWatch alarms
(API 5xx, no workers), and an operator Metrics page computed from Postgres. That is good for debugging one task,
but it does not answer the on-call questions: *is the service healthy right now, is it getting worse, and who
needs to act?* Alarms on raw thresholds were either noisy or late. Also, the people who respond to PortalPilot
problems are mostly the operators already in the Studio, not a separate SRE team.

## Decision

1. **Prometheus metrics** from every process: stage outcomes and durations, grounding attempts by tier and verdict,
   ledger transitions, HTTP requests. The API also exposes **Postgres-derived gauges** at scrape time (task states,
   open interventions and their age, the double-submit invariant, unreconciled submits, outbox lag), which stay
   correct across restarts because they come from the system of record.
2. **SLOs with multi-window burn-rate alerts** (Google SRE workbook): API availability 99.5 %, perception 99 %
   (actions some tier could verify). Plus **invariant alerts** that page on any non-zero value: double-submit
   suspects and unreconciled submits. Every alert has a severity (`page` or `ticket`), an actionable summary and a
   runbook. The rules have promtool unit tests in CI.
3. **Alertmanager posts to the product.** Alerts become **incidents** in Postgres with a timeline (fired,
   acknowledged, notes, resolved). Operators acknowledge and annotate them in the Studio, and the outbox relays
   `incident.opened` to the notifier, which is how they get paged in chat. A client with PagerDuty or Opsgenie adds
   a receiver alongside it.
4. **Dashboards as code** (a Python generator, two dashboards: on-call overview and agent internals).

## Consequences

* The game day on 2026-11-06 exercised this end to end: two injected failures, two incidents, acknowledged and
  resolved, with a postmortem written from the recorded timeline.
* Burn-rate alerts are quiet for short blips and fast for sustained burns. A promtool test covers both cases.
* At low volume, ratio SLIs are jumpy. Alerts that use them carry a minimum-traffic guard.
* The CloudWatch alarms stay for the AWS-level failure modes Prometheus can't see from inside (no tasks running at
  all, DLQ depth).
* There are two sources of truth for "is it broken": Prometheus for the signal, Postgres for the incident record.
  They are joined by the alert fingerprint.

## Alternatives considered

* **CloudWatch only.** It is native on AWS, but it does not carry over to Kubernetes clients (ADR 0010), and SLO
  burn-rate math there is clumsy.
* **A hosted APM / incident tool only.** It makes a third-party vendor a requirement for every client, and the
  record of what happened lives outside the client's account.
* **Static thresholds** ("5xx > 5 in 5 min"). Simple, but they page at 3 a.m. for one bad minute and miss slow burns.
