"""Backtest the viability score against what actually happened next.

Two sources of truth, in increasing order of value:

  1. Forward interest movement, recovered from the snapshot store. For every
     keyword we know its score on day T and its Google Trends interest on both
     day T and day T+N, so we can ask whether a high score actually preceded
     rising demand. Available today, on ~46 days of history.

  2. Real user outcomes, from the outcomes table. Someone scored a keyword,
     launched, and told us how it went. Far more valuable and impossible for a
     competitor to obtain — but only accumulates once accounts are live, so the
     script runs happily with zero outcome rows and reports the gap honestly.

Output lands in cache/calibration.json and drives both the score bands and the
public track-record page.

Usage:
    python scripts/calibrate.py [--horizon 30] [--hit-threshold 20]
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trendpulse import history
from trendpulse.paths import CALIBRATION_FILE

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("calibrate")

# Band labels/colors are fixed; only the thresholds are re-derived.
BAND_SHAPE = [
    ("Strong Opportunity", "#22c55e", 0.08),   # aim for the top ~8% of scores
    ("Good Potential",     "#86efac", 0.22),
    ("Moderate Risk",      "#facc15", 0.45),
    ("High Risk",          "#f97316", 0.75),
    ("Not Recommended",    "#ef4444", 1.00),
]


def _value_at(arr, dates, target_day):
    """Latest non-null value at or before target_day."""
    for i in range(len(dates) - 1, -1, -1):
        if arr[i] is None:
            continue
        if dates[i] <= target_day:
            return arr[i], dates[i]
    return None, None


def _latest(arr, dates):
    for i in range(len(dates) - 1, -1, -1):
        if arr[i] is not None:
            return arr[i], dates[i]
    return None, None


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Rank correlation — robust to the heavy skew in these signals."""
    n = len(xs)
    if n < 20:
        return None

    def ranks(vals):
        order = sorted(range(n), key=lambda i: vals[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx  = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    dy  = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return round(num / (dx * dy), 3)


def factor_diagnostics(samples: list[dict]) -> dict:
    """Which inputs actually predict forward demand, measured one at a time.

    This is the part that turns the score from a fixed formula into something
    that can be retrained. A factor with a negative correlation is actively
    hurting the score and should be re-weighted or inverted.
    """
    fields = {
        "growth_at_score":   "12-month growth % (drives trend_momentum)",
        "interest_at_score": "current interest (drives current_interest)",
        "reddit_at_score":   "Reddit posts / 30d (drives social_demand)",
        "listings_at_score": "Amazon listings (drives competition)",
        "score_then":        "composite viability score",
    }
    out = {}
    for field, desc in fields.items():
        pairs = [(s[field], s["forward_change"]) for s in samples if s.get(field) is not None]
        if len(pairs) < 20:
            out[field] = {"description": desc, "n": len(pairs), "correlation": None}
            continue
        rho = spearman([p[0] for p in pairs], [p[1] for p in pairs])
        out[field] = {"description": desc, "n": len(pairs), "correlation": rho,
                      "predictive": None if rho is None else rho > 0.05}
    return out


def backtest(series: dict, horizon: int, hit_threshold: float,
             min_base: float = 10.0, metric: str = "abs") -> dict:
    """Score on day T vs interest change from T to the most recent observation.

    `min_base` drops keywords whose interest index was near zero at T. Without it
    a move from 1 to 3 reads as +200% and swamps everything — the median base
    across tracked keywords is under 7 on a 0-100 index.
    """
    dates = series["dates"]
    if len(dates) < 2:
        return {"samples": [], "horizon_days": horizon, "usable": False}

    last_day  = dates[-1]
    start_day = (date.fromisoformat(last_day) - timedelta(days=horizon)).isoformat()

    samples, dropped_low_base = [], 0
    for key, rec in series["keywords"].items():
        v_then, v_day = _value_at(rec["v"], dates, start_day)
        if v_then is None:
            continue
        ci_then, _ = _value_at(rec["ci"], dates, start_day)
        ci_now, ci_day = _latest(rec["ci"], dates)
        if ci_then is None or ci_now is None or ci_day == v_day:
            continue
        if float(ci_then) < min_base:
            dropped_low_base += 1
            continue

        delta = float(ci_now) - float(ci_then)
        forward = delta if metric == "abs" else delta / max(float(ci_then), 1.0) * 100.0
        g_then, _ = _value_at(rec["g"], dates, start_day)
        r_then, _ = _value_at(rec["r30"], dates, start_day)
        a_then, _ = _value_at(rec["arc"], dates, start_day)

        samples.append({
            "keyword":   rec["label"],
            "category":  rec.get("category"),
            "score_then": v_then,
            "score_day":  v_day,
            "interest_then": ci_then,
            "interest_now":  ci_now,
            "growth_at_score":   g_then,
            "interest_at_score": ci_then,
            "reddit_at_score":   r_then,
            "listings_at_score": a_then,
            "forward_change": round(forward, 1),
            "hit": forward >= hit_threshold,
        })

    return {"samples": samples, "horizon_days": horizon, "metric": metric,
            "min_base": min_base, "dropped_low_base": dropped_low_base,
            "hit_threshold": hit_threshold, "usable": len(samples) >= 30}


def derive_bands(scores: list[float]) -> list[dict]:
    """Percentile thresholds, so the top band is populated by construction."""
    if not scores:
        return [{"min": m, "label": l, "color": c}
                for m, (l, c, _) in zip([75, 60, 45, 30, 0], BAND_SHAPE)]

    ordered = sorted(scores, reverse=True)
    bands = []
    for label, color, pct in BAND_SHAPE:
        if label == "Not Recommended":
            bands.append({"min": 0, "label": label, "color": color})
            continue
        idx = min(len(ordered) - 1, max(0, int(len(ordered) * pct) - 1))
        bands.append({"min": int(ordered[idx]), "label": label, "color": color})

    # Thresholds must strictly decrease or the lookup in scoring.label_for breaks.
    for i in range(1, len(bands)):
        if bands[i]["min"] >= bands[i - 1]["min"]:
            bands[i]["min"] = max(0, bands[i - 1]["min"] - 1)
    return bands


def band_performance(samples: list[dict], bands: list[dict]) -> list[dict]:
    out = []
    for i, band in enumerate(bands):
        upper = bands[i - 1]["min"] if i > 0 else 101
        members = [s for s in samples if band["min"] <= s["score_then"] < upper]
        if not members:
            out.append({**band, "n": 0, "hit_rate": None, "median_change": None})
            continue
        hits = sum(1 for m in members if m["hit"])
        out.append({
            **band,
            "n": len(members),
            "hit_rate": round(hits / len(members) * 100, 1),
            "median_change": round(statistics.median(m["forward_change"] for m in members), 1),
        })
    return out


def load_user_outcomes() -> dict:
    """Real launch outcomes, if a database is configured."""
    try:
        from trendpulse import db
        if not db.is_available():
            return {"available": False, "reason": db.unavailable_reason(), "rows": []}
        rows = db.all_outcomes_for_calibration()
    except Exception as exc:
        return {"available": False, "reason": str(exc), "rows": []}

    good = {"worked", "hit"}
    scored = [r for r in rows if r.get("viability_at_decision") is not None]
    summary = {
        "available": True,
        "rows": len(rows),
        "scored_rows": len(scored),
        "hit_rate": (round(sum(1 for r in scored if r["result"] in good) / len(scored) * 100, 1)
                     if scored else None),
    }
    return summary


def build_track_record(samples: list[dict], top: int = 25) -> list[dict]:
    """Highest-scoring calls that subsequently rose — the public proof."""
    flagged = [s for s in samples if s["score_then"] >= 50 and s["hit"]]
    flagged.sort(key=lambda s: s["forward_change"], reverse=True)
    return [{
        "keyword":        s["keyword"],
        "category":       s["category"],
        "flagged_on":     s["score_day"],
        "score_at_flag":  s["score_then"],
        "interest_then":  s["interest_then"],
        "interest_now":   s["interest_now"],
        "change_pct":     s["forward_change"],
    } for s in flagged[:top]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=30,
                    help="days between the score and the outcome measurement")
    ap.add_argument("--hit-threshold", type=float, default=5.0,
                    help="forward interest change that counts as a hit")
    ap.add_argument("--min-base", type=float, default=10.0,
                    help="drop keywords whose interest index was below this at T")
    ap.add_argument("--metric", choices=("abs", "pct"), default="abs",
                    help="measure forward change in index points or percent")
    args = ap.parse_args()

    series = history.load_series()
    if not series:
        log.error("No history yet — run scripts/backfill_history.py first.")
        return 1

    bt = backtest(series, args.horizon, args.hit_threshold,
                  min_base=args.min_base, metric=args.metric)
    samples = bt["samples"]
    log.info("Backtest: %d usable samples over a %d-day horizon", len(samples), args.horizon)

    if not bt["usable"]:
        log.warning("Thin sample (%d) — bands will be derived but treat them as provisional.",
                    len(samples))

    # Bands come from the *current* score distribution, not the historical one.
    current = [v for v in
               (rec["v"][-1] if rec["v"] and rec["v"][-1] is not None else None
                for rec in series["keywords"].values())
               if v is not None]
    if not current:
        current = [s["score_then"] for s in samples]

    bands = derive_bands(current)
    perf  = band_performance(samples, bands)
    outcomes = load_user_outcomes()
    diagnostics = factor_diagnostics(samples)

    overall_hit = (round(sum(1 for s in samples if s["hit"]) / len(samples) * 100, 1)
                   if samples else None)
    # Does a higher score actually mean a better hit rate?
    rated = [p for p in perf if p["hit_rate"] is not None and p["n"] >= 5]
    monotonic = all(rated[i]["hit_rate"] >= rated[i + 1]["hit_rate"]
                    for i in range(len(rated) - 1)) if len(rated) > 1 else None

    payload = {
        "generated":      datetime.utcnow().isoformat(),
        "horizon_days":   args.horizon,
        "hit_threshold":  args.hit_threshold,
        "sample_size":    len(samples),
        "metric":         args.metric,
        "min_base":       args.min_base,
        "dropped_low_base": bt["dropped_low_base"],
        "factor_diagnostics": diagnostics,
        "history_days":   len(series["dates"]),
        "provisional":    not bt["usable"],
        "overall_hit_rate": overall_hit,
        "score_separates_outcomes": monotonic,
        "bands":          bands,
        "band_performance": perf,
        "user_outcomes":  outcomes,
        "track_record":   build_track_record(samples),
        "warning": (
            None if monotonic in (True, None) else
            "Higher scores did NOT precede stronger demand in this backtest — the "
            "relationship is inverted. The score is weighted toward momentum already "
            "achieved, so it tends to flag keywords at their peak, which then revert. "
            "Treat the bands as descriptive of the current distribution, not as a "
            "validated prediction, until the factor weights are retrained."
        ),
        "notes": (
            "Bands are percentile thresholds over the current score distribution. "
            "Hit rate is the share of keywords whose Google Trends interest rose by "
            f"at least {args.hit_threshold:g}% over the following {args.horizon} days. "
            "This measures demand direction, not revenue — revenue calibration needs "
            "user-reported outcomes, which accumulate once accounts are in use."
        ),
    }

    CALIBRATION_FILE.write_text(json.dumps(payload, indent=2))
    log.info("Wrote %s", CALIBRATION_FILE)
    log.info("Bands: %s", ", ".join(f"{b['label']}≥{b['min']}" for b in bands))
    for p in perf:
        log.info("  %-20s n=%-4d hit_rate=%s median_change=%s",
                 p["label"], p["n"], p["hit_rate"], p["median_change"])
    log.info("Overall hit rate: %s%% | score separates outcomes: %s", overall_hit, monotonic)
    log.info("Factor diagnostics (Spearman vs forward demand):")
    for field, d in diagnostics.items():
        log.info("  %-18s rho=%-7s n=%-4d %s", field, d["correlation"], d["n"],
                 "" if d.get("predictive") is None else
                 ("predictive" if d["predictive"] else "NOT predictive"))
    if payload.get("warning"):
        log.warning("%s", payload["warning"])
    if not outcomes.get("available"):
        log.info("User outcomes: none yet (%s)", outcomes.get("reason", "no rows"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
