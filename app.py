"""
TrendPulse – Flask backend
Fetches Google Trends data via pytrends, caches to disk, serves JSON to frontend.
"""

import json
import os
import time
import logging
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path

from flask import Flask, jsonify, send_from_directory
from pytrends.request import TrendReq
from apscheduler.schedulers.background import BackgroundScheduler

# ── Config ────────────────────────────────────────────────────────────────────
CACHE_FILE   = Path("cache/trends.json")
CACHE_TTL_H  = 24          # refresh every 24 hours
GEO          = "US"        # change to "" for worldwide, "GB" for UK, etc.
TIMEFRAME    = "today 12-m"

# Keywords to track – edit this list freely
TRACKED_KEYWORDS = [
    "Creatine Gummies",
    "AI Smart Ring",
    "Beef Tallow Skincare",
    "Portable Sauna Blanket",
    "Mushroom Coffee",
    "Water Bottle with Filter",
    "Peptide Face Serum",
    "Grounding Mat",
    "Freeze Dryer Home",
    "Dog Probiotic Chews",
    "LED Face Mask",
    "Linen Clothing",
    "Mini Projector",
    "Collagen Peptides Powder",
    "Hydrogen Water Bottle",
    "Electric Skates",
    "Niacinamide Serum",
    "Portable Blender",
    "Gut Health Test Kit",
    "Lash Serum",
    "Barefoot Running Shoes",
    "Cat Water Fountain",
    "Air Fryer Accessories",
    "Whoop Band Alternative",
]

# Category map – used for badge display in frontend
CATEGORY_MAP = {
    "Creatine Gummies":        "Health & Wellness",
    "AI Smart Ring":           "Tech & Gadgets",
    "Beef Tallow Skincare":    "Beauty",
    "Portable Sauna Blanket":  "Fitness",
    "Mushroom Coffee":         "Health & Wellness",
    "Water Bottle with Filter":"Home & Kitchen",
    "Peptide Face Serum":      "Beauty",
    "Grounding Mat":           "Health & Wellness",
    "Freeze Dryer Home":       "Home & Kitchen",
    "Dog Probiotic Chews":     "Pets",
    "LED Face Mask":           "Beauty",
    "Linen Clothing":          "Fashion",
    "Mini Projector":          "Tech & Gadgets",
    "Collagen Peptides Powder":"Health & Wellness",
    "Hydrogen Water Bottle":   "Health & Wellness",
    "Electric Skates":         "Tech & Gadgets",
    "Niacinamide Serum":       "Beauty",
    "Portable Blender":        "Fitness",
    "Gut Health Test Kit":     "Health & Wellness",
    "Lash Serum":              "Beauty",
    "Barefoot Running Shoes":  "Fitness",
    "Cat Water Fountain":      "Pets",
    "Air Fryer Accessories":   "Home & Kitchen",
    "Whoop Band Alternative":  "Fitness",
}

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder="static")


# ── Trend fetching ────────────────────────────────────────────────────────────
def classify_status(growth_pct: float) -> str:
    if growth_pct >= 1000:
        return "breakout"
    if growth_pct >= 200:
        return "hot"
    return "rising"


def fetch_trends_for_batch(pytrends: TrendReq, batch: list[str]) -> dict:
    """Fetch interest-over-time for up to 5 keywords (pytrends limit)."""
    results = {}
    try:
        pytrends.build_payload(batch, timeframe=TIMEFRAME, geo=GEO)
        df = pytrends.interest_over_time()
        if df.empty:
            log.warning("Empty dataframe for batch: %s", batch)
            return results
        # Drop the 'isPartial' column if present
        if "isPartial" in df.columns:
            df = df.drop(columns=["isPartial"])
        log.info("Returned columns: %s", list(df.columns))
        # Resample to monthly and take last 12 months
        try:
            monthly = df.resample("ME").mean().tail(12)
        except Exception:
            monthly = df.resample("M").mean().tail(12)
        # Build a lowercase lookup so matching is case-insensitive
        col_map = {c.lower(): c for c in monthly.columns}
        for kw in batch:
            col = col_map.get(kw.lower())
            if col is not None:
                series = monthly[col].fillna(0).tolist()
                results[kw] = [round(v, 1) for v in series]
                log.info("  ✓ %s → %s", kw, [round(v) for v in series])
            else:
                log.warning("  ✗ '%s' not found in columns %s", kw, list(monthly.columns))
    except Exception as exc:
        if "429" in str(exc):
            log.warning("Rate limited (429) — waiting 30s then retrying batch")
            time.sleep(30)
            try:
                pytrends.build_payload(batch, timeframe=TIMEFRAME, geo=GEO)
                df = pytrends.interest_over_time()
                if not df.empty:
                    if "isPartial" in df.columns:
                        df = df.drop(columns=["isPartial"])
                    try:
                        monthly = df.resample("ME").mean().tail(12)
                    except Exception:
                        monthly = df.resample("M").mean().tail(12)
                    col_map = {c.lower(): c for c in monthly.columns}
                    for kw in batch:
                        col = col_map.get(kw.lower())
                        if col is not None:
                            series = monthly[col].fillna(0).tolist()
                            results[kw] = [round(v, 1) for v in series]
                            log.info("  ✓ (retry) %s", kw)
            except Exception as retry_exc:
                log.warning("Retry also failed: %s", retry_exc)
        else:
            log.warning("Batch fetch failed: %s", exc)
    return results


