# Design sprint: claims intake (3 half-days)

A compressed design sprint (Map → Sketch → Decide/Prototype → Test). A full 5-day sprint is too much client time for
a 6-week engagement, so this version uses three mornings and does the prototyping between sessions. Its job is to
answer, before anything is built: **which part of claim intake do we automate first, where must a human stay in the
loop, and what would make the claims lead say yes after the pilot?**

**Participants (max 7):** claims lead (**Decider**), two claims handlers who key claims today, the portal owner from
IT, someone from risk/compliance, the FDE (facilitator), and optionally an engineer to note constraints.

**Before day 1 (FDE):** sit with a handler for an hour and time five real claims end to end; get an anonymized CSV
export; get test-environment access to the portal; read ENGAGEMENT § 1 and send the attendees the questions that
need data (volumes, error rates).

---

## Day 1: Map (3 h 15)

| Time | Activity | Output |
|---|---|---|
| 0:00 | **Intro and ground rules** (10 min). Goal, schedule, the Decider, phones away, parking lot. | |
| 0:10 | **Long-term goal** (20 min). "In 6 months, claim intake is…" Each writes one silently; read out; Decider picks or merges. | goal statement |
| 0:30 | **Sprint questions** (20 min). "To meet the goal, what has to be true? What could stop us?" Rephrase as questions: *Can the agent file a claim without ever filing it twice? Will handlers trust it with claims over £5k?* | 3–5 sprint questions |
| 0:50 | **Map the current flow** (40 min). Handlers walk the real flow on the real portal while the FDE draws it: CSV → retype → MFA → pages → review → submit → note the claim number. Mark every **wait**, **error-prone step** and **judgment call**. | current-state map |
| 1:30 | *break* (15 min) | |
| 1:45 | **Ask the experts** (40 min). Short interviews: handler ("what goes wrong?"), IT ("what does the portal do under load, maintenance windows, session limits?"), risk ("what must never happen? what must be auditable?"). Everyone else writes **How Might We** notes silently. | ~40 HMW notes |
| 2:25 | **Organize and vote HMWs** (25 min). Affinity-cluster on the wall, then 2 dots each. | top 5–6 HMWs |
| 2:50 | **Pick the target** (15 min). The Decider circles the moment on the map where the sprint will focus. | target, with reason |
| 3:05 | **Parking lot review** (10 min) | owners |

**Facilitator notes**

* During the map, *the handlers drive the mouse*. When they say "and then I just…", stop and ask what "just" is.
  Those are the steps nobody documents (copying the claim number into a spreadsheet, re-logging in after the
  20-minute timeout).
* Risk will often say "never" ("it must never submit without a person"). Don't argue. Write it as a HMW ("How might
  we let a person approve high-value claims without approving every claim?"), which becomes a design question rather
  than a veto.
* Typical HMWs for this engagement, to seed if the room is quiet: *HMW know a claim was filed if the portal times
  out? HMW avoid retyping from the export? HMW tell someone an MFA code is needed while they are away from the
  Studio?* (That last one led to the notifier in ADR 0009.)

## Day 2: Sketch and decide (3 h)

| Time | Activity | Output |
|---|---|---|
| 0:00 | **Lightning demos** (30 min). 3-minute tours of things worth stealing: the mock portal driven by the agent; another tool's approval inbox; how their claims system already alerts people. Capture "big ideas" on stickies. | ~10 big ideas |
| 0:30 | **Four-step sketch, silently** (75 min): notes (20) → ideas (20) → **Crazy 8s** (8 frames in 8 min, on the target moment, e.g. "the handler's view when a claim needs them") → solution sketch (25 min, 3 panels, anonymous, self-explanatory). | one solution sketch per person |
| 1:45 | *break* (15 min) | |
| 2:00 | **Art museum and heat map** (15 min). Sketches on the wall; everyone puts small dots on the parts they like. No talking. | heat map |
| 2:15 | **Speed critique** (25 min). The facilitator narrates each sketch (3 min), the group calls out standouts, the creator speaks last. | standout ideas per sketch |
| 2:40 | **Straw poll, then Decider vote** (15 min). Everyone one vote; the Decider gets 3 "supervotes" and writes down why. | chosen concept(s) |
| 2:55 | **Assumptions to test** (5 min). What must be true for this concept to work? The two riskiest become the test questions for day 3. | test questions |

