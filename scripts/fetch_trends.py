"""
TrendPulse nightly fetch script.
- Loads dynamic keyword list from cache/keywords.json (falls back to defaults)
- Fetches Google Trends + Reddit + Amazon data for all active keywords
- Detects fading trends and marks them
- Discovers new rising keywords via pytrends related_queries
- Writes updated cache/keywords.json and cache/trends.json

Run locally or via GitHub Actions. Do NOT run on Railway (datacenter IPs get blocked).

Usage:
    pip install pytrends requests beautifulsoup4 lxml
    python scripts/fetch_trends.py
"""

import json
import time
import logging
import re
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from pytrends.request import TrendReq

# ── Paths ─────────────────────────────────────────────────────────────────────
CACHE_DIR      = Path("cache")
TRENDS_FILE    = CACHE_DIR / "trends.json"
KEYWORDS_FILE  = CACHE_DIR / "keywords.json"

# ── Config ────────────────────────────────────────────────────────────────────
GEO       = "US"
TIMEFRAME = "today 12-m"

# Fading: peak must be notable AND recent months must be well below it
FADING_PEAK_MIN      = 25   # ignore flat/low keywords (noise)
FADING_RECENT_RATIO  = 0.45  # recent 3-mo avg must be < 45% of peak
FADING_SLOPE_WINDOW  = 4    # look at last N months for declining slope

# Discovery: how many new keywords to add per run (caps runaway growth)
MAX_NEW_PER_RUN = 10

# Reddit
REDDIT_HEADERS = {"User-Agent": "TrendPulse/1.0 (trend research tool)"}

# Amazon — rotate UAs to reduce fingerprinting
AMAZON_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]
AMAZON_SESSION = requests.Session()   # reuse TCP connection within a run