def compute_growth(series: list[float]) -> float:
    """Growth from first non-zero value to last value."""
    non_zero = [v for v in series if v > 0]
    if len(non_zero) < 2:
        return 0.0
    start = non_zero[0]
    end   = series[-1]
    if start == 0:
        return 0.0
    return round(((end - start) / start) * 100, 1)


def trend_score(series: list[float], growth: float) -> int:
    """Simple 0-100 score combining recency and growth momentum."""
    if not series:
        return 0
    recency   = series[-1]               # current interest (0-100)
    momentum  = min(growth / 50, 100)    # cap at 100
    score     = 0.6 * recency + 0.4 * momentum
    return min(100, max(0, round(score)))


def fetch_all_trends() -> list[dict]:
    """Fetch trends for all tracked keywords in batches of 5."""
    pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
    raw = {}

    # pytrends max 5 keywords per request
    batch_size = 5
    for i in range(0, len(TRACKED_KEYWORDS), batch_size):
        batch = TRACKED_KEYWORDS[i:i + batch_size]
        log.info("Fetching batch %d/%d: %s", i // batch_size + 1,
                 -(-len(TRACKED_KEYWORDS) // batch_size), batch)
        batch_result = fetch_trends_for_batch(pytrends, batch)
        raw.update(batch_result)
        time.sleep(8)   # be polite to Google's servers

    keywords_out = []
    for kw in TRACKED_KEYWORDS:
        series = raw.get(kw, [50] * 12)   # fallback flat line if fetch failed
        growth = compute_growth(series)
        keywords_out.append({
            "id":       TRACKED_KEYWORDS.index(kw) + 1,
            "keyword":  kw,
            "category": CATEGORY_MAP.get(kw, "General"),
            "status":   classify_status(growth),
            "growth":   growth,
            "score":    trend_score(series, growth),
            "trend":    series,
            "fetched":  datetime.utcnow().isoformat(),
        })

    # Sort by growth descending
    keywords_out.sort(key=lambda k: k["growth"], reverse=True)
    return keywords_out


def load_cache() -> Optional[dict]:
    """Return cached data if fresh, else None."""
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text())
        fetched_at = datetime.fromisoformat(data["fetched_at"])
        if datetime.utcnow() - fetched_at < timedelta(hours=CACHE_TTL_H):
            return data
    except Exception:
        pass
    return None


def save_cache(keywords: list[dict]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.utcnow().isoformat(),
        "keywords": keywords,
    }
    CACHE_FILE.write_text(json.dumps(payload, indent=2))
    log.info("Cache saved (%d keywords)", len(keywords))


def refresh_cache() -> None:
    log.info("Refreshing trend cache…")
    try:
        keywords = fetch_all_trends()
        save_cache(keywords)
    except Exception as exc:
        log.error("Cache refresh failed: %s", exc)


def get_trends() -> list[dict]:
    """Return trends from cache, refreshing if stale/missing."""
    cached = load_cache()
    if cached:
        log.info("Serving from cache (fetched %s)", cached["fetched_at"])
        return cached["keywords"]
    log.info("Cache miss — fetching live data")
    keywords = fetch_all_trends()
    save_cache(keywords)
    return keywords


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/trends")
def api_trends():
    try:
        data = get_trends()
        return jsonify({"ok": True, "keywords": data})
    except Exception as exc:
        log.error("API error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """Manually trigger a cache refresh (call from admin/cron)."""
    try:
        refresh_cache()
        return jsonify({"ok": True, "message": "Cache refreshed"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/debug")
def api_debug():
    """Show raw cache contents for troubleshooting."""
    if not CACHE_FILE.exists():
        return jsonify({"ok": False, "error": "No cache file yet"})
    return jsonify(json.loads(CACHE_FILE.read_text()))


@app.route("/api/status")
def api_status():
    cached = load_cache()
    return jsonify({
        "ok": True,
        "cache": {
            "exists": cached is not None,
            "fetched_at": cached["fetched_at"] if cached else None,
            "keyword_count": len(cached["keywords"]) if cached else 0,
        }
    })


# ── Scheduler: auto-refresh every 24 h ───────────────────────────────────────
scheduler = BackgroundScheduler()
scheduler.add_job(refresh_cache, "interval", hours=CACHE_TTL_H, id="trend_refresh")
scheduler.start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    # Warm up cache on startup (runs in background so server starts fast)
    if not load_cache():
        import threading
        threading.Thread(target=refresh_cache, daemon=True).start()
    app.run(host="0.0.0.0", port=port, debug=False)
