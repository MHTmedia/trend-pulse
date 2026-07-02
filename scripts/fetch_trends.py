"""
Standalone script to fetch Google Trends data and write cache/trends.json.
Run locally or via GitHub Actions — NOT from Railway (datacenter IPs get blocked).

Usage:
    pip install pytrends
    python scripts/fetch_trends.py
"""

import json
import time
import logging
from datetime import datetime
from pathlib import Path

from pytrends.request import TrendReq

# ── Config (must match app.py) ────────────────────────────────────────────────
CACHE_FILE = Path("cache/trends.json")
GEO        = "US"
TIMEFRAME  = "today 12-m"

TRACKED_KEYWORDS = [
    # ── Health & Wellness
    "Creatine Gummies", "Mushroom Coffee", "Collagen Peptides Powder",
    "Hydrogen Water Bottle", "Grounding Mat", "Gut Health Test Kit",
    "Magnesium Glycinate", "Berberine Supplement", "Methylene Blue Supplement",
    "Shilajit Supplement", "Sea Moss Gel", "Tallow Balm", "Beef Liver Supplement",
    "Electrolyte Powder", "Adaptogen Supplements", "NAD Supplement",
    "Spermidine Supplement", "Peptide Supplement", "Urolithin A Supplement",
    "Mouth Tape Sleep",
    # ── Beauty
    "Beef Tallow Skincare", "Peptide Face Serum", "Niacinamide Serum",
    "Lash Serum", "LED Face Mask", "Retinol Alternative", "Snail Mucin Serum",
    "Slugging Skincare", "Facial Gua Sha", "Ice Roller Face", "Lip Filler Alternative",
    "Glass Skin Routine", "Body Sunscreen SPF", "Scalp Serum Hair",
    "Rosemary Oil Hair Growth", "Hair Gloss Treatment", "Barrier Repair Moisturizer",
    "Blue Light Glasses", "Microcurrent Face Device", "RF Skin Tightening Device",
    # ── Fitness
    "Portable Sauna Blanket", "Portable Blender", "Barefoot Running Shoes",
    "Whoop Band Alternative", "Weighted Vest", "Zone 2 Training Monitor",
    "Cold Plunge Tub", "Sauna Tent", "Walking Pad Treadmill", "Pull Up Bar Doorframe",
    "Resistance Band Set", "Massage Gun", "Incline Treadmill Walking",
    "Pilates Reformer Home", "Rucking Backpack", "Battle Rope",
    "Adjustable Dumbbell Set", "Gymnastic Rings", "Vibration Plate", "Foam Roller Electric",
    # ── Tech & Gadgets
    "AI Smart Ring", "Electric Skates", "Mini Projector", "AI Pin Wearable",
    "Foldable Phone Case", "Portable Power Station", "Solar Panel Charger",
    "Smart Home Hub", "Robot Vacuum Mop Combo", "Air Quality Monitor",
    "Wireless Earbuds", "Dashcam 4K", "Action Camera", "Thermal Camera Phone",
    "Smart Glasses", "Portable Monitor", "Mechanical Keyboard",
    "Standing Desk Mat", "Cable Management Box", "Magnetic Phone Mount",
    # ── Home & Kitchen
    "Water Bottle with Filter", "Freeze Dryer Home", "Air Fryer Accessories",
    "Countertop Dishwasher", "Sous Vide Machine", "Beeswax Food Wraps",
    "Dutch Oven Cast Iron", "Bread Maker Machine", "Espresso Machine Home",
    "Mushroom Growing Kit", "Compost Bin Kitchen", "Water Kefir Kit",
    "Fermentation Crock", "Dehydrator Machine", "Silicone Baking Mats",
    "Oil Dispenser Bottle", "Bamboo Cutting Board", "Reusable Produce Bags",
    "Smart Thermostat", "Cordless Vacuum",
    # ── Pets
    "Dog Probiotic Chews", "Cat Water Fountain", "Raw Dog Food", "Dog Anxiety Vest",
    "Cat GPS Tracker", "Automatic Cat Feeder", "Dog DNA Test Kit",
    "Pet Camera Treat Dispenser", "Freeze Dried Dog Food", "Orthopedic Dog Bed",
    # ── Fashion & Apparel
    "Linen Clothing", "Merino Wool Base Layer", "Wide Leg Pants",
    "Compression Socks", "Bamboo Pajamas", "Tactical Pants",
    "Minimalist Sneakers", "Crossbody Bag", "Bucket Hat", "Swim Shorts Quick Dry",
]

