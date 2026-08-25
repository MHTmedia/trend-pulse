"""Generate the weekly TrendPulse report — the distribution moat.

Produces two artefacts per week:
    static/reports/<year>-W<week>.html   a shareable page
    static/reports/<year>-W<week>.md     newsletter-ready markdown

The report deliberately publishes the score's measured accuracy alongside its
picks. A trend product with a visible, checkable track record is far harder to
displace than one that only publishes predictions, and it is the cheapest way to
turn a public dataset into a brand people cite.

Usage:
    python scripts/weekly_report.py [--window 7] [--top 10]
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trendpulse import history, verticals
from trendpulse.paths import CALIBRATION_FILE, REPORT_DIR, TRENDS_FILE

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("report")


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return None


def build_markdown(ctx: dict) -> str:
    L = []
    add = L.append
    add(f"# TrendPulse Weekly — {ctx['label']}")
    add("")
    add(f"*{ctx['keyword_count']} keywords tracked · {ctx['history_days']} days of "
        f"proprietary history · vertical: {ctx['vertical_label']}*")
    add("")

    add("## Biggest movers this week")
    add("")
    if ctx["risers"]:
        add("| Keyword | Category | Viability | 7-day change |")
        add("|---|---|---:|---:|")
        for r in ctx["risers"]:
            add(f"| {r['keyword']} | {r.get('category') or '—'} | "
                f"{r['viability']} | +{r['delta']:.0f} |")
    else:
        add("_Not enough history yet to compute movers._")
    add("")

    if ctx["fallers"]:
        add("## Cooling off")
        add("")
        add("| Keyword | Category | Viability | 7-day change |")
        add("|---|---|---:|---:|")
        for r in ctx["fallers"]:
            add(f"| {r['keyword']} | {r.get('category') or '—'} | "
                f"{r['viability']} | {r['delta']:.0f} |")
        add("")

    if ctx["newly_tracked"]:
        add("## Newly discovered")
        add("")
        for n in ctx["newly_tracked"]:
            add(f"- **{n['keyword']}** ({n.get('category') or '—'}) — "
                f"viability {n.get('viability')}, first seen {n.get('first_seen')}")
        add("")

    add("## How this score is performing")
    add("")
    cal = ctx["calibration"]
    if not cal:
        add("_No backtest has been run yet._")
    else:
        add(f"- Backtest sample: **{cal['sample_size']}** keywords over a "
            f"**{cal['horizon_days']}-day** horizon")
        add(f"- Overall hit rate: **{cal['overall_hit_rate']}%**")
        add(f"- Higher score predicted stronger demand: "
            f"**{'yes' if cal.get('score_separates_outcomes') else 'no'}**")
        if cal.get("warning"):
            add("")
            add(f"> ⚠️ {cal['warning']}")
    add("")
    add("---")
    add("")
    add(f"_Generated {ctx['generated']} · Google Trends, Amazon, Wikipedia pageviews "
        "and first-party campaign data._")
    return "\n".join(L)


def build_html(ctx: dict, markdown: str) -> str:
    e = html.escape

    def rows(items, sign=""):
        if not items:
            return '<tr><td colspan="4" class="empty">Not enough history yet.</td></tr>'
        out = []
        for r in items:
            cls = "up" if r["delta"] > 0 else "down"
            out.append(
                f'<tr><td class="kw">{e(r["keyword"])}</td>'
                f'<td class="cat">{e(str(r.get("category") or "—"))}</td>'
                f'<td class="num">{r["viability"]}</td>'
                f'<td class="num {cls}">{sign if r["delta"] > 0 else ""}{r["delta"]:.0f}</td></tr>')
        return "".join(out)

    cal = ctx["calibration"] or {}
    warning_html = ""
    if cal.get("warning"):
        warning_html = f'<div class="warn"><strong>Accuracy caveat.</strong> {e(cal["warning"])}</div>'

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TrendPulse Weekly — {e(ctx['label'])}</title>
<style>
  :root {{ --bg:#0b0f17; --surface:#131926; --line:#212a3b; --text:#e6edf7;
           --muted:#8b97ab; --up:#22c55e; --down:#ef4444; --accent:#6366f1; }}
  @media (prefers-color-scheme: light) {{
    :root {{ --bg:#f7f8fb; --surface:#fff; --line:#e3e7ee; --text:#131926; --muted:#5d6675; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:32px 20px; background:var(--bg); color:var(--text);
          font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
  .wrap {{ max-width:820px; margin:0 auto; }}
  h1 {{ font-size:28px; margin:0 0 6px; letter-spacing:-.02em; }}
  h2 {{ font-size:17px; margin:34px 0 12px; letter-spacing:-.01em; }}
  .sub {{ color:var(--muted); font-size:13px; margin-bottom:8px; }}
  table {{ width:100%; border-collapse:collapse; background:var(--surface);
           border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
  th,td {{ padding:9px 12px; text-align:left; border-bottom:1px solid var(--line); font-size:13.5px; }}
  th {{ color:var(--muted); font-weight:600; font-size:11.5px; text-transform:uppercase;
        letter-spacing:.04em; }}
  tr:last-child td {{ border-bottom:none; }}
  .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .cat {{ color:var(--muted); }}
  .up {{ color:var(--up); font-weight:600; }}
  .down {{ color:var(--down); font-weight:600; }}
  .empty {{ color:var(--muted); text-align:center; padding:18px; }}
  .stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; }}
  .stat {{ background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }}
  .stat b {{ display:block; font-size:21px; letter-spacing:-.02em; }}
  .stat span {{ color:var(--muted); font-size:11.5px; text-transform:uppercase; letter-spacing:.04em; }}
  .warn {{ background:rgba(249,115,22,.09); border:1px solid rgba(249,115,22,.3);
           border-radius:10px; padding:13px 15px; font-size:13.5px; margin-top:14px; }}
  ul {{ padding-left:18px; }}
  footer {{ margin-top:38px; padding-top:16px; border-top:1px solid var(--line);
            color:var(--muted); font-size:12px; }}
  a {{ color:var(--accent); }}
</style></head>
<body><div class="wrap">
  <h1>TrendPulse Weekly</h1>
  <div class="sub">{e(ctx['label'])} · {ctx['keyword_count']} keywords ·
      {ctx['history_days']} days of history · {e(ctx['vertical_label'])}</div>

  <h2>Biggest movers this week</h2>
  <table><tr><th>Keyword</th><th>Category</th><th class="num">Viability</th>
    <th class="num">7-day</th></tr>{rows(ctx['risers'], '+')}</table>

  <h2>Cooling off</h2>
  <table><tr><th>Keyword</th><th>Category</th><th class="num">Viability</th>
    <th class="num">7-day</th></tr>{rows(ctx['fallers'])}</table>

  <h2>Newly discovered</h2>
  <ul>{"".join(f"<li><strong>{e(n['keyword'])}</strong> — viability {n.get('viability')}, first seen {e(str(n.get('first_seen')))}</li>" for n in ctx['newly_tracked']) or "<li class='empty'>None this week.</li>"}</ul>

  <h2>How this score is performing</h2>
  <div class="stats">
    <div class="stat"><b>{cal.get('sample_size','—')}</b><span>Backtest sample</span></div>
    <div class="stat"><b>{cal.get('overall_hit_rate','—')}%</b><span>Hit rate</span></div>
    <div class="stat"><b>{cal.get('horizon_days','—')}d</b><span>Horizon</span></div>
    <div class="stat"><b>{'Yes' if cal.get('score_separates_outcomes') else 'No'}</b>
      <span>Score predicts demand</span></div>
  </div>
  {warning_html}

  <footer>Generated {e(ctx['generated'])} · Sources: Google Trends, Amazon,
    Wikipedia pageviews, first-party campaign data ·
    <a href="/track-record">Full track record</a></footer>
</div></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=7)
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    trends = load_json(TRENDS_FILE)
    if not trends:
        log.error("No trends cache — run the nightly job first.")
        return 1

    movers_all = history.load_movers() or {"windows": {}}
    movers = movers_all.get("windows", {}).get(str(args.window), {"risers": [], "fallers": []})
    deltas = history.load_deltas()

    # "Newly discovered" = short observed history, ranked by current viability.
    newly = sorted(
        ({"keyword": k["keyword"], "category": k.get("category"),
          "viability": k.get("viability"),
          "first_seen": (deltas.get(history.norm(k["keyword"])) or {}).get("first_seen")}
         for k in trends["keywords"]
         if (deltas.get(history.norm(k["keyword"])) or {}).get("days_tracked", 99) <= 10),
        key=lambda n: n["viability"] or 0, reverse=True)[:args.top]

    now = datetime.utcnow()
    year, week, _ = now.isocalendar()
    slug = f"{year}-W{week:02d}"

    ctx = {
        "label":          f"Week {week}, {year}",
        "slug":           slug,
        "generated":      now.strftime("%Y-%m-%d %H:%M UTC"),
        "keyword_count":  len(trends["keywords"]),
        "history_days":   history.coverage()["snapshot_count"],
        "vertical_label": verticals.active_vertical().get("label", "All Categories"),
        "risers":         movers.get("risers", [])[:args.top],
        "fallers":        movers.get("fallers", [])[:args.top],
        "newly_tracked":  newly,
        "calibration":    load_json(CALIBRATION_FILE),
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    md   = build_markdown(ctx)
    page = build_html(ctx, md)
    (REPORT_DIR / f"{slug}.md").write_text(md)
    (REPORT_DIR / f"{slug}.html").write_text(page)

    index = sorted({p.stem for p in REPORT_DIR.glob("*.html")}, reverse=True)
    (REPORT_DIR / "index.json").write_text(json.dumps(
        {"generated": now.isoformat(), "reports": index}, indent=2))

    log.info("Wrote %s.html and %s.md (%d risers, %d fallers, %d new)",
             slug, slug, len(ctx["risers"]), len(ctx["fallers"]), len(newly))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
