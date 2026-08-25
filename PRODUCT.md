# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Solo e-commerce sellers — Amazon FBA and Shopify operators — deciding what to
launch next. They are self-serve and price-sensitive, and they evaluate in short
sessions squeezed between other work, so a session usually ends in one of two
outcomes: a keyword worth watching, or nothing worth the inventory risk.

## Product Purpose

Surface product trends early enough to act on, and be honest about how often the
call was right. The nightly pipeline tracks 535 keywords across Google Trends,
Amazon, Wikipedia and Reddit, scores each for launch viability, and accumulates
the result into a time series. Success is a seller avoiding a bad inventory
commitment as often as catching a good one.

## Positioning

Two things a neighboring product cannot truthfully copy:

1. **The accumulated history.** Every competitor can rebuild today's snapshot
   from the same public inputs in a weekend. None of them can retroactively
   acquire the daily observations since 2026-07-02 — 46 days and counting.
2. **The published accuracy.** The score's measured predictive power is
   printed on a public page, including when it is bad. Trend tools normally
   assert authority; this one shows its working.

## Operating Context

Data refreshes once nightly via GitHub Actions (7 AM UTC), which commits the
cache and triggers a Vercel redeploy. There is no live fetching and no refresh
button — the dashboard is a reader over a file the pipeline wrote. Sellers
arrive to a day-old picture by design, and the product should never imply
real-time data it does not have.

## Capabilities and Constraints

- **Read path is entirely file-based** out of `cache/`, needs no infrastructure,
  and keeps working when the database is absent.
- **Write path needs Postgres.** Serverless filesystems are read-only and
  ephemeral, so accounts, watchlists, notes and outcomes live in `DATABASE_URL`.
- **No build step.** The frontend is a single self-contained `static/index.html`
  served by Flask, with Chart.js from a CDN. Any design work must stay
  buildless — no npm, no bundler, no framework runtime.
- **Google Trends blocks datacenter IPs**, which is why fetching runs in GitHub
  Actions and never on the host.
- **Plans:** Free, a 7-day Trial, and Pro at **$29/month**. The trial requires
  email and password only — no card up front. Quotas meter distinct keywords
  per day rather than requests.
- **Reddit has returned 403 for the entire tracked history**, scoring as zero
  rather than unknown. Authenticated access is wired but unproven.

## Brand Commitments

Name: **TrendPulse**. The user's binding visual constraint is a clean,
tech-forward product interface in the register of https://www.radix-ui.com — a
disciplined neutral scale, one accent, dense and dark-first. An earlier editorial
direction was built and rejected for reading like a newsletter; do not return to
serif display, abstracts, or footnote apparatus.

Explicit anti-references: emoji as iconography, stock Tailwind palette values,
and the newsletter register.

## Evidence on Hand

Real, and safe to show:

- `cache/calibration.json` — n=355, 30-day horizon. The composite score
  correlates **−0.132** with forward movement; `current_interest` correlates
  **−0.562** while being weighted positively. Overall hit rate 11.8%.
- `cache/history/` — 46 daily snapshots, 2026-07-02 onward.
- `cache/trends.json` — 535 keywords with interest, growth, Amazon listing
  counts, price and rating.
- `static/reports/2026-W34` — one generated weekly report.

Absent, and not to be invented: customers, testimonials, revenue, user counts,
press, case studies, or any claim that the score is accurate. The calibration
above is the only accuracy evidence that exists, and it is unflattering.

## Product Principles

1. **Publish the accuracy, especially when it is bad.** The track record is
   never gated and never softened; it is the entire positioning.
2. **Sell the time dimension, not the keyword list.** The snapshot is
   reproducible by anyone; the history is not.
3. **Missing data is not good news.** Absent signals score as unknown, never as
   a favorable value.
4. **Never imply freshness the pipeline does not have.** Data is nightly, and
   the interface says so plainly.
5. **A seller's session should end in a decision**, not in browsing.

## Accessibility & Inclusion

No product-specific standard has been established. Baseline: the dense numeric
content must stay legible at small sizes in both themes, and colour must never
be the only carrier of rise/fall meaning.
