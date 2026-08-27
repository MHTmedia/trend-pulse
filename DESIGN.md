# Design

<!-- impeccable:design-schema 1 -->

## World

**shadcn/ui, ported to plain CSS.** The same token architecture as
ui.shadcn.com — HSL triplets consumed through `hsl(var(--token))`, one
`--radius` deriving sm/md/lg, and the same component anatomy (Button variants,
Card, Badge, Table, Dialog, Input, Tabs, Alert).

shadcn is React + Tailwind + Radix primitives, and this app has no build step,
so the system is **implemented directly rather than installed**. The visual
identity is portable; the React packaging is not. A real migration would mean
npm, a bundler, rewriting the frontend as components, and changing the Vercel
build — see "If you migrate" below.

One deliberate theming change: `--primary` is emerald rather than shadcn's
neutral near-black. This product exists to surface opportunity, so the colour
that means "act on this" is the top of the opportunity scale.

## Tokens

Standard shadcn names, so any shadcn component or theme drops in unmodified:
`--background --foreground --card --card-foreground --popover --primary
--primary-foreground --secondary --muted --muted-foreground --accent
--destructive --border --input --ring --radius`.

Dark is `:root:not([data-theme="light"])` under `prefers-color-scheme: dark`,
plus a `[data-theme]` override in both directions.

### The opportunity scale

`--opp-1` … `--opp-5`, emerald → lime → amber → orange → rose. This is the
product's one evaluative judgement and the only thing allowed to use those hues:

| Token | Meaning |
|---|---|
| `--opp-1` | Prime — also `--primary`, `--up`, and the brand mark |
| `--opp-2` | Strong |
| `--opp-3` | Moderate — also the watchlist star and flagged alerts |
| `--opp-4` | Thin |
| `--opp-5` | Poor — also `--down` |

Applied to the score figure and its meter, derived from the **same band that
produces the label** (the server's bands are calibrated and move; a fixed
threshold printed "Strong Opportunity" in amber). Adjacent steps sit far apart
in hue deliberately — the calibrated bands put most keywords in the top two.

Category identity is a separate 12-hue set (`--c1`…`--c12`), assigned by a
stable hash of the category name, and is never evaluative — it says *what*, not
*how good*.

Direction never relies on colour alone: every delta carries a sign and a caret.

## If you migrate

To use the real shadcn components: `npx shadcn@latest init`, move
`static/index.html` into a React app, keep `app.py` as the JSON API, and point
Vercel at the built bundle. The tokens above transfer verbatim — that is the
point of matching the names.

## Type

**Archivo** for everything; **JetBrains Mono** for formulas only. Deliberately
not shadcn's default Inter, nor Geist — both are flagged as converged defaults,
and the point of this exercise was to not look generated.

shadcn's scale: 14px body, 13px meta, 15px card titles, 18px dialog titles,
26px stat figures, with `-0.03em` tracking on the large figures.

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