# ── Default keyword list (used only if keywords.json doesn't exist yet) ───────
DEFAULT_KEYWORDS = [
    # Health & Wellness
    {"keyword": "Creatine Gummies",          "category": "Health & Wellness"},
    {"keyword": "Mushroom Coffee",            "category": "Health & Wellness"},
    {"keyword": "Collagen Peptides Powder",   "category": "Health & Wellness"},
    {"keyword": "Hydrogen Water Bottle",      "category": "Health & Wellness"},
    {"keyword": "Grounding Mat",              "category": "Health & Wellness"},
    {"keyword": "Gut Health Test Kit",        "category": "Health & Wellness"},
    {"keyword": "Magnesium Glycinate",        "category": "Health & Wellness"},
    {"keyword": "Berberine Supplement",       "category": "Health & Wellness"},
    {"keyword": "Methylene Blue Supplement",  "category": "Health & Wellness"},
    {"keyword": "Shilajit Supplement",        "category": "Health & Wellness"},
    {"keyword": "Sea Moss Gel",               "category": "Health & Wellness"},
    {"keyword": "Tallow Balm",                "category": "Health & Wellness"},
    {"keyword": "Beef Liver Supplement",      "category": "Health & Wellness"},
    {"keyword": "Electrolyte Powder",         "category": "Health & Wellness"},
    {"keyword": "Adaptogen Supplements",      "category": "Health & Wellness"},
    {"keyword": "NAD Supplement",             "category": "Health & Wellness"},
    {"keyword": "Spermidine Supplement",      "category": "Health & Wellness"},
    {"keyword": "Peptide Supplement",         "category": "Health & Wellness"},
    {"keyword": "Urolithin A Supplement",     "category": "Health & Wellness"},
    {"keyword": "Mouth Tape Sleep",           "category": "Health & Wellness"},
    # Beauty
    {"keyword": "Beef Tallow Skincare",       "category": "Beauty"},
    {"keyword": "Peptide Face Serum",         "category": "Beauty"},
    {"keyword": "Niacinamide Serum",          "category": "Beauty"},
    {"keyword": "Lash Serum",                 "category": "Beauty"},
    {"keyword": "LED Face Mask",              "category": "Beauty"},
    {"keyword": "Retinol Alternative",        "category": "Beauty"},
    {"keyword": "Snail Mucin Serum",          "category": "Beauty"},
    {"keyword": "Slugging Skincare",          "category": "Beauty"},
    {"keyword": "Facial Gua Sha",             "category": "Beauty"},
    {"keyword": "Ice Roller Face",            "category": "Beauty"},
    {"keyword": "Lip Filler Alternative",     "category": "Beauty"},
    {"keyword": "Glass Skin Routine",         "category": "Beauty"},
    {"keyword": "Body Sunscreen SPF",         "category": "Beauty"},
    {"keyword": "Scalp Serum Hair",           "category": "Beauty"},
    {"keyword": "Rosemary Oil Hair Growth",   "category": "Beauty"},
    {"keyword": "Hair Gloss Treatment",       "category": "Beauty"},
    {"keyword": "Barrier Repair Moisturizer", "category": "Beauty"},
    {"keyword": "Blue Light Glasses",         "category": "Beauty"},
    {"keyword": "Microcurrent Face Device",   "category": "Beauty"},
    {"keyword": "RF Skin Tightening Device",  "category": "Beauty"},
    # Fitness
    {"keyword": "Portable Sauna Blanket",     "category": "Fitness"},
    {"keyword": "Portable Blender",           "category": "Fitness"},
    {"keyword": "Barefoot Running Shoes",     "category": "Fitness"},
    {"keyword": "Whoop Band Alternative",     "category": "Fitness"},
    {"keyword": "Weighted Vest",              "category": "Fitness"},
    {"keyword": "Zone 2 Training Monitor",    "category": "Fitness"},
    {"keyword": "Cold Plunge Tub",            "category": "Fitness"},
    {"keyword": "Sauna Tent",                 "category": "Fitness"},
    {"keyword": "Walking Pad Treadmill",      "category": "Fitness"},
    {"keyword": "Pull Up Bar Doorframe",      "category": "Fitness"},
    {"keyword": "Resistance Band Set",        "category": "Fitness"},
    {"keyword": "Massage Gun",                "category": "Fitness"},
    {"keyword": "Incline Treadmill Walking",  "category": "Fitness"},
    {"keyword": "Pilates Reformer Home",      "category": "Fitness"},
    {"keyword": "Rucking Backpack",           "category": "Fitness"},
    {"keyword": "Battle Rope",                "category": "Fitness"},
    {"keyword": "Adjustable Dumbbell Set",    "category": "Fitness"},
    {"keyword": "Gymnastic Rings",            "category": "Fitness"},
    {"keyword": "Vibration Plate",            "category": "Fitness"},
    {"keyword": "Foam Roller Electric",       "category": "Fitness"},
    # Tech & Gadgets
    {"keyword": "AI Smart Ring",              "category": "Tech"},
    {"keyword": "Electric Skates",            "category": "Tech"},
    {"keyword": "Mini Projector",             "category": "Tech"},
    {"keyword": "AI Pin Wearable",            "category": "Tech"},
    {"keyword": "Foldable Phone Case",        "category": "Tech"},
    {"keyword": "Portable Power Station",     "category": "Tech"},
    {"keyword": "Solar Panel Charger",        "category": "Tech"},
    {"keyword": "Smart Home Hub",             "category": "Tech"},
    {"keyword": "Robot Vacuum Mop Combo",     "category": "Tech"},
    {"keyword": "Air Quality Monitor",        "category": "Tech"},
    {"keyword": "Wireless Earbuds",           "category": "Tech"},
    {"keyword": "Dashcam 4K",                 "category": "Tech"},
    {"keyword": "Action Camera",              "category": "Tech"},
    {"keyword": "Thermal Camera Phone",       "category": "Tech"},
    {"keyword": "Smart Glasses",              "category": "Tech"},
    {"keyword": "Portable Monitor",           "category": "Tech"},
    {"keyword": "Mechanical Keyboard",        "category": "Tech"},
    {"keyword": "Standing Desk Mat",          "category": "Tech"},
    {"keyword": "Cable Management Box",       "category": "Tech"},
    {"keyword": "Magnetic Phone Mount",       "category": "Tech"},
    # Home & Kitchen
    {"keyword": "Water Bottle with Filter",   "category": "Home & Kitchen"},
    {"keyword": "Freeze Dryer Home",          "category": "Home & Kitchen"},
    {"keyword": "Air Fryer Accessories",      "category": "Home & Kitchen"},
    {"keyword": "Countertop Dishwasher",      "category": "Home & Kitchen"},
    {"keyword": "Sous Vide Machine",          "category": "Home & Kitchen"},
    {"keyword": "Beeswax Food Wraps",         "category": "Home & Kitchen"},
    {"keyword": "Dutch Oven Cast Iron",       "category": "Home & Kitchen"},
    {"keyword": "Bread Maker Machine",        "category": "Home & Kitchen"},
    {"keyword": "Espresso Machine Home",      "category": "Home & Kitchen"},
    {"keyword": "Mushroom Growing Kit",       "category": "Home & Kitchen"},
    {"keyword": "Compost Bin Kitchen",        "category": "Home & Kitchen"},
    {"keyword": "Water Kefir Kit",            "category": "Home & Kitchen"},
    {"keyword": "Fermentation Crock",         "category": "Home & Kitchen"},
    {"keyword": "Dehydrator Machine",         "category": "Home & Kitchen"},
    {"keyword": "Silicone Baking Mats",       "category": "Home & Kitchen"},
    {"keyword": "Oil Dispenser Bottle",       "category": "Home & Kitchen"},
    {"keyword": "Bamboo Cutting Board",       "category": "Home & Kitchen"},
    {"keyword": "Reusable Produce Bags",      "category": "Home & Kitchen"},
    {"keyword": "Smart Thermostat",           "category": "Home & Kitchen"},
    {"keyword": "Cordless Vacuum",            "category": "Home & Kitchen"},
    # Pets
    {"keyword": "Dog Probiotic Chews",        "category": "Pets"},
    {"keyword": "Cat Water Fountain",         "category": "Pets"},
    {"keyword": "Raw Dog Food",               "category": "Pets"},
    {"keyword": "Dog Anxiety Vest",           "category": "Pets"},
    {"keyword": "Cat GPS Tracker",            "category": "Pets"},
    {"keyword": "Automatic Cat Feeder",       "category": "Pets"},
    {"keyword": "Dog DNA Test Kit",           "category": "Pets"},
    {"keyword": "Pet Camera Treat Dispenser", "category": "Pets"},
    {"keyword": "Freeze Dried Dog Food",      "category": "Pets"},
    {"keyword": "Orthopedic Dog Bed",         "category": "Pets"},
    # Fashion & Apparel
    {"keyword": "Linen Clothing",             "category": "Fashion"},
    {"keyword": "Merino Wool Base Layer",     "category": "Fashion"},
    {"keyword": "Wide Leg Pants",             "category": "Fashion"},
    {"keyword": "Compression Socks",          "category": "Fashion"},
    {"keyword": "Bamboo Pajamas",             "category": "Fashion"},
    {"keyword": "Tactical Pants",             "category": "Fashion"},
    {"keyword": "Minimalist Sneakers",        "category": "Fashion"},
    {"keyword": "Crossbody Bag",              "category": "Fashion"},
    {"keyword": "Bucket Hat",                 "category": "Fashion"},
    {"keyword": "Swim Shorts Quick Dry",      "category": "Fashion"},
]

