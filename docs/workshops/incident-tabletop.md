# Incident tabletop (90 min)

**Goal:** the client's operators and IT on-call have rehearsed the three likeliest PortalPilot incidents before
handover, and the runbooks have been corrected by the people who will use them.

A tabletop is a discussion-based exercise: no systems are touched. The facilitator plays the world (alerts, the
portal, the claims lead asking for updates) and reveals information in timed **injects**. Its technical counterpart
is the [game day](../ops/postmortems/2026-11-06-game-day.md), which injected real failures. Run the game day first,
and use its actual timings in the injects.

**Roles:** facilitator (FDE), **incident lead** (rotates per scenario), **operator** (works the Studio), **comms**
(writes client updates), **scribe** (keeps the timeline). Observers may only ask questions in the debrief.

## Agenda

| Time | Block |
|---|---|
| 0:00 | Ground rules (5 min): no blame, "I don't know" is a good answer, say what you would *do*, not what the system does. Roles assigned. |
| 0:05 | **Scenario 1: Monday-morning portal outage** (25 min) |
| 0:30 | **Scenario 2: "Was this claim filed twice?"** (25 min) |
| 0:55 | **Scenario 3: silent notifications** (20 min) |
| 1:15 | Debrief and runbook fixes (15 min): for each scenario, what helped, what was missing, owner and date per change. |

## Scenario 1: Monday-morning portal outage

| Inject (time) | Facilitator says | Look for |
|---|---|---|
| 0 min | "08:40. The weekend export, 400 rows, started at 08:30. A **PortalDegraded** ticket opens." | Who acknowledges? Do they open the runbook? |
| +5 | "Needs attention shows 3 stuck tasks, all at `login`, and the screenshots show *Service Unavailable*." | Do they confirm it is the portal (runbook step 1) before touching anything? |
| +10 | "The claims lead messages: *why has the batch stopped? 397 rows are queued.*" | Comms uses the status template. Does anyone know *why* queued rows don't start? (Parked tasks hold the concurrency slots, ADR 0013.) |
| +15 | "IT says the portal is back. Nothing moves." | They hand back the stuck tasks (runbook step 4). |
| +20 | "Reveal: one of the 3 stuck tasks was at `submit` when the portal went down." | Do they know the agent will run `dedupe_check` before submitting again? Who checks the claim on the portal? |

## Scenario 2: "Was this claim filed twice?"

| Inject | Facilitator says | Look for |
|---|---|---|
| 0 min | "A **DoubleSubmitSuspected** page fires at 02:10. You are the on-call." | Severity understood: page, acknowledge now. First action: stop workers (it is safe). |
| +5 | "One ledger row has `clicks = 2`." | Do they search the portal for the reference rather than trusting the database? |
| +10 | Branch A: "The portal shows one claim." Branch B: "The portal shows two." | A: they find `reconciled_absent` in the audit trail and close it. B: they escalate, ask the claims team to void one, and open a postmortem. |
| +15 | "The alert keeps re-firing after you explained the row." | Silencing a *specific* alert in Alertmanager versus disabling the rule. |

## Scenario 3: silent notifications

| Inject | Facilitator says | Look for |
|---|---|---|
| 0 min | "Operators say they haven't had a Teams message all day, but approvals are piling up. No alert has fired." | Who notices the monitoring gap? (`InterventionWaitingTooLong` should have fired. Did it, in the Studio?) |
| +5 | "The relay is running and outbox lag is 0." | They follow the event path: relay → bus → rule → notifier → webhook. Next stop: the notifier DLQ. |
| +10 | "The notifier DLQ has 41 messages, and the logs show 403 from the webhook." | Webhook rotated by the client's Teams admin: [dlq-not-empty](../ops/runbooks/dlq-not-empty.md). What about the 41 missed messages? |

## Debrief questions

1. Where did you look first, and was that the right place?
2. What did you need that you didn't have: access, a command, a dashboard, a person?
3. Which runbook step was wrong, unclear or missing? (Fix it in the room.)
4. What should page that didn't, and what paged that shouldn't have?