CATEGORY_MAP = {
    "Creatine Gummies": "Health & Wellness", "Mushroom Coffee": "Health & Wellness",
    "Collagen Peptides Powder": "Health & Wellness", "Hydrogen Water Bottle": "Health & Wellness",
    "Grounding Mat": "Health & Wellness", "Gut Health Test Kit": "Health & Wellness",
    "Magnesium Glycinate": "Health & Wellness", "Berberine Supplement": "Health & Wellness",
    "Methylene Blue Supplement": "Health & Wellness", "Shilajit Supplement": "Health & Wellness",
    "Sea Moss Gel": "Health & Wellness", "Tallow Balm": "Health & Wellness",
    "Beef Liver Supplement": "Health & Wellness", "Electrolyte Powder": "Health & Wellness",
    "Adaptogen Supplements": "Health & Wellness", "NAD Supplement": "Health & Wellness",
    "Spermidine Supplement": "Health & Wellness", "Peptide Supplement": "Health & Wellness",
    "Urolithin A Supplement": "Health & Wellness", "Mouth Tape Sleep": "Health & Wellness",
    "Beef Tallow Skincare": "Beauty", "Peptide Face Serum": "Beauty",
    "Niacinamide Serum": "Beauty", "Lash Serum": "Beauty", "LED Face Mask": "Beauty",
    "Retinol Alternative": "Beauty", "Snail Mucin Serum": "Beauty",
    "Slugging Skincare": "Beauty", "Facial Gua Sha": "Beauty", "Ice Roller Face": "Beauty",
    "Lip Filler Alternative": "Beauty", "Glass Skin Routine": "Beauty",
    "Body Sunscreen SPF": "Beauty", "Scalp Serum Hair": "Beauty",
    "Rosemary Oil Hair Growth": "Beauty", "Hair Gloss Treatment": "Beauty",
    "Barrier Repair Moisturizer": "Beauty", "Blue Light Glasses": "Beauty",
    "Microcurrent Face Device": "Beauty", "RF Skin Tightening Device": "Beauty",
    "Portable Sauna Blanket": "Fitness", "Portable Blender": "Fitness",
    "Barefoot Running Shoes": "Fitness", "Whoop Band Alternative": "Fitness",
    "Weighted Vest": "Fitness", "Zone 2 Training Monitor": "Fitness",
    "Cold Plunge Tub": "Fitness", "Sauna Tent": "Fitness",
    "Walking Pad Treadmill": "Fitness", "Pull Up Bar Doorframe": "Fitness",
    "Resistance Band Set": "Fitness", "Massage Gun": "Fitness",
    "Incline Treadmill Walking": "Fitness", "Pilates Reformer Home": "Fitness",
    "Rucking Backpack": "Fitness", "Battle Rope": "Fitness",
    "Adjustable Dumbbell Set": "Fitness", "Gymnastic Rings": "Fitness",
    "Vibration Plate": "Fitness", "Foam Roller Electric": "Fitness",
    "AI Smart Ring": "Tech", "Electric Skates": "Tech", "Mini Projector": "Tech",
    "AI Pin Wearable": "Tech", "Foldable Phone Case": "Tech",
    "Portable Power Station": "Tech", "Solar Panel Charger": "Tech",
    "Smart Home Hub": "Tech", "Robot Vacuum Mop Combo": "Tech",
    "Air Quality Monitor": "Tech", "Wireless Earbuds": "Tech", "Dashcam 4K": "Tech",
    "Action Camera": "Tech", "Thermal Camera Phone": "Tech", "Smart Glasses": "Tech",
    "Portable Monitor": "Tech", "Mechanical Keyboard": "Tech",
    "Standing Desk Mat": "Tech", "Cable Management Box": "Tech",
    "Magnetic Phone Mount": "Tech",
    "Water Bottle with Filter": "Home & Kitchen", "Freeze Dryer Home": "Home & Kitchen",
    "Air Fryer Accessories": "Home & Kitchen", "Countertop Dishwasher": "Home & Kitchen",
    "Sous Vide Machine": "Home & Kitchen", "Beeswax Food Wraps": "Home & Kitchen",
    "Dutch Oven Cast Iron": "Home & Kitchen", "Bread Maker Machine": "Home & Kitchen",
    "Espresso Machine Home": "Home & Kitchen", "Mushroom Growing Kit": "Home & Kitchen",
    "Compost Bin Kitchen": "Home & Kitchen", "Water Kefir Kit": "Home & Kitchen",
    "Fermentation Crock": "Home & Kitchen", "Dehydrator Machine": "Home & Kitchen",
    "Silicone Baking Mats": "Home & Kitchen", "Oil Dispenser Bottle": "Home & Kitchen",
    "Bamboo Cutting Board": "Home & Kitchen", "Reusable Produce Bags": "Home & Kitchen",
    "Smart Thermostat": "Home & Kitchen", "Cordless Vacuum": "Home & Kitchen",
    "Dog Probiotic Chews": "Pets", "Cat Water Fountain": "Pets", "Raw Dog Food": "Pets",
    "Dog Anxiety Vest": "Pets", "Cat GPS Tracker": "Pets", "Automatic Cat Feeder": "Pets",
    "Dog DNA Test Kit": "Pets", "Pet Camera Treat Dispenser": "Pets",
    "Freeze Dried Dog Food": "Pets", "Orthopedic Dog Bed": "Pets",
    "Linen Clothing": "Fashion", "Merino Wool Base Layer": "Fashion",
    "Wide Leg Pants": "Fashion", "Compression Socks": "Fashion",
    "Bamboo Pajamas": "Fashion", "Tactical Pants": "Fashion",
    "Minimalist Sneakers": "Fashion", "Crossbody Bag": "Fashion",
    "Bucket Hat": "Fashion", "Swim Shorts Quick Dry": "Fashion",
}

