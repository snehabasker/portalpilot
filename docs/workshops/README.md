# Workshops: facilitation kit for a PortalPilot engagement

Three workshops, in the order a deployment needs them. Each has a goal, an agenda with timings, exercise
instructions, facilitator notes, and the artifacts it must produce. Most of the decisions they produce end up in
code, an ADR or a runbook in this repository.

| Workshop | When | Who | Length | Produces |
|---|---|---|---|---|
| [Design sprint: claims intake](design-sprint.md) | week 1 | claims lead, 2 claims handlers, IT/portal owner, risk, FDE (facilitator) | 3 × half day | target workflow, tested prototype of the operator flow, ranked risks, pilot scope |
| [Operator enablement](operator-enablement.md) | week 4, before supervised mode | the operators who will run the Studio | 2 h | operators who can answer an approval, an MFA prompt and a stuck task without help |
| [Incident tabletop](incident-tabletop.md) | week 5, before handover | operators, client IT on-call, FDE | 90 min | rehearsed response to the 3 likeliest incidents; runbook fixes |

How they connect: the design sprint decides *what* to automate and where humans stay in the loop. Enablement
teaches the people who stay in the loop. The tabletop rehearses what happens when the automation fails. The
[game day](../ops/postmortems/2026-11-06-game-day.md) is the technical counterpart of the tabletop: real failures
injected into a real stack.

## Facilitation principles used throughout

* **Work alone, together.** Individual silent writing first, then share. It stops the loudest voice from setting the
  frame and gets 3x the ideas.
* **One Decider.** Named at the start (usually the claims lead). The group gives input with dot votes; the Decider
  decides, and the reason is written down. No decisions by "consensus" that nobody owns.
* **Real artifacts, not opinions.** Time five real claims being keyed. Bring a real (anonymized) CSV export. Put the
  real portal on the screen. Arguments about "how long it takes" end when there is a stopwatch.
* **Timebox visibly**, with a timer on screen. When time is up, park it: the **parking lot** is reviewed at the end
  of every session and each item gets an owner or is dropped on purpose.
* **Write it where it will live.** Decisions go straight into an ADR draft, risks into the risk table in
  [ENGAGEMENT.md](../ENGAGEMENT.md), and runbook gaps into the runbook, during the session and not "later".

## Materials

Whiteboard (Miro/FigJam for remote), sticky notes in 3 colors, dot stickers (6 per person), a timer, the Studio
running locally with the mock portal (`docker compose up`), five anonymized claim forms, and the
[templates](templates.md).
