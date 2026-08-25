# Design

<!-- impeccable:design-schema 1 -->

## World

**Radix.** A dense product interface on a disciplined neutral scale, dark-first.
One accent — iris — reserved for interactive and selected states and never used
as decoration. Bordered panels at 6/8px radii, Archivo at 13px with tabular
figures throughout.

Chosen by the user from three built options (Radix / Geist / Linear) rendered
against real data. Replaces an editorial working-paper direction that was built
and rejected for reading like a newsletter — serif display, abstracts and
footnote apparatus are anti-references, not fallbacks.

## Tokens

Defined on `:root` (dark), redefined under `@media (prefers-color-scheme: light)`
guarded `:root:not([data-theme="dark"])`, and again under `:root[data-theme="light"]`.

| Token | Dark | Light | Use |
|---|---|---|---|
| `--bg` | `#111113` | `#fcfcfd` | Page ground |
| `--panel` | `#18181b` | `#ffffff` | Cards, table, dialogs |
| `--panel-2` | `#1f1f23` | `#f7f7f9` | Elevated / selected segment |
| `--border` | `#26262b` | `#e6e6ea` | Standard 1px border |
| `--border-2/3` | `#33333b` / `#43434d` | `#d9d9e0` / `#c4c4cd` | Inputs, hover |
| `--text` | `#eeeef0` | `#1c1c1f` | Primary |
| `--text-2` | `#b4b4bb` | `#5c5c66` | Secondary |
| `--text-3` | `#7d7d86` | `#8b8b94` | Labels, absent values |
| `--accent` | `#5b5bd6` | `#5b5bd6` | Interactive + selected **only** |
| `--up` / `--down` | `#30a46c` / `#e5484d` | `#218358` / `#ce2c31` | Direction |
| `--warn` | `#ffb224` | `#a15c00` | Watchlist star, flagged notice, poor accuracy |
| `--c1`–`--c12` | vivid step | step 11 | Category identity |
| `--b1`–`--b5` | ramp | ramp | Viability bands |

### Colour systems

Three, each carrying meaning. Nothing is coloured for its own sake.

1. **Category identity** — 12 hues (`--c1`…`--c12`) assigned by a stable hash of
   the category name, so a category keeps its colour across sorts, filters and
   sessions and becomes something you learn rather than noise. Appears as a 7px
   dot beside the category label and as the 2px accent strip on a card.
2. **Viability ramp** — `--b1`…`--b5`, green → lime → amber → orange → red,
   applied to the score figure and its meter. The colour is derived from the
   *same* band that produces the label, because the server's bands are calibrated
   and move; a fixed threshold would print "Strong Opportunity" in amber.
   Adjacent steps are deliberately far apart in hue — the calibrated bands put
   most keywords in the top two, so near-identical greens would carry no
   information.
3. **Direction** — `--up` / `--down` on deltas and sparklines, always alongside a
   sign and a caret glyph, so the table survives monochrome and colour-vision
   differences.

Lens chips take their own hue when active and stay neutral when inactive. Stat
cards carry a tinted icon badge. The primary CTA is a vertical gradient with an
inset highlight; its hover lift is a real offset shadow, never a coloured halo.

## Type

**Archivo** (400/500/600/700) for everything; **JetBrains Mono** for formulas and
code only. One family is right here — this is product UI, not a brand surface.
Deliberately not Inter or Geist: both are flagged as converged defaults.

Steps: 11px meta · 12px labels · 13px body and table · 14px brand · 21px stat
figures. Ratios sit at 1.125–1.2, which is the correct band for dense product UI
even though a generic 1.25 heuristic flags it.

## Components

- **Top bar** — 52px, sticky, translucent with `backdrop-filter`. Brand, nav,
  vertical select, auth slot. Wraps to two rows below 820px.
- **Stat strip** — four cards: tracked, still climbing, breakouts, and score
  accuracy. The accuracy card turns `--warn` while the correlation is ≤ 0, so the
  product's weakest number is the one it shows first.
- **Notice** — plan state (public / trial / expired / locked). Panel-bordered
  row with the CTA right-aligned; `.flagged` variant for trial-ended.
- **Panel + table** — the primary view. Header with title and sort tag, ruled
  rows, star column, keyword + NEW chip, sparkline, growth, interest, 7/30-day
  deltas, listings, viability with a meter.
- **Cards** — secondary grid view, same data per keyword.
- **Dialogs** — native `<dialog>`, 12px radius, header rule, scrolling body.
- **Price panel** — one paid tier only. The figure comes from `plans.PRICE` via
  the API; the view never hardcodes it.
- **Icons** — 16-unit SVG sprite, 1.5 stroke, `currentColor`. No emoji.

## Locked states

Gated cells render a lock glyph in `--text-3` — never a blur, never a teaser
value. The table is followed by a notice naming what is withheld and what opens
it. Absent data renders `—`, and in prose "unknown" or "not collected", never
zero.

## Motion

One authored moment: the loading skeleton pulse. Everything else is instant state
change at 150ms or less. All disabled under `prefers-reduced-motion: reduce`.

## Constraints

Single self-contained `static/index.html` served by Flask — no build step, no
framework, no CDN scripts. Fonts from Google Fonts; sparklines and icons are
inline SVG. `static/track-record.html` is a companion surface on the same tokens.
