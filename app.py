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
# Fetched in staggered batches of 5 with 12s delay to avoid Google rate limits.
# ~100 keywords takes ~4 minutes to fetch. Refreshes every 24 hours.
TRACKED_KEYWORDS = [
    # ── Health & Wellness ──────────────────────────────────────────────────────
    "Creatine Gummies",
    "Mushroom Coffee",
    "Collagen Peptides Powder",
    "Hydrogen Water Bottle",
    "Grounding Mat",
    "Gut Health Test Kit",
    "Magnesium Glycinate",
    "Berberine Supplement",
    "Methylene Blue Supplement",
    "Shilajit Supplement",
    "Sea Moss Gel",
    "Tallow Balm",
    "Beef Liver Supplement",
    "Electrolyte Powder",
    "Adaptogen Supplements",
    "NAD Supplement",
    "Spermidine Supplement",
    "Peptide Supplement",
    "Urolithin A Supplement",
    "Mouth Tape Sleep",

    # ── Beauty ────────────────────────────────────────────────────────────────
    "Beef Tallow Skincare",
    "Peptide Face Serum",
    "Niacinamide Serum",
    "Lash Serum",
    "LED Face Mask",
    "Retinol Alternative",
    "Snail Mucin Serum",
    "Slugging Skincare",
    "Facial Gua Sha",
    "Ice Roller Face",
    "Lip Filler Alternative",
    "Glass Skin Routine",
    "Body Sunscreen SPF",
    "Scalp Serum Hair",
    "Rosemary Oil Hair Growth",
    "Hair Gloss Treatment",
    "Barrier Repair Moisturizer",
    "Blue Light Glasses",
    "Microcurrent Face Device",
    "RF Skin Tightening Device",

    # ── Fitness ───────────────────────────────────────────────────────────────
    "Portable Sauna Blanket",
    "Portable Blender",
    "Barefoot Running Shoes",
    "Whoop Band Alternative",
    "Weighted Vest",
    "Zone 2 Training Monitor",
    "Cold Plunge Tub",
    "Sauna Tent",
    "Walking Pad Treadmill",
    "Pull Up Bar Doorframe",
    "Resistance Band Set",
    "Massage Gun",
    "Incline Treadmill Walking",
    "Pilates Reformer Home",
    "Rucking Backpack",
    "Battle Rope",
    "Adjustable Dumbbell Set",
    "Gymnastic Rings",
    "Vibration Plate",
    "Foam Roller Electric",

    # ── Tech & Gadgets ────────────────────────────────────────────────────────
    "AI Smart Ring",
    "Electric Skates",
    "Mini Projector",
    "AI Pin Wearable",
    "Foldable Phone Case",
    "Portable Power Station",
    "Solar Panel Charger",
    "Smart Home Hub",
    "Robot Vacuum Mop Combo",
    "Air Quality Monitor",
    "Wireless Earbuds",
    "Dashcam 4K",
    "Action Camera",
    "Thermal Camera Phone",
    "Smart Glasses",
    "Portable Monitor",
    "Mechanical Keyboard",
    "Standing Desk Mat",
    "Cable Management Box",
    "Magnetic Phone Mount",

    # ── Home & Kitchen ────────────────────────────────────────────────────────
    "Water Bottle with Filter",
    "Freeze Dryer Home",
    "Air Fryer Accessories",
    "Countertop Dishwasher",
    "Sous Vide Machine",
    "Beeswax Food Wraps",
    "Dutch Oven Cast Iron",
    "Bread Maker Machine",
    "Espresso Machine Home",
    "Mushroom Growing Kit",
    "Compost Bin Kitchen",
    "Water Kefir Kit",
    "Fermentation Crock",
    "Dehydrator Machine",
    "Silicone Baking Mats",
    "Oil Dispenser Bottle",
    "Bamboo Cutting Board",
    "Reusable Produce Bags",
    "Smart Thermostat",
    "Cordless Vacuum",

    # ── Pets ──────────────────────────────────────────────────────────────────
    "Dog Probiotic Chews",
    "Cat Water Fountain",
    "Raw Dog Food",
    "Dog Anxiety Vest",
    "Cat GPS Tracker",
    "Automatic Cat Feeder",
    "Dog DNA Test Kit",
    "Pet Camera Treat Dispenser",
    "Freeze Dried Dog Food",
    "Orthopedic Dog Bed",

    # ── Fashion & Apparel ─────────────────────────────────────────────────────
    "Linen Clothing",
    "Merino Wool Base Layer",
    "Wide Leg Pants",
    "Compression Socks",
    "Bamboo Pajamas",
    "Tactical Pants",
    "Minimalist Sneakers",
    "Crossbody Bag",
    "Bucket Hat",
    "Swim Shorts Quick Dry",
]

