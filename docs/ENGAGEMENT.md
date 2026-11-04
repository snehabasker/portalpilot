# Engagement pack: automating claim intake into a legacy provider portal

This is a worked example of the documents that go with a client deployment of PortalPilot. The client and
figures are illustrative. Replace them with what discovery finds.

## 1. Discovery questions (first two sessions)

**The work.** How many claims per week? What is the peak? Who keys them today, and how long does one take
(time five real ones)? What fraction need a phone call or a correction afterwards? Which fields are retyped from
where?

**The portal.** Is there truly no API, or bulk upload, or SFTP? (Ask the portal vendor; this is the cheapest
possible outcome.) Is MFA required, and by what factor? Is a service account allowed, or must it be a named user?
Are there rate limits, session timeouts, or maintenance windows? Is there a search screen where a submitted claim
can be found by our own reference? That screen is what makes double-submit protection possible.

**Risk and policy.** Which claims need a human sign-off (amount, type, customer tier)? Who may approve? Where may
screenshots of customer data be stored, and for how long? Is there a data residency requirement? What does audit
need to see?

**Success.** What would make this a yes in 6 weeks: hours saved, backlog cleared, error rate?

## 2. Use-case fit

| Criterion | Claim intake | Notes |
|---|---|---|
| Volume and repetition | high | same 4-page flow per row |
| Cost of an error | high: a duplicate claim is a payout risk | hence the submit ledger and portal-side dedupe |
| Recoverability | good | the portal is searchable by our reference |
| Variability of pages | low to medium | pop-ups and maintenance pages, handled; new page layouts escalate to a human |
| Human judgment needed | some | approval threshold, MFA |

## 3. ROI model (illustrative)

| Input | Value |
|---|---|
| Claims per month | 6,000 |
| Manual minutes per claim (measured) | 7 |
| Loaded cost per hour | $38 |
| Share needing human touch with PortalPilot (MFA, approvals, escalations) | 12%, at 1.5 min each |
| Grounding cost per claim (cache + own-GPU Qwen; Claude only on escalations) | ≈ $0.002 |
| Infra (Fargate, RDS, ALB, GPU node part-time) | ≈ $900 / month |

Manual: 6,000 × 7 / 60 × $38 = **$26,600 / month**.
With PortalPilot: humans 6,000 × 12% × 1.5 / 60 × $38 ≈ $680, plus infra $900 and models ≈ $12, so **≈ $1,600 / month**.
Payback on a 6-week build happens inside the first quarter. Duplicate claims prevented are not counted, and they
are often the larger number.

Sensitivities: the human-touch share (measure it in the pilot) and the manual minutes (time real work, don't
estimate it).

## 4. Rollout plan

| Week | Milestone | Exit criterion |
|---|---|---|
| 1 | Discovery, portal walkthrough, sample CSVs, playbook draft | stages and success checks agreed with the claims lead |
| 2 | Deploy to client AWS (`deploy.sh`), vault the service account, dry runs against the portal's test environment | 50 rows filed in test, 0 duplicates |
| 3 | **Shadow mode**: agent fills the form up to review, human compares and submits | ≥ 98% field accuracy on 200 rows |
| 4 | Supervised: agent submits under the approval threshold; humans approve everything above it | 0 duplicates, escalation rate measured |
| 5–6 | Raise threshold and concurrency, hand over runbook, train operators on the Studio | client team runs a full week unassisted |

## 5. Runbook (operators)

| Situation | What you see | What to do |
|---|---|---|
| MFA prompt | "Needs attention: mfa code" | enter the code from the service phone; it is sealed and used once |
| Approval | review-page screenshot with the amount | Approve or Reject with a note (stored in the audit log) |
| Agent stuck (`grounding_failure` / `unexpected_page`) | Studio opens in takeover with the live page | click and type on the screenshot, then **Hand back: retry stage** (agent tries again) or **I finished this stage** (agent moves on). The agent always re-checks the portal before submitting. |
| Worker alarm "no workers" | CloudWatch / email | ECS restarts tasks automatically; workflows wait safely and resume. If it persists, check the worker log group. |
| Portal down for maintenance | tasks retrying, then waiting on a human | nothing; retries back off. For long outages, leave the tasks: they resume when it returns. |
| Need to stop everything | | scale the `worker` service to 0; nothing is lost |

## 6. Risks and controls

| Risk | Control |
|---|---|
| Duplicate submission | idempotency key typed into the portal, dedupe search before every submit, one-shot submit guarded by a SQL state machine, reconcile on unknown outcome |
| Wrong field filled | every click verified against the element under the pixel; the review page is read back and compared with the source row before submit |
| Credential exposure | KMS envelope encryption, decrypted inside the activity only, never in workflow history or logs, decryption audited |
| Portal changes layout | vision grounding needs no selectors; a changed page fails verification and goes to a human instead of a wrong click |
| Screenshots contain PII | private S3 bucket, SSE, 90-day expiry (configurable), access only via the operator API |
| Model provider outage | the Qwen tier runs on our own GPU; the Claude tier is a fallback, not a dependency |
| Audit | append-only, hash-chained audit log; full step replay per claim |
