# 0012. Design tokens as the source of truth, contrast checked in CI

* Status: accepted
* Date: 2026-11-06
* Deciders: Sneha Basker

## Context

The Studio grew page by page with one stylesheet and hand-picked colors. Three problems showed up:

* **Accessibility bugs nobody noticed.** The light theme reused the dark theme's status colors. Measured against
  white: ok 2.49:1, warn 2.19:1, bad 3.15:1, accent 2.72:1, operator 1.59:1. All fail WCAG AA (4.5:1) for the
  12 px chip text they were used for. The primary button's white text on the accent blue was 2.72:1 in *both*
  themes.
* **Colors defined in three places**: the CSS, inline styles in pages, and now Grafana dashboards, which should use
  the same status colors so an operator reads them the same way.
* New pages (Incidents) had no shared components to build from.

## Decision

* `frontend/src/design-system/tokens.json` is the single source for color (semantic names such as `ok`,
  `text-muted`, `on-accent`, `tier-qwen`, with values per theme), spacing, radius, type and motion.
* `scripts/build-tokens.mjs` generates `tokens.css` (custom properties, with the dark theme default, a light theme,
  and `prefers-color-scheme`) and `tokens.ts`, and **checks declared contrast pairs in every theme**. CI runs it
  with `--check`, which fails on a stale generated file or on any pair below its minimum.
* Components (`Button`, `StatusChip`, `TierBadge`, `Card`, `Stat`, `Progress`, `Timeline`, `KeyValue`, `Field`,
  `EmptyState`, `PageHeader`, `LiveDot`) take semantic props (`tone="bad"`), never colors. Status is always shown
  as text as well as color.
* A **living styleguide** page in the Studio (`#/design`) renders every token, every component state and the
  contrast report.
* Existing page CSS aliases the old variable names to tokens, so pages migrate gradually rather than in a big-bang
  rewrite.

## Consequences

* The light-theme failures are fixed by darker status tokens, and adding a new pair is one line in `tokens.json`.
* The Grafana dashboard generator uses the same hex values. They are still copied, not imported. A follow-up is to
  read `tokens.json` from the generator.
* Contrast checks cover declared pairs, not every rendered combination, and do not replace a screen-reader pass.
* Some pages still use pre-token class names. They render correctly through the aliases and move over as they
  change.

## Alternatives considered

* **Adopt a component library (MUI, Chakra).** Faster at first, but the Studio's needs are specific (screenshot
  overlays, tier colors, dense operator tables) and the library's theme layer would become a second token system.
* **Style Dictionary.** It is the standard token pipeline, but a 90-line script covers two outputs and adds the
  contrast gate, which Style Dictionary does not do out of the box.
* **Storybook.** Useful once several teams consume the components. For one app, the in-app styleguide shows the
  real components under the real CSS, with no extra build.
