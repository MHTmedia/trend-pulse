"""Append-only time-series store for TrendPulse.

The nightly job used to overwrite cache/trends.json, so the app could only ever
answer "what does this keyword look like today". This module keeps every night's
observation instead, which is the one asset a competitor with identical public
inputs cannot reconstruct after the fact.

Layout:
    cache/history/snapshots/YYYY-MM-DD.json.gz   full-fidelity rows, append-only
    cache/history/series.json                    rolled-up series (server-side)
    cache/history/movers.json                    precomputed movers (served)

Keywords are keyed by their normalised name, never by `id` — ids in trends.json
are positional and get reassigned every night when the list is re-sorted.
"""

from __future__ import annotations

import gzip
import json
import logging
from datetime import date, datetime, timedelta
from typing import Iterator, Optional

from .paths import HISTORY_DIR, MOVERS_FILE, SERIES_FILE, SNAPSHOT_DIR

DELTAS_FILE = HISTORY_DIR / "deltas.json"

log = logging.getLogger(__name__)

# How much history the rolled-up series keeps. Snapshots are kept forever; this
# window only bounds the file the app reads at request time.
SERIES_WINDOW_DAYS = 400

# Fields carried into each snapshot row, as (output_key, source_key).
_ROW_FIELDS = [
    ("cat", "category"),
    ("st",  "status"),
    ("v",   "viability"),
    ("s",   "score"),
    ("g",   "growth"),
    ("r30", "reddit_30d"),
    ("rv",  "reddit_velocity"),
    ("arc", "amazon_result_count"),
    ("ap",  "amazon_avg_price"),
    ("ar",  "amazon_avg_rating"),
]


def norm(keyword: str) -> str:
    """Stable identity for a keyword across nights."""
    return " ".join(str(keyword).strip().lower().split())


# ── Writing ───────────────────────────────────────────────────────────────────

def snapshot_path(day: str):
    return SNAPSHOT_DIR / f"{day}.json.gz"


def _row(entry: dict) -> dict:
    row = {"k": entry.get("keyword"), "label": entry.get("keyword")}
    for out_key, src_key in _ROW_FIELDS:
        row[out_key] = entry.get(src_key)
    # Current interest = last point of the 12-month Google Trends series.
    series = entry.get("trend") or []
    row["ci"] = series[-1] if series else None
    return row


