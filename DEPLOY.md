# TrendPulse – Deployment Guide

**Live:** https://trend-pulse-ten.vercel.app
**Host:** Vercel (project `trend-pulse`, team `mhtmedias-projects`)
**Repo:** https://github.com/MHTmedia/trend-pulse

---

## How it works

There are two moving parts, and they are deliberately separate:

| Part | Runs where | When |
|---|---|---|
| **Data fetch** (`scripts/fetch_trends.py`) | GitHub Actions | Nightly, 7 AM UTC (1 AM CST) |
| **Web app** (`app.py` + `static/index.html`) | Vercel serverless | On every request |

The nightly Action fetches Google Trends / Reddit / Amazon data, writes
`cache/trends.json` + `cache/keywords.json`, and **commits them to `main`**.
Vercel is linked to the repo, so that commit triggers an automatic redeploy
and the new data goes live within ~1 minute.

The Flask app never fetches anything. It only reads `cache/trends.json`.

> **Why the split:** Google Trends blocks datacenter IPs. Fetching from
> GitHub Actions works; fetching from a cloud host does not.

---

## Deploying a change

```bash
git push origin main
```

That's the whole deploy. Vercel builds and promotes to production automatically.
`./push.sh "your message"` does add + commit + push in one step.

Watch the build at https://vercel.com/mhtmedias-projects/trend-pulse

---

## Vercel configuration

`vercel.json` is the entire config:

```json
{
  "framework": "flask",
  "functions": { "app.py": { "includeFiles": "**/*" } }
}
```

Two things matter here, and both have bitten this project already:

1. **`includeFiles`** — without it, `cache/` and `static/` are not bundled into
   the serverless function and every request 404s or returns "no data yet".
2. **No `rewrites`.** Vercel natively detects Flask and routes to `app.py`.
   A catch-all rewrite (`/(.*)` → `/api/index`) makes things *worse*: Vercel now
   routes internal rewrites using the **rewritten** path, so Flask receives
   `/api/index`, matches no route, and 404s on every request.

Also note `CACHE_FILE` in `app.py` is built from `Path(__file__).parent`, not a
relative path — a serverless function's working directory is not the repo root.

---

## Local development

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://localhost:8000. It reads the same committed `cache/trends.json`, so
you see whatever the last nightly run produced — no waiting, no fetching.

To regenerate the cache locally (slow, ~10+ min, hits real APIs):

```bash
pip install pytrends requests beautifulsoup4 lxml
python scripts/fetch_trends.py
```

---

## Changing the tracked keywords

The keyword list is **data, not code** — it lives in `cache/keywords.json` and
the nightly discovery engine adds to it automatically.

- **To seed new categories/terms:** edit `DISCOVERY_SEEDS` in
  `scripts/fetch_trends.py`.
- **To change the fallback list** (used only if `keywords.json` is missing):
  edit `DEFAULT_KEYWORDS` in the same file.
- **To add or remove a specific keyword right now:** edit
  `cache/keywords.json` directly and push.

---

## Forcing a data refresh

There is no refresh endpoint — the app is read-only by design. To refresh now:

1. Go to the repo's **Actions** tab
2. Select **Refresh Trend Cache**
3. Click **Run workflow**

It commits the new cache, which redeploys Vercel automatically.

---

## Troubleshooting

### Site shows "Could not load trends"
`GET /api/status` tells you what the function can actually see:

```bash
curl https://trend-pulse-ten.vercel.app/api/status
```

- `keyword_count: 0` or `fetched_at: null` → the function can't read the cache.
  Almost always a missing/incorrect `includeFiles` in `vercel.json`.
- HTTP 404 on every path → a rewrite is hijacking routing (see above).

### Data is stale / hasn't updated
1. Did the nightly Action run? Check the repo's **Actions** tab.
2. Did it produce a commit? A no-change run commits nothing by design.
3. Did Vercel skip the build? **Never put `[skip ci]` in a commit message** —
   Vercel honours it too, so the data lands in git but never ships. This was
   removed from the workflow for exactly that reason.

### Build fails
Check logs at https://vercel.com/mhtmedias-projects/trend-pulse. Only `flask`
is required at runtime; the fetch-only deps (pytrends, bs4, lxml) are installed
by the Action, not by Vercel, and must stay out of `requirements.txt`.

---

## File structure

```
trend-tracker/
├── app.py                        ← Flask app (cache reader only)
├── vercel.json                   ← Vercel config
├── requirements.txt              ← Runtime deps (flask only)
├── .vercelignore
├── push.sh                       ← add + commit + push helper
├── cache/
│   ├── trends.json               ← Nightly output, committed
│   └── keywords.json             ← Tracked keyword list, committed
├── scripts/
│   └── fetch_trends.py           ← Nightly fetch + discovery engine
├── static/
│   └── index.html                ← Frontend (single file)
└── .github/workflows/
    └── refresh-trends.yml        ← Nightly schedule
```

---

## Environment variables

The public dashboard needs none of these — it reads committed cache files. They
unlock the account features and the non-commodity signals.

| Variable | Required for | Notes |
|---|---|---|
| `DATABASE_URL` | Watchlists, notes, outcomes | Postgres URL (Neon/Supabase/RDS). Without it the app runs read-only: the dashboard works, account features report themselves unavailable. Serverless filesystems are read-only, so SQLite is local-dev only. |
| `SECRET_KEY` | Sessions | Long random string. If unset in production every cold start mints a new key and silently logs everyone out. |
| `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` | Social demand | Reddit's unauthenticated search JSON now returns 403. Without credentials social demand is recorded as **unknown**, not zero, and affected keywords carry a lower confidence rating. |
| `YOUTUBE_API_KEY` | Creator-supply signal | Optional. |
| `TRENDPULSE_VERTICAL` | Vertical focus | One of the ids in `config/verticals.json`. Overrides the `active` field there. |
| `SIGNAL_ETSY` | Etsy listing counts | Off by default — Etsy 403s datacenter IPs. Set to `1` only if routing through a proxy. |

Set these in the Vercel project settings, and the `REDDIT_*` / `YOUTUBE_API_KEY`
ones as GitHub Actions secrets too, since the nightly job needs them.

## Scripts

```bash
python scripts/fetch_trends.py        # nightly: fetch, score, append a snapshot
python scripts/backfill_history.py    # one-off: replay git history into snapshots
python scripts/calibrate.py           # backtest the score, re-derive bands
python scripts/weekly_report.py       # build the shareable weekly report
```

`backfill_history.py` is safe to re-run; it skips days already present unless
`--force` is passed.

## First-party signal drops

Put JSON files in `data/proprietary/`:

```json
[
  {
    "keyword": "Creatine Gummies",
    "source":  "mht-client-campaigns",
    "as_of":   "2026-08-01",
    "signal":  78,
    "notes":   "3 clients, blended ROAS 2.4 across $48k spend"
  }
]
```

`signal` is a normalised 0–100 demand score, deliberately not raw spend or
revenue, so client-confidential figures never enter the repo. These feed the
`first_party` scoring factor — the one input a competitor cannot obtain.

## Data layout

| Path | Purpose |
|---|---|
| `cache/trends.json` | Current snapshot, served to the dashboard |
| `cache/history/snapshots/` | Append-only nightly record (archival; excluded from the Vercel bundle) |
| `cache/history/series.json` | Rolled-up per-keyword series |
| `cache/history/deltas.json` | Trajectory summary shipped with each page load |
| `cache/history/movers.json` | Precomputed movers |
| `cache/calibration.json` | Backtest results and score bands |
| `static/reports/` | Generated weekly reports |
