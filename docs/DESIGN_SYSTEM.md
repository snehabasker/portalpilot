# PortalPilot design system

The Studio is used for long stretches by operators who make consequential decisions (approve a £7,400 claim, take
over a browser). The design system serves that: **status must be unmistakable, dense data must stay readable, and
nothing may depend on color alone.**

Live styleguide: run the Studio and open **Design system** (`#/design`). It renders every token, every component state
and the contrast report from the same code the pages use.

## Structure

```
frontend/src/design-system/
  tokens.json        source of truth: colors per theme, space, radius, type, motion, contrast pairs
  tokens.css         GENERATED: CSS custom properties (dark default, light, prefers-color-scheme)
  tokens.ts          GENERATED: typed tokens + the contrast report
  components.tsx     Button, StatusChip, TierBadge, LiveDot, Card, Stat, StatGrid, PageHeader, EmptyState,
                     KeyValue, Progress, Timeline, Field
  components.css     component styles, using semantic tokens only
frontend/scripts/build-tokens.mjs   npm run tokens (write) · npm run tokens:check (CI)
```

## Tokens

**Semantic, not literal.** Components use `--color-ok`, `--color-text-muted`, `--color-on-accent`, never `#3fb97b`.
A theme is a mapping from semantic names to values, so the light theme can use a darker green without touching any
component.

| Group | Tokens | Notes |
|---|---|---|
| Surfaces | `bg`, `surface`, `surface-2`, `line`, `media-bg` | `media-bg` stays black in both themes: screenshots are framed the same way |
| Text | `text`, `text-muted` | muted is still ≥ 4.5:1 on every surface |
| Intent | `accent`, `ok`, `warn`, `bad`, `operator` | used as text and outline colors; `on-accent` and `on-warn` for text on filled backgrounds |
| Grounding tiers | `tier-cache`, `tier-qwen`, `tier-claude`, `tier-dom` + `on-tier` | filled badges; the same colors are used in Grafana |
| Scale | `space-1…6` (4 px steps), `radius-sm/md/lg/pill`, `font-size-xs…stat`, `motion-fast/base` | |

**Contrast is a build check.** `tokens.json` declares the pairs that appear in the UI (`["warn", "surface", 4.5]`),
and `npm run tokens:check` fails CI if any pair in any theme is below its minimum, or if the generated files are
stale. Adding a new foreground/background combination to a component means adding its pair.

The check exists because of a real bug: before the tokens, the light theme reused the dark theme's status colors
(warn on white 2.19:1, operator 1.59:1), and the primary button's white label on blue was 2.72:1 in both themes.

## Components

Rules every component follows:

1. **Semantic props.** `<StatusChip status="waiting_human" />` picks its tone from the status, and
   `<Card tone="bad">` and `<Stat tone="ok">` take intents, not colors.
2. **Status is text and color.** Chips print the status word; progress bars have an `aria-label` that lists their
   parts; the live indicator says "live" or "offline".
3. **Real elements.** Buttons are `<button>`, links are `<a>`, focus is visible (`:focus-visible` outline in accent).
   `Button busy` sets `aria-busy` and disables the button against double clicks, which matters on Approve.
4. **Motion respects `prefers-reduced-motion`.**

| Component | Use for | Don't use for |
|---|---|---|
| `Button` (`primary` / `secondary` / `ok` / `danger` / `ghost`) | one primary per view; `ok`/`danger` for approve/reject pairs | navigation (use a link) |
| `StatusChip` | any lifecycle state (task, batch, intervention, incident, ledger) | free text |
| `TierBadge` | which grounding tier acted | anything else; tier colors are reserved |
| `Card` (`tone="warn"/"bad"`) | grouping; toned cards for things needing a person | decoration |
| `Stat` / `StatGrid` | headline numbers with a one-line hint that says how to read them | tables of numbers |
| `Progress` | parts of a whole (batch states, tier share) | time remaining |
| `Timeline` | ordered events (incident timeline, task history) | |
| `KeyValue` | inspectors (task, ledger, incident) | |
| `Field` | every form control, with a label and an optional hint | |
| `EmptyState` | lists that can be empty; say what empty *means* ("Nothing is waiting on a human.") | |

## Adoption

The Incidents page and the styleguide are built only from design-system components. Metrics, Needs attention and
Batches use them for stats, cards, empty states and progress. The Replay Studio's specialized parts (screenshot
overlay, filmstrip, step timeline) still use page CSS, which reads tokens through aliases (`--ok` →
`--color-ok`), so they are themed and contrast-safe and move to components as they change.

## Changing a token

1. Edit `tokens.json`.
2. `npm run tokens`. This writes the CSS and TS and prints the contrast report.
3. Check `#/design` in both themes.
4. If the color also appears in Grafana (status or tier colors), update `deploy/observability/grafana/build_dashboards.py`
   and regenerate.