def write_snapshot(keywords: list[dict], day: Optional[str] = None,
                   source: str = "nightly") -> str:
    """Persist one night's observations. Re-writing the same day overwrites it."""
    day = day or datetime.utcnow().date().isoformat()
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "date":    day,
        "source":  source,
        "written": datetime.utcnow().isoformat(),
        "rows":    [_row(k) for k in keywords],
    }
    with gzip.open(snapshot_path(day), "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))

    log.info("history: wrote snapshot %s (%d rows, source=%s)", day, len(payload["rows"]), source)
    return day


def snapshot_days() -> list[str]:
    if not SNAPSHOT_DIR.exists():
        return []
    return sorted(p.name[: -len(".json.gz")] for p in SNAPSHOT_DIR.glob("*.json.gz"))


def read_snapshot(day: str) -> Optional[dict]:
    path = snapshot_path(day)
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        log.warning("history: unreadable snapshot %s", day)
        return None


def iter_snapshots(since: Optional[str] = None) -> Iterator[dict]:
    for day in snapshot_days():
        if since and day < since:
            continue
        snap = read_snapshot(day)
        if snap:
            yield snap


# ── Rolling up ────────────────────────────────────────────────────────────────

# Series kept per keyword. Everything else stays in the snapshots, which are the
# archival record used for backtesting.
_SERIES_KEYS = ["v", "s", "g", "ci", "r30", "arc"]


def rebuild_series(window_days: int = SERIES_WINDOW_DAYS) -> dict:
    """Collapse all snapshots into one per-keyword series file."""
    cutoff = (date.today() - timedelta(days=window_days)).isoformat()
    days: list[str] = []
    keywords: dict[str, dict] = {}

    for snap in iter_snapshots(since=cutoff):
        day = snap["date"]
        idx = len(days)
        days.append(day)

        for row in snap.get("rows", []):
            if not row.get("k"):
                continue
            key = norm(row["k"])
            rec = keywords.get(key)
            if rec is None:
                rec = keywords[key] = {
                    "label":    row.get("label") or row["k"],
                    "category": row.get("cat"),
                    "first_seen": day,
                    **{k: [] for k in _SERIES_KEYS},
                }
            # Backfill gaps so every series lines up with `days` by index.
            for k in _SERIES_KEYS:
                arr = rec[k]
                while len(arr) < idx:
                    arr.append(None)
                arr.append(row.get(k))
            rec["last_seen"] = day
            if row.get("cat"):
                rec["category"] = row["cat"]
            if row.get("st"):
                rec["status"] = row["st"]

    # Pad trailing gaps for keywords that dropped out before the last snapshot.
    total = len(days)
    for rec in keywords.values():
        for k in _SERIES_KEYS:
            arr = rec[k]
            while len(arr) < total:
                arr.append(None)

    payload = {
        "updated":  datetime.utcnow().isoformat(),
        "dates":    days,
        "keywords": keywords,
    }
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    SERIES_FILE.write_text(json.dumps(payload, separators=(",", ":")))
    log.info("history: rebuilt series — %d keywords across %d days", len(keywords), total)
    return payload


def load_series() -> Optional[dict]:
    if not SERIES_FILE.exists():
        return None
    try:
        return json.loads(SERIES_FILE.read_text())
    except Exception:
        return None


# ── Reads used by the API ─────────────────────────────────────────────────────

def keyword_history(keyword: str, series: Optional[dict] = None) -> Optional[dict]:
    """Full observed history for one keyword, as date/value pairs."""
    series = series or load_series()
    if not series:
        return None
    rec = series["keywords"].get(norm(keyword))
    if not rec:
        return None

    dates = series["dates"]
    points = []
    for i, day in enumerate(dates):
        if rec["v"][i] is None and rec["s"][i] is None:
            continue   # keyword wasn't tracked yet, or was skipped that night
        points.append({
            "date":       day,
            "viability":  rec["v"][i],
            "score":      rec["s"][i],
            "growth":     rec["g"][i],
            "interest":   rec["ci"][i],
            "reddit_30d": rec["r30"][i],
            "listings":   rec["arc"][i],
        })

    return {
        "keyword":    rec["label"],
        "category":   rec.get("category"),
        "status":     rec.get("status"),
        "first_seen": rec.get("first_seen"),
        "last_seen":  rec.get("last_seen"),
        "points":     points,
    }


def _last_two(arr: list, dates: list[str], window_days: int):
    """Latest value, and the newest value at least `window_days` older than it."""
    latest_i = next((i for i in range(len(arr) - 1, -1, -1) if arr[i] is not None), None)
    if latest_i is None:
        return None, None, None, None

    latest_day = date.fromisoformat(dates[latest_i])
    target = latest_day - timedelta(days=window_days)
    prior_i = None
    for i in range(latest_i - 1, -1, -1):
        if arr[i] is None:
            continue
        if date.fromisoformat(dates[i]) <= target:
            prior_i = i
            break
    if prior_i is None:
        return arr[latest_i], None, dates[latest_i], None
    return arr[latest_i], arr[prior_i], dates[latest_i], dates[prior_i]


def compute_movers(window_days: int = 7, top: int = 40,
                   series: Optional[dict] = None) -> dict:
    """Biggest viability gainers and losers over the window."""
    series = series or load_series()
    if not series:
        return {"window_days": window_days, "risers": [], "fallers": [], "coverage": 0}

    dates = series["dates"]
    moves = []
    for key, rec in series["keywords"].items():
        now, then, now_day, then_day = _last_two(rec["v"], dates, window_days)
        if now is None or then is None:
            continue
        delta = now - then
        if delta == 0:
            continue
        score_now, score_then, _, _ = _last_two(rec["s"], dates, window_days)
        moves.append({
            "keyword":     rec["label"],
            "category":    rec.get("category"),
            "status":      rec.get("status"),
            "viability":   now,
            "prev":        then,
            "delta":       round(delta, 1),
            "score":       score_now,
            "score_delta": (round(score_now - score_then, 1)
                            if score_now is not None and score_then is not None else None),
            "from_date":   then_day,
            "to_date":     now_day,
        })

    moves.sort(key=lambda m: m["delta"], reverse=True)
    return {
        "window_days": window_days,
        "generated":   datetime.utcnow().isoformat(),
        "coverage":    len(moves),
        "risers":      [m for m in moves if m["delta"] > 0][:top],
        "fallers":     [m for m in moves if m["delta"] < 0][-top:][::-1],
    }


def rebuild_movers(windows=(7, 30), top: int = 40, series: Optional[dict] = None) -> dict:
    series = series or load_series()
    payload = {
        "generated": datetime.utcnow().isoformat(),
        "windows":   {str(w): compute_movers(w, top, series) for w in windows},
    }
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    MOVERS_FILE.write_text(json.dumps(payload, separators=(",", ":")))
    return payload


def load_movers() -> Optional[dict]:
    if not MOVERS_FILE.exists():
        return None
    try:
        return json.loads(MOVERS_FILE.read_text())
    except Exception:
        return None


def coverage() -> dict:
    """Summary of how deep the proprietary history now runs.

    Falls back to the rolled-up series because the raw snapshots are excluded
    from the deployed bundle — they grow without bound and the app never reads
    them directly.
    """
    days = snapshot_days()
    series = load_series()
    if not days and series:
        days = series.get("dates", [])
    return {
        "snapshot_count": len(days),
        "first_date":     days[0] if days else None,
        "last_date":      days[-1] if days else None,
        "keyword_count":  len(series["keywords"]) if series else 0,
    }


def rebuild_deltas(series: Optional[dict] = None) -> dict:
    """Per-keyword trajectory summary, small enough to ship with every page load.

    Without this the dashboard would need one request per card to answer
    "is this rising or cooling", which is the whole point of keeping history.
    """
    series = series or load_series()
    if not series:
        return {}

    dates = series["dates"]
    out: dict[str, dict] = {}
    for key, rec in series["keywords"].items():
        now, wk, _, _ = _last_two(rec["v"], dates, 7)
        _, mo, _, _   = _last_two(rec["v"], dates, 30)
        observed = sum(1 for v in rec["v"] if v is not None)
        if now is None:
            continue
        out[key] = {
            "viability":   now,
            "d7":          round(now - wk, 1) if wk is not None else None,
            "d30":         round(now - mo, 1) if mo is not None else None,
            "first_seen":  rec.get("first_seen"),
            "days_tracked": observed,
        }

    payload = {"generated": datetime.utcnow().isoformat(), "keywords": out}
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    DELTAS_FILE.write_text(json.dumps(payload, separators=(",", ":")))
    log.info("history: rebuilt deltas for %d keywords", len(out))
    return payload


def load_deltas() -> dict:
    if not DELTAS_FILE.exists():
        return {}
    try:
        return json.loads(DELTAS_FILE.read_text()).get("keywords", {})
    except Exception:
        return {}
