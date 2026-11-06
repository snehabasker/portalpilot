# Operator enablement (2 h, hands-on)

**Goal:** every operator can, without help, (1) approve or reject a claim, (2) enter an MFA code, (3) take over a
stuck task and hand it back, and (4) tell whether anything has gone wrong today. This is checked at the end, not
assumed.

**Who:** up to 6 operators per session, each at their own machine. Facilitator plus one helper.

**Setup (facilitator, 30 min before):** one local stack per pair, or a shared demo deployment with one tenant per
pair. Prepare three CSVs: `easy.csv` (all clean), `approvals.csv` (two rows over the threshold) and `messy.csv`
(the e2e file). Set the mock portal to MFA on and pop-ups at 30 %.

## Agenda

| Time | Block | How |
|---|---|---|
| 0:00 | **Why it works this way** (15 min) | Not a feature tour. Three ideas only: the agent never presses submit twice (show Metrics: clicks = claims); a human is asked whenever the agent is unsure; nothing is lost if you walk away (workflows wait). |
| 0:15 | **Watch one claim** (10 min) | Upload `easy.csv` together, open one task's Replay. Point at the tier badge, the green box (verified element), and the red ✕ (a rejected guess). |
| 0:25 | **Drill 1: approvals** (15 min) | Pairs upload `approvals.csv`. Approve one, reject one with a note. Then find the note in the audit trail. |
| 0:40 | **Drill 2: MFA** (15 min) | The portal asks for a code. Get it from the "service phone" (the helper reads it from `/admin/mfa-code`). One pair waits 15 minutes on purpose: what happens? (The prompt reappears with a new code.) |
| 0:55 | *break* (10 min) | |
| 1:05 | **Drill 3: stuck task** (25 min) | The helper switches the portal to 503s for a minute, so tasks end up in takeover. Operators must find them, look at the screenshot, decide "the portal was down", and hand back with **retry stage**. Discussion: when would you use **I finished this stage** instead? (Only when you really did the stage yourself; the agent still checks the portal before submit.) |
| 1:30 | **Is anything wrong today?** (10 min) | Incidents page, Metrics, and what each alert means for *them* (the runbook "Impact" lines). |
| 1:40 | **Check** (15 min) | Each operator, alone, gets a fresh batch with one approval, one MFA prompt and one stuck task, plus a checklist. The facilitator observes, doesn't help, and notes where people hesitate. |
| 1:55 | **Close** (5 min) | Where the runbooks are, who to call, parking lot. |

## Facilitator notes

* People learn the takeover by being afraid of it. Say explicitly: "you cannot cause a duplicate from here; try
  things." Then let them.
* Watch for operators who approve without opening the screenshot. That is the habit to break; ask them what the
  amount was.
* The check is a test of the *training*, not of the people. Where three people hesitate at the same step, change the
  UI or the session. Write it down for the retro.

## Exit criteria (recorded per operator)

- [ ] Approved and rejected a claim, with a note
- [ ] Entered an MFA code within its validity window
- [ ] Recovered a stuck task with the correct hand-back mode
- [ ] Said in their own words what "clicks = claims" means