# Seed terms used to discover new keywords per category
DISCOVERY_SEEDS = {
    "Health & Wellness": ["health supplement", "wellness product", "biohacking"],
    "Beauty":            ["skincare product", "beauty trend", "hair care"],
    "Fitness":           ["fitness equipment", "home gym", "workout gear"],
    "Tech":              ["tech gadget", "smart device", "wearable tech"],
    "Home & Kitchen":    ["kitchen gadget", "home product", "cooking tool"],
    "Pets":              ["pet product", "dog accessory", "cat product"],
    "Fashion":           ["fashion trend", "clothing style", "accessories"],
}

# Words that indicate a query is not a product (filter these out)
NON_PRODUCT_PATTERNS = re.compile(
    r"\b(how to|what is|where to|why|who|when|best|review|vs|versus|near me|"
    r"recipe|tutorial|ideas|tips|guide|meaning|definition|price)\b",
    re.IGNORECASE,
)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ── Keyword list management ───────────────────────────────────────────────────

def load_keywords() -> list[dict]:
    """Load keywords.json, or fall back to defaults."""
    if KEYWORDS_FILE.exists():
        try:
            data = json.loads(KEYWORDS_FILE.read_text())
            log.info("Loaded %d keywords from %s", len(data), KEYWORDS_FILE)
            return data
        except Exception as e:
            log.warning("Could not read keywords.json (%s) — using defaults", e)
    log.info("No keywords.json found — using default list (%d keywords)", len(DEFAULT_KEYWORDS))
    return [dict(k, status="active", added=datetime.utcnow().date().isoformat())
            for k in DEFAULT_KEYWORDS]