# Category map – used for badge display in frontend
CATEGORY_MAP = {
    # Health & Wellness
    "Creatine Gummies":          "Health & Wellness",
    "Mushroom Coffee":           "Health & Wellness",
    "Collagen Peptides Powder":  "Health & Wellness",
    "Hydrogen Water Bottle":     "Health & Wellness",
    "Grounding Mat":             "Health & Wellness",
    "Gut Health Test Kit":       "Health & Wellness",
    "Magnesium Glycinate":       "Health & Wellness",
    "Berberine Supplement":      "Health & Wellness",
    "Methylene Blue Supplement": "Health & Wellness",
    "Shilajit Supplement":       "Health & Wellness",
    "Sea Moss Gel":              "Health & Wellness",
    "Tallow Balm":               "Health & Wellness",
    "Beef Liver Supplement":     "Health & Wellness",
    "Electrolyte Powder":        "Health & Wellness",
    "Adaptogen Supplements":     "Health & Wellness",
    "NAD Supplement":            "Health & Wellness",
    "Spermidine Supplement":     "Health & Wellness",
    "Peptide Supplement":        "Health & Wellness",
    "Urolithin A Supplement":    "Health & Wellness",
    "Mouth Tape Sleep":          "Health & Wellness",
    # Beauty
    "Beef Tallow Skincare":      "Beauty",
    "Peptide Face Serum":        "Beauty",
    "Niacinamide Serum":         "Beauty",
    "Lash Serum":                "Beauty",
    "LED Face Mask":             "Beauty",
    "Retinol Alternative":       "Beauty",
    "Snail Mucin Serum":         "Beauty",
    "Slugging Skincare":         "Beauty",
    "Facial Gua Sha":            "Beauty",
    "Ice Roller Face":           "Beauty",
    "Lip Filler Alternative":    "Beauty",
    "Glass Skin Routine":        "Beauty",
    "Body Sunscreen SPF":        "Beauty",
    "Scalp Serum Hair":          "Beauty",
    "Rosemary Oil Hair Growth":  "Beauty",
    "Hair Gloss Treatment":      "Beauty",
    "Barrier Repair Moisturizer":"Beauty",
    "Blue Light Glasses":        "Beauty",
    "Microcurrent Face Device":  "Beauty",
    "RF Skin Tightening Device": "Beauty",
    # Fitness
    "Portable Sauna Blanket":    "Fitness",
    "Portable Blender":          "Fitness",
    "Barefoot Running Shoes":    "Fitness",
    "Whoop Band Alternative":    "Fitness",
    "Weighted Vest":             "Fitness",
    "Zone 2 Training Monitor":   "Fitness",
    "Cold Plunge Tub":           "Fitness",
    "Sauna Tent":                "Fitness",
    "Walking Pad Treadmill":     "Fitness",
    "Pull Up Bar Doorframe":     "Fitness",
    "Resistance Band Set":       "Fitness",
    "Massage Gun":               "Fitness",
    "Incline Treadmill Walking": "Fitness",
    "Pilates Reformer Home":     "Fitness",
    "Rucking Backpack":          "Fitness",
    "Battle Rope":               "Fitness",
    "Adjustable Dumbbell Set":   "Fitness",
    "Gymnastic Rings":           "Fitness",
    "Vibration Plate":           "Fitness",
    "Foam Roller Electric":      "Fitness",
    # Tech & Gadgets
    "AI Smart Ring":             "Tech & Gadgets",
    "Electric Skates":           "Tech & Gadgets",
    "Mini Projector":            "Tech & Gadgets",
    "AI Pin Wearable":           "Tech & Gadgets",
    "Foldable Phone Case":       "Tech & Gadgets",
    "Portable Power Station":    "Tech & Gadgets",
    "Solar Panel Charger":       "Tech & Gadgets",
    "Smart Home Hub":            "Tech & Gadgets",
    "Robot Vacuum Mop Combo":    "Tech & Gadgets",
    "Air Quality Monitor":       "Tech & Gadgets",
    "Wireless Earbuds":          "Tech & Gadgets",
    "Dashcam 4K":                "Tech & Gadgets",
    "Action Camera":             "Tech & Gadgets",
    "Thermal Camera Phone":      "Tech & Gadgets",
    "Smart Glasses":             "Tech & Gadgets",
    "Portable Monitor":          "Tech & Gadgets",
    "Mechanical Keyboard":       "Tech & Gadgets",
    "Standing Desk Mat":         "Tech & Gadgets",
    "Cable Management Box":      "Tech & Gadgets",
    "Magnetic Phone Mount":      "Tech & Gadgets",
    # Home & Kitchen
    "Water Bottle with Filter":  "Home & Kitchen",
    "Freeze Dryer Home":         "Home & Kitchen",
    "Air Fryer Accessories":     "Home & Kitchen",
    "Countertop Dishwasher":     "Home & Kitchen",
    "Sous Vide Machine":         "Home & Kitchen",
    "Beeswax Food Wraps":        "Home & Kitchen",
    "Dutch Oven Cast Iron":      "Home & Kitchen",
    "Bread Maker Machine":       "Home & Kitchen",
    "Espresso Machine Home":     "Home & Kitchen",
    "Mushroom Growing Kit":      "Home & Kitchen",
    "Compost Bin Kitchen":       "Home & Kitchen",
    "Water Kefir Kit":           "Home & Kitchen",
    "Fermentation Crock":        "Home & Kitchen",
    "Dehydrator Machine":        "Home & Kitchen",
    "Silicone Baking Mats":      "Home & Kitchen",
    "Oil Dispenser Bottle":      "Home & Kitchen",
    "Bamboo Cutting Board":      "Home & Kitchen",
    "Reusable Produce Bags":     "Home & Kitchen",
    "Smart Thermostat":          "Home & Kitchen",
    "Cordless Vacuum":           "Home & Kitchen",
    # Pets
    "Dog Probiotic Chews":       "Pets",
    "Cat Water Fountain":        "Pets",
    "Raw Dog Food":              "Pets",
    "Dog Anxiety Vest":          "Pets",
    "Cat GPS Tracker":           "Pets",
    "Automatic Cat Feeder":      "Pets",
    "Dog DNA Test Kit":          "Pets",
    "Pet Camera Treat Dispenser":"Pets",
    "Freeze Dried Dog Food":     "Pets",
    "Orthopedic Dog Bed":        "Pets",
    # Fashion & Apparel
    "Linen Clothing":            "Fashion",
    "Merino Wool Base Layer":    "Fashion",
    "Wide Leg Pants":            "Fashion",
    "Compression Socks":         "Fashion",
    "Bamboo Pajamas":            "Fashion",
    "Tactical Pants":            "Fashion",
    "Minimalist Sneakers":       "Fashion",
    "Crossbody Bag":             "Fashion",
    "Bucket Hat":                "Fashion",
    "Swim Shorts Quick Dry":     "Fashion",
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


PROGRESS_FILE = Path("cache/progress.json")

def write_progress(current: int, total: int, status: str, label: str) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(json.dumps({
        "current": current, "total": total, "status": status, "label": label
    }))

def read_progress() -> dict:
    try:
        if PROGRESS_FILE.exists():
            return json.loads(PROGRESS_FILE.read_text())
    except Exception:
        pass
    return {"current": 0, "total": 0, "status": "idle", "label": ""}


def fetch_all_trends() -> list[dict]:
    """Fetch trends for all tracked keywords in batches of 5."""
    global fetch_progress
    pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
    raw = {}

    # pytrends max 5 keywords per request
    batch_size = 5
    total_batches = -(-len(TRACKED_KEYWORDS) // batch_size)
    write_progress(0, total_batches, "fetching", "Starting…")

    for i in range(0, len(TRACKED_KEYWORDS), batch_size):
        batch = TRACKED_KEYWORDS[i:i + batch_size]
        batch_num = i // batch_size + 1
        write_progress(batch_num, total_batches, "fetching", f"Fetching batch {batch_num} of {total_batches}…")
        log.info("Fetching batch %d/%d: %s", batch_num, total_batches, batch)
        batch_result = fetch_trends_for_batch(pytrends, batch)
        raw.update(batch_result)
        if batch_num < total_batches:
            time.sleep(12)  # be polite to Google's servers

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
    write_progress(total_batches, total_batches, "done", "Done")
    return keywords_out


def load_cache(ignore_ttl: bool = False) -> Optional[dict]:
    """Return cached data if fresh (or any cache if ignore_ttl=True), else None."""
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text())
        fetched_at = datetime.fromisoformat(data["fetched_at"])
        if ignore_ttl or datetime.utcnow() - fetched_at < timedelta(hours=CACHE_TTL_H):
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


def get_trends() -> tuple[list[dict], Optional[str]]:
    """Return (keywords, stale_since) — stale_since is set if serving old cache."""
    cached = load_cache()
    if cached:
        log.info("Serving from cache (fetched %s)", cached["fetched_at"])
        return cached["keywords"], None
    # Try live fetch
    try:
        log.info("Cache miss — fetching live data")
        keywords = fetch_all_trends()
        save_cache(keywords)
        return keywords, None
    except Exception as exc:
        log.error("Live fetch failed: %s", exc)
        # Fall back to stale cache if it exists
        stale = load_cache(ignore_ttl=True)
        if stale:
            log.warning("Serving stale cache from %s", stale["fetched_at"])
            return stale["keywords"], stale["fetched_at"]
        raise  # Nothing to fall back to


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/trends")
def api_trends():
    try:
        keywords, stale_since = get_trends()
        return jsonify({
            "ok": True,
            "keywords": keywords,
            "stale_since": stale_since,  # None = fresh, ISO string = stale fallback
        })
    except Exception as exc:
        log.error("API error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """Kick off a cache refresh in a background thread and return immediately."""
    current = read_progress()
    if current.get("status") == "fetching":
        return jsonify({"ok": True, "message": "Already fetching"})
    write_progress(0, 0, "fetching", "Starting…")
    import threading
    threading.Thread(target=refresh_cache, daemon=True).start()
    return jsonify({"ok": True, "message": "Refresh started"})


@app.route("/api/progress")
def api_progress():
    return jsonify(read_progress())


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