# ── Helpers (match app.py logic exactly) ─────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def compute_growth(series):
    non_zero = [v for v in series if v > 0]
    if len(non_zero) < 2:
        return 0.0
    return round(((series[-1] - non_zero[0]) / non_zero[0]) * 100, 1)


def trend_score(series, growth):
    if not series:
        return 0
    recency  = series[-1]
    momentum = min(growth / 50, 100)
    return min(100, max(0, round(0.6 * recency + 0.4 * momentum)))


def classify_status(growth):
    if growth >= 1000:
        return "breakout"
    if growth >= 200:
        return "hot"
    return "rising"


def fetch_batch(pytrends, batch):
    results = {}
    try:
        pytrends.build_payload(batch, timeframe=TIMEFRAME, geo=GEO)
        df = pytrends.interest_over_time()
        if df.empty:
            return results
        if "isPartial" in df.columns:
            df = df.drop(columns=["isPartial"])
        try:
            monthly = df.resample("ME").mean().tail(12)
        except Exception:
            monthly = df.resample("M").mean().tail(12)
        col_map = {c.lower(): c for c in monthly.columns}
        for kw in batch:
            col = col_map.get(kw.lower())
            if col:
                series = monthly[col].fillna(0).tolist()
                results[kw] = [round(v, 1) for v in series]
                log.info("  ✓ %s", kw)
            else:
                log.warning("  ✗ %s (not in response)", kw)
    except Exception as exc:
        if "429" in str(exc):
            log.warning("Rate limited — waiting 30s and retrying")
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
                        if col:
                            results[kw] = [round(v, 1) for v in monthly[col].fillna(0).tolist()]
            except Exception as e2:
                log.warning("Retry failed: %s", e2)
        else:
            log.warning("Batch error: %s", exc)
    return results


def main():
    log.info("Starting trend fetch for %d keywords…", len(TRACKED_KEYWORDS))
    pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
    raw = {}
    batch_size = 5
    batches = [TRACKED_KEYWORDS[i:i+batch_size] for i in range(0, len(TRACKED_KEYWORDS), batch_size)]

    for i, batch in enumerate(batches, 1):
        log.info("Batch %d/%d: %s", i, len(batches), batch)
        raw.update(fetch_batch(pytrends, batch))
        if i < len(batches):
            time.sleep(12)

    keywords_out = []
    for idx, kw in enumerate(TRACKED_KEYWORDS, 1):
        series = raw.get(kw, [50] * 12)
        growth = compute_growth(series)
        keywords_out.append({
            "id":       idx,
            "keyword":  kw,
            "category": CATEGORY_MAP.get(kw, "General"),
            "status":   classify_status(growth),
            "growth":   growth,
            "score":    trend_score(series, growth),
            "trend":    series,
            "fetched":  datetime.utcnow().isoformat(),
        })

    keywords_out.sort(key=lambda k: k["growth"], reverse=True)

    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": datetime.utcnow().isoformat(), "keywords": keywords_out}
    CACHE_FILE.write_text(json.dumps(payload, indent=2))
    log.info("✅ Saved %d keywords to %s", len(keywords_out), CACHE_FILE)


if __name__ == "__main__":
    main()