def save_keywords(keywords: list[dict]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    KEYWORDS_FILE.write_text(json.dumps(keywords, indent=2))
    log.info("Saved %d keywords to %s", len(keywords), KEYWORDS_FILE)


# ── Trend analysis ────────────────────────────────────────────────────────────

def compute_growth(series: list[float]) -> float:
    non_zero = [v for v in series if v > 0]
    if len(non_zero) < 2:
        return 0.0
    return round(((series[-1] - non_zero[0]) / non_zero[0]) * 100, 1)


def trend_score(series: list[float], growth: float) -> int:
    if not series:
        return 0
    recency  = series[-1]
    momentum = min(growth / 50, 100)
    return min(100, max(0, round(0.6 * recency + 0.4 * momentum)))


def classify_momentum(growth: float) -> str:
    if growth >= 1000:
        return "breakout"
    if growth >= 200:
        return "hot"
    return "rising"


def is_fading(series: list[float]) -> bool:
    """
    Returns True if the keyword's interest has peaked and is meaningfully
    declining — not just seasonal noise.

    Criteria:
      1. Peak was significant (reached FADING_PEAK_MIN at some point)
      2. Recent 3-month average is below FADING_RECENT_RATIO of the peak
      3. The last FADING_SLOPE_WINDOW months show a declining slope
    """
    if len(series) < 6:
        return False
    peak = max(series)
    if peak < FADING_PEAK_MIN:
        return False  # Was never notable enough to call it "fading"

    recent   = series[-3:]
    recent_avg = sum(recent) / len(recent)
    if recent_avg >= peak * FADING_RECENT_RATIO:
        return False  # Still healthy relative to peak

    # Check slope of last N months is negative
    window = series[-FADING_SLOPE_WINDOW:]
    slope  = window[-1] - window[0]
    return slope < 0


# ── Google Trends fetching ────────────────────────────────────────────────────

def fetch_batch(pytrends: TrendReq, batch: list[str]) -> dict:
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


def fetch_all_trends(keywords: list[str]) -> dict:
    """Fetch Google Trends for all keywords. Returns {keyword: [monthly series]}."""
    pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
    raw = {}
    batch_size = 5
    batches = [keywords[i:i+batch_size] for i in range(0, len(keywords), batch_size)]
    for i, batch in enumerate(batches, 1):
        log.info("Google Trends batch %d/%d", i, len(batches))
        raw.update(fetch_batch(pytrends, batch))
        if i < len(batches):
            time.sleep(12)
    return raw


# ── Keyword discovery ─────────────────────────────────────────────────────────

def looks_like_product(query: str) -> bool:
    """Heuristic: filter out questions, how-tos, and non-product searches."""
    q = query.strip()
    if len(q) < 4 or len(q) > 60:
        return False
    if NON_PRODUCT_PATTERNS.search(q):
        return False
    # Must contain at least one letter (no pure numbers/symbols)
    if not re.search(r"[a-zA-Z]{3}", q):
        return False
    return True


def discover_new_keywords(existing_keywords: set[str]) -> list[dict]:
    """
    Use pytrends related_queries to find rising product keywords
    not already in our list.
    """
    pytrends    = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
    discovered  = []
    seen        = set(k.lower() for k in existing_keywords)

    for category, seeds in DISCOVERY_SEEDS.items():
        for seed in seeds:
            try:
                log.info("Discovering via seed: '%s' (%s)", seed, category)
                pytrends.build_payload([seed], timeframe="today 3-m", geo=GEO)
                related = pytrends.related_queries()
                rising  = related.get(seed, {}).get("rising")
                if rising is None or rising.empty:
                    time.sleep(5)
                    continue

                for _, row in rising.iterrows():
                    query = str(row.get("query", "")).strip()
                    if (query.lower() not in seen
                            and looks_like_product(query)
                            and len(discovered) < MAX_NEW_PER_RUN):
                        discovered.append({
                            "keyword":  query.title(),
                            "category": category,
                            "status":   "active",
                            "added":    datetime.utcnow().date().isoformat(),
                            "is_new":   True,
                        })
                        seen.add(query.lower())
                        log.info("  🆕 Discovered: %s (%s)", query, category)

                time.sleep(8)
            except Exception as exc:
                log.warning("Discovery failed for '%s': %s", seed, exc)
                time.sleep(5)

    log.info("Discovered %d new keywords", len(discovered))
    return discovered


# ── Reddit ────────────────────────────────────────────────────────────────────

def fetch_reddit_mentions(keyword: str) -> tuple[int, int, float | None]:
    now       = datetime.now(timezone.utc)
    week_ago  = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    total_30d = this_week = last_week = 0
    try:
        resp = requests.get(
            "https://www.reddit.com/search.json",
            params={"q": keyword, "sort": "new", "limit": 100, "t": "month", "type": "link"},
            headers=REDDIT_HEADERS,
            timeout=10,
        )
        if resp.status_code != 200:
            return 0, 0, None
        for post in resp.json().get("data", {}).get("children", []):
            created = datetime.fromtimestamp(post["data"]["created_utc"], tz=timezone.utc)
            if created >= month_ago:
                total_30d += 1
            if created >= week_ago:
                this_week += 1
            elif created >= week_ago - timedelta(days=7):
                last_week += 1
    except Exception as exc:
        log.warning("Reddit fetch failed for '%s': %s", keyword, exc)
    velocity = None
    if this_week > 0 or last_week > 0:
        velocity = 100.0 if last_week == 0 else round(((this_week - last_week) / last_week) * 100, 1)
    log.info("  Reddit '%s': %d/30d  %d this week", keyword, total_30d, this_week)
    return total_30d, this_week, velocity


# ── Amazon ───────────────────────────────────────────────────────────────────

def _amazon_headers() -> dict:
    return {
        "User-Agent": random.choice(AMAZON_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
    }


def fetch_amazon_data(keyword: str) -> dict:
    """
    Scrape Amazon search results for a keyword.
    Returns:
      result_count   – approximate total listings (market size)
      best_seller    – True if any top-10 result has a Best Seller badge
      amazons_choice – True if any top-10 result has an Amazon's Choice badge
      top_reviews    – review count of the highest-reviewed top-10 product
      avg_price      – average price across top-10 priced products (USD)
      avg_rating     – average star rating across top-10 rated products
      seller_count   – number of distinct products on the first page (competition proxy)
    """
    empty = {
        "amazon_result_count": None,
        "amazon_best_seller":  False,
        "amazons_choice":      False,
        "amazon_top_reviews":  None,
        "amazon_avg_price":    None,
        "amazon_avg_rating":   None,
        "amazon_seller_count": None,
    }
    url = f"https://www.amazon.com/s?k={quote_plus(keyword)}&ref=nb_sb_noss"
    try:
        resp = AMAZON_SESSION.get(url, headers=_amazon_headers(), timeout=15)
        if resp.status_code != 200:
            log.warning("  Amazon HTTP %d for '%s'", resp.status_code, keyword)
            return empty
        # Detect CAPTCHA / robot-check page
        if "robot" in resp.text[:2000].lower() or "captcha" in resp.text[:2000].lower():
            log.warning("  Amazon bot-check triggered for '%s' — skipping", keyword)
            return empty

        soup = BeautifulSoup(resp.text, "lxml")

        # ── Result count ──────────────────────────────────────────────────────
        result_count = None
        count_el = soup.select_one("span.a-color-state.a-text-bold, span[data-component-type='s-result-info-bar'] h1")
        if not count_el:
            # try the breadcrumb-style count
            for span in soup.find_all("span", class_="a-color-state"):
                txt = span.get_text(" ", strip=True)
                if "result" in txt.lower():
                    count_el = span
                    break
        if count_el:
            txt = count_el.get_text(" ", strip=True)
            # e.g. "1-16 of over 4,000 results" or "over 1,000 results"
            nums = re.findall(r"[\d,]+", txt.replace(",", ""))
            if nums:
                result_count = int(max(nums, key=lambda n: int(n)))

        # ── Product cards ─────────────────────────────────────────────────────
        cards = soup.select("div[data-component-type='s-search-result']")[:10]

        best_seller  = False
        amazons_choice = False
        prices       = []
        ratings      = []
        review_counts = []

        for card in cards:
            text = card.get_text(" ", strip=True)

            # Badges
            for badge in card.select("span.a-badge-text, span[data-component-type='s-status-badge-component']"):
                bt = badge.get_text(strip=True).lower()
                if "best seller" in bt:
                    best_seller = True
                if "amazon's choice" in bt or "amazons choice" in bt:
                    amazons_choice = True

            # Price — grab whole + fraction
            price_whole = card.select_one("span.a-price-whole")
            price_frac  = card.select_one("span.a-price-fraction")
            if price_whole:
                try:
                    p = float(price_whole.get_text(strip=True).replace(",", "").rstrip("."))
                    if price_frac:
                        p += float("0." + price_frac.get_text(strip=True))
                    if 0.5 < p < 5000:   # sanity-check
                        prices.append(p)
                except ValueError:
                    pass

            # Rating
            rating_el = card.select_one("span.a-icon-alt")
            if rating_el:
                m = re.search(r"([\d.]+) out of", rating_el.get_text())
                if m:
                    try:
                        ratings.append(float(m.group(1)))
                    except ValueError:
                        pass

            # Review count
            for span in card.select("span.a-size-base"):
                txt = span.get_text(strip=True).replace(",", "")
                if txt.isdigit() and int(txt) > 10:
                    review_counts.append(int(txt))
                    break

        seller_count = len(cards)

        result = {
            "amazon_result_count": result_count,
            "amazon_best_seller":  best_seller,
            "amazons_choice":      amazons_choice,
            "amazon_top_reviews":  max(review_counts) if review_counts else None,
            "amazon_avg_price":    round(sum(prices) / len(prices), 2) if prices else None,
            "amazon_avg_rating":   round(sum(ratings) / len(ratings), 2) if ratings else None,
            "amazon_seller_count": seller_count,
        }
        log.info("  Amazon '%s': %s results, BSB=%s, reviews=%s, price=$%s",
                 keyword, result_count, best_seller,
                 result["amazon_top_reviews"], result["amazon_avg_price"])
        return result

    except Exception as exc:
        log.warning("  Amazon fetch failed for '%s': %s", keyword, exc)
        return empty


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # ── 1. Load keyword list ───────────────────────────────────────────────────
    keyword_records = load_keywords()
    active_records  = [k for k in keyword_records if k.get("status") != "paused"]
    active_keywords = [k["keyword"] for k in active_records]
    kw_meta         = {k["keyword"]: k for k in keyword_records}

    log.info("Tracking %d active keywords (%d total including paused)",
             len(active_keywords), len(keyword_records))

    # ── 2. Fetch Google Trends ─────────────────────────────────────────────────
    raw = fetch_all_trends(active_keywords)

    # ── 3. Detect fading & update keyword statuses ─────────────────────────────
    fading_count = 0
    for rec in keyword_records:
        if rec.get("status") == "paused":
            continue
        series = raw.get(rec["keyword"], [])
        if series and is_fading(series):
            rec["status"]       = "fading"
            rec["fading_since"] = datetime.utcnow().date().isoformat()
            fading_count += 1
            log.info("📉 Fading: %s", rec["keyword"])
        elif rec.get("status") == "fading" and series:
            # Check if it recovered
            peak = max(series)
            recent_avg = sum(series[-3:]) / 3
            if recent_avg >= peak * 0.6:
                rec["status"] = "active"
                rec.pop("fading_since", None)
                log.info("📈 Recovered: %s", rec["keyword"])

    log.info("%d keywords marked as fading", fading_count)

    # ── 4. Discover new keywords ───────────────────────────────────────────────
    existing_set  = {k["keyword"].lower() for k in keyword_records}
    new_keywords  = discover_new_keywords(existing_set)
    keyword_records.extend(new_keywords)
    kw_meta.update({k["keyword"]: k for k in new_keywords})

    # ── 5. Fetch Reddit mentions ───────────────────────────────────────────────
    log.info("Fetching Reddit mentions…")
    reddit_data = {}
    for i, kw in enumerate(active_keywords):
        total, this_week, velocity = fetch_reddit_mentions(kw)
        reddit_data[kw] = {"reddit_30d": total, "reddit_7d": this_week, "reddit_velocity": velocity}
        if i < len(active_keywords) - 1:
            time.sleep(1.5)

    # ── 6. Fetch Amazon data ───────────────────────────────────────────────────
    log.info("Fetching Amazon data…")
    amazon_data = {}
    for i, kw in enumerate(active_keywords):
        amazon_data[kw] = fetch_amazon_data(kw)
        if i < len(active_keywords) - 1:
            time.sleep(random.uniform(2.5, 4.5))   # randomised delay

    # ── 7. Build trends output ─────────────────────────────────────────────────
    keywords_out = []
    for idx, rec in enumerate(keyword_records, 1):
        kw     = rec["keyword"]
        series = raw.get(kw, [50] * 12)
        growth = compute_growth(series)
        rd     = reddit_data.get(kw, {})
        amz    = amazon_data.get(kw, {})
        status = rec.get("status", "active")

        keywords_out.append({
            "id":               idx,
            "keyword":          kw,
            "category":         rec.get("category", "General"),
            "status":           status,
            "momentum":         classify_momentum(growth),
            "growth":           growth,
            "score":            trend_score(series, growth),
            "trend":            series,
            "fetched":          datetime.utcnow().isoformat(),
            "is_new":           rec.get("is_new", False),
            "added":            rec.get("added"),
            "fading_since":     rec.get("fading_since"),
            # Reddit
            "reddit_30d":           rd.get("reddit_30d", 0),
            "reddit_7d":            rd.get("reddit_7d", 0),
            "reddit_velocity":      rd.get("reddit_velocity"),
            # Amazon
            "amazon_result_count":  amz.get("amazon_result_count"),
            "amazon_best_seller":   amz.get("amazon_best_seller", False),
            "amazons_choice":       amz.get("amazons_choice", False),
            "amazon_top_reviews":   amz.get("amazon_top_reviews"),
            "amazon_avg_price":     amz.get("amazon_avg_price"),
            "amazon_avg_rating":    amz.get("amazon_avg_rating"),
            "amazon_seller_count":  amz.get("amazon_seller_count"),
        })

    # Sort: active first (by growth), then fading
    keywords_out.sort(key=lambda k: (k["status"] == "fading", -k["growth"]))

    # ── 7. Save outputs ────────────────────────────────────────────────────────
    save_keywords(keyword_records)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": datetime.utcnow().isoformat(), "keywords": keywords_out}
    TRENDS_FILE.write_text(json.dumps(payload, indent=2))
    amz_ok = sum(1 for k in keywords_out if k.get("amazon_result_count") is not None)
    log.info("✅ Saved %d keywords to %s (%d fading, %d new discovered, %d with Amazon data)",
             len(keywords_out), TRENDS_FILE, fading_count, len(new_keywords), amz_ok)


if __name__ == "__main__":
    main()
