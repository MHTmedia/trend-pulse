# Design

<!-- impeccable:design-schema 1 -->

## World

**A working paper, not a dashboard.** The app ground is a desk; content sits on a
sheet with hairline rules. Light is the primary rendition because the artifact is
a document; dark is the same document reversed, never a separate identity.

The grammar is the preprint's: a masthead and dateline, an abstract, numbered
tables with captions, superscript footnote markers resolving to a Notes section,
and a colophon. These are load-bearing — the abstract is where the measured
accuracy is stated up front, and Note 2 is where the backtest is qualified.

Anti-reference (the incumbent look, replaced not polished): stock Tailwind
palette values, system-font-only typography, emoji as iconography, and every
element enclosed in a rounded card.

## Tokens

Defined on `:root`, redefined under both `@media (prefers-color-scheme: dark)`
(guarded `:root:not([data-theme="light"])`) and `:root[data-theme="dark"]`.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--desk` | `#e8e8e4` | `#0c0d10` | Page ground behind the sheet |
| `--sheet` | `#ffffff` | `#17191d` | The document surface |
| `--sheet-sunk` | `#f4f4f1` | `#1e2126` | Row hover, formula blocks, code |
| `--ink` | `#14161a` | `#e8e7e3` | Body text |
| `--ink-2` | `#4a4e57` | `#a8aab0` | Secondary prose, notes |
| `--ink-3` | `#767b85` | `#7e828a` | Labels, column heads, absent values |
| `--rule` | `#d8d8d3` | `#2c2f35` | Hairline rules between rows |
| `--rule-strong` | `#14161a` | `#e8e7e3` | Section and table-head rules |
| `--accent` | `#23417e` | `#8fb0ea` | Links, primary action, selection **only** |
| `--rise` / `--fall` | `#1c6046` / `#8f2f2f` | `#5fbf90` / `#e08a8a` | Direction |
| `--flag` | `#8a6d1f` | `#d6b45c` | Watchlist star, trial-ended notice |

Color strategy is **Restrained**: neutrals plus one accent. Direction color never
carries meaning alone — every delta also renders a sign (`+12` / `−9`) and a
drawn caret, so the table survives monochrome and color-vision differences.

## Type

| Role | Face | Notes |
|---|---|---|
| Display, prose, keywords | **Source Serif 4** | 400/600/700 + italic. The wordmark sets "Pulse" in italic 400 against roman 700. |
| UI, labels, all figures | **Archivo** | 400–700. Column heads at 10px/`.085em` uppercase. |

Every numeric context sets `font-variant-numeric: tabular-nums lining-nums` so
columns align; body prose keeps oldstyle figures. Fixed rem-ish scale, no fluid
type in the table. Prose measures are capped at 62–78ch.

## Components

- **Sheet** — `max-width: 1180px`, side rules and a soft shadow; rules drop below
  1220px so the document goes edge-to-edge on small screens.
- **Masthead** — wordmark, inline nav, then a dateline of observation number,
  date, market, sources and keyword count. Stacks below 760px.
- **Abstract** — a 128px label column beside prose stating counts, the tracked-day
  span, and the measured correlation with its footnote.
- **Notice** — the plan-state row (trial remaining, trial ended, public series).
  A rule-bounded row, never a tinted alert box.
- **Table 1** — the primary view. Ruled rows, star column, keyword with category
  sub-label, inline SVG sparkline, growth, interest, 7/30-day deltas, listings,
  and a viability figure with a 46px meter.
- **Entries** — secondary catalogue view; a bordered grid of entry blocks, each a
  keyword, a wide sparkline, and a definition list.
- **Notes** — numbered footnotes, the anchor targets for the abstract's markers.
- **Dialogs** — native `<dialog>` with `::backdrop`; header rule, scrolling body.
- **Icons** — a 16-unit SVG sprite, 1.5 stroke, `currentColor`. No emoji anywhere.

## Locked states

Gated cells render a lock glyph in `--ink-3`, never a blur or a teaser value, and
the table is followed by a notice naming what is withheld and what opens it.
Absent data renders as `—` and, in prose, as "unknown" or "not collected" —
never as zero, since a zero reads as a measurement.

## Motion

One authored moment: the loading skeleton's opacity pulse. Row and control
feedback is instant state change. Everything is disabled under
`prefers-reduced-motion: reduce`.

## Constraints

Single self-contained `static/index.html` served by Flask — no build step, no
framework, no CDN scripts. Fonts come from Google Fonts; sparklines and icons are
inline SVG. `static/track-record.html` is a companion document sharing the same
tokens, faces and rules.