**Between day 2 and day 3 (FDE):** build the prototype. For this engagement the prototype is the real Studio,
configured as the chosen concept (approval threshold, which interventions exist, how they look), with the mock portal
set to behave like the client's (MFA on, their pop-ups). Write a test script of 4–5 tasks.

**Facilitator notes**

* Crazy 8s is uncomfortable for non-designers. Say out loud that the sketches are *meant* to be ugly, and do one
  yourself on screen first.
* Anonymity during the art museum matters most for the handlers: their sketch should not lose to the claims lead's
  because of who drew it.
* Supervotes go to the Decider for a reason: they own the outcome. If the straw poll and the supervotes disagree,
  record both. That disagreement is useful in the retro.

## Day 3: Test and plan (3 h 15)

| Time | Activity | Output |
|---|---|---|
| 0:00 | **Five user tests** (5 × 25 min). One handler or operator per slot, think-aloud, one interviewer, everyone else watching from another room or call, taking notes on a shared grid (participant × task: green/red/neutral). | observation grid |
| 2:05 | *break* (10 min) | |
| 2:15 | **Patterns** (25 min). Read the grid: what worked for 3+ people, what failed for 3+? Answer the sprint questions from day 1 one by one: yes / no / unclear. | answered sprint questions |
| 2:40 | **Risk storming on the target flow** (25 min). For each step: "how could this go wrong?" Silent writing, cluster, then score likelihood × impact. Feeds ENGAGEMENT § 6. | ranked risks + controls |
| 3:05 | **Pilot scope and success criteria** (10 min). The Decider commits: which claims, what threshold, what measurement, what "yes" looks like at week 6. | pilot plan (ENGAGEMENT § 4) |

**User-test script (operator flow), as used with the Studio prototype**

1. "A batch from this morning's export has finished. Tell me whether anything needs you." *(Watch: do they find
   Needs attention? Do they understand `waiting_human`?)*
2. "Claim 4 is over the approval limit. Decide whether it should be filed." *(Do they trust the screenshot of the
   review page? Do they look for the source row?)*
3. "The portal is asking for a code. The phone is on your desk." *(MFA flow; time from prompt to code entered.)*
4. "The agent says it is stuck. Find out why and get it moving again." *(Takeover: do they understand that hand
   back means the agent re-checks before submitting?)*
5. "How would you know if a claim had been filed twice?" *(Can they find the Metrics clicks-vs-claims stat?)*

**Facilitator notes**

* Interview rules: ask "what are you looking for?" and "what do you expect to happen?", and never "is that
  clear?". When they ask you a question, answer with "what would you do if I weren't here?"
* Recruit the testers from people who were *not* in the sprint. Day-1 participants know too much.

---

## What a sprint like this produces in this repository

| Sprint output | Where it lives |
|---|---|
| Goal, sprint questions, success criteria | ENGAGEMENT § 4 (rollout plan and exit criteria) |
| HMW "know a claim was filed if the portal times out" | ADR 0003, the reconcile path |
| HMW "tell someone an MFA code is needed" | ADR 0009, the notifier Lambda |
| "Avoid retyping from the export" | ADR 0009, S3 intake |
| Risk storming results | ENGAGEMENT § 6 (risks and controls) |
| Test finding "operators did not know a stuck task could be handed back" | the Studio's takeover hint text; the [operator enablement](operator-enablement.md) drill |

## Remote variant

The same agenda in Miro or FigJam, with sessions capped at 2 h 30 and a 10-minute break every hour. Silent writing
works better remotely (no peeking), but energy drops faster. Cameras on during critique, and pre-made frames for
Crazy 8s so nobody wrestles the tool.
