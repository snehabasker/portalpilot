# 0006. Playbooks in code; models only for perception

* Status: accepted (recorded retroactively)
* Date: 2026-11-02
* Deciders: Sneha Basker

## Context

An LLM agent could plan the whole flow itself ("file this claim"). The client's risk team needs to know in advance
what the system will do, in what order, with which checks. They need to review it, test it, and have it behave
the same way tomorrow.

## Decision

Each portal flow is a **playbook in code**: an ordered list of stages (`login → dedupe_check → fill_policy →
fill_claimant → fill_incident → review → submit`), each with explicit success checks. Models answer only perception
questions ("where is this control?"). Unknown pages escalate to a human.

## Consequences

* The submit guard, stage order and success checks can be reviewed, unit-tested and audited.
* A new portal or a changed flow needs a developer to update the playbook. This is the main scaling cost, and the
  first thing to revisit if PortalPilot serves many portals.
* Adding an LLM planner for one stage (say, an unexpected interstitial) is a contained change that leaves the
  guarantees alone.

## Alternatives considered

* **Free-roaming agent.** Flexible, but its behaviour is hard to bound and hard to explain to an auditor, and a
  side-effecting step (submit) ends up under the model's control.
* **Recorded macros.** Equivalent to code but untestable, and they break on layout changes the grounding tier would
  absorb.
