# Workshop templates

Copy into the whiteboard tool, or print at A3.

## How Might We note

> **HMW** _\<verb\>_ _\<who\>_ _\<outcome\>_ **without** _\<the constraint we must keep\>_?
>
> e.g. HMW *let a handler approve high-value claims* **without** *approving every claim*?

Rules: one per note, starts with "How might we", no solutions in the note.

## Sprint question

> Can we _\<outcome\>_ **if** _\<the risky condition\>_?
>
> e.g. Can the agent file 400 claims overnight **if** the portal times out on 1 in 50 submits?

## Solution sketch (3 panels)

| Panel 1: what the user sees first | Panel 2: what they do | Panel 3: what happens next |
|---|---|---|
| | | |

Title at the top, no names, words allowed, and it must make sense without the author present.

## Decision record (to fill in at the moment of decision)

| | |
|---|---|
| Decision | |
| Decider | |
| Options considered | |
| Straw-poll result | |
| Why this one | |
| What we'd need to see to revisit it | |

(Significant ones become an ADR: [docs/adr](../adr/README.md).)

## Risk storming grid

| Step in the flow | What could go wrong | Likelihood (1–3) | Impact (1–3) | Score | Control | Owner |
|---|---|---|---|---|---|---|
| submit | portal saves but times out, and the agent retries | 3 | 3 | 9 | reconcile by portal search; one-shot submit (ADR 0003) | FDE |
| | | | | | | |

## User-test observation grid

| Task | P1 | P2 | P3 | P4 | P5 | Pattern |
|---|---|---|---|---|---|---|
| finds what needs them | | | | | | |
| approves with evidence | | | | | | |
| enters MFA code | | | | | | |
| recovers stuck task | | | | | | |
| knows how to check for duplicates | | | | | | |

Mark each cell green (did it), red (failed or needed help) or grey (hesitated), with a one-line quote.

## Parking lot

| Item | Raised by | Owner | Decision: do / drop / later |
|---|---|---|---|
| | | | |
