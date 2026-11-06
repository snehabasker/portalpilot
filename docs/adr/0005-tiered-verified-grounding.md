# 0005. Tiered grounding, with every point verified against the DOM

* Status: accepted (recorded retroactively)
* Date: 2026-11-02
* Deciders: Sneha Basker

## Context

Legacy portals have no stable selectors, so the agent has to locate controls from the screenshot ("click the
Policy number field"). A frontier model is accurate but costs money and seconds per action, at roughly 25 actions
per claim and thousands of claims a month. My GRPO-trained Qwen2-VL-2B click policy is nearly free on our own GPU,
but it scored 91.3 % on this portal, and a 9 % miss rate on clicks is unacceptable on its own.

## Decision

Send each action through a chain of tiers, cheapest first: `cache → qwen → claude` (`dom` for CI and machines
without a GPU). Before any click, a **verifier** checks the proposed point: `elementFromPoint` must be the right
kind of element, its accessible name must contain the expected text, and no modal may be covering it. A rejected
point escalates to the next tier, and a verified point is cached under a hash of the page's *structure*.

## Consequences

* The verifier, not model confidence, decides whether to click. All 6 of the small model's misses (label cell
  instead of input) were caught before clicking.
* Cost per claim is dominated by the cache after the first row of a batch (34 of 99 actions in the chaos run).
* Verification needs the expected element's name. It cannot catch "right element, wrong meaning" on a page whose
  labels lie. The review-page read-back before submit covers that case.
* Every tier's attempt is recorded (point, verdict, latency, cost), which is what feeds the replay UI and the
  `portalpilot_grounding_attempts_total` metric.

## Alternatives considered

* **Frontier model only.** It is simplest, but it costs ~$0.01–0.03 per action, takes seconds per action, and makes
  an outside provider part of the critical path.
* **Small model only.** Cheap, but 91 % is not good enough without a safety net.
* **Classic RPA selectors.** They break silently when the portal changes and need a developer for every layout
  change.
