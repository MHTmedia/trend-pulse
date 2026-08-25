"""Signal sources beyond the commodity Google/Reddit/Amazon stack.

Anyone can rebuild the original three inputs in a weekend, so they cannot carry a
paid product on their own. This module adds sources that are progressively harder
to replicate:

  1. Wikipedia pageviews  — free and keyless, but almost nobody fuses it with
     commerce data. Encyclopedic attention leads purchase intent for genuinely
     new categories, and unlike Google Trends it is an absolute count, not a
     0-100 relative index.
  2. Etsy listing counts  — an independent read on seller-side saturation, and
     the leading indicator for Amazon crowding.
  3. YouTube result volume — creator supply, gated behind an optional API key.
  4. First-party drops    — anything in data/proprietary/. This is the hook for
     MHT's own client campaign performance: real spend against real creative in
     these categories, which no competitor can obtain at any price.

Every fetcher returns None on failure rather than raising or guessing. Downstream
scoring treats None as absent and lowers the confidence rating, so a dead source
degrades the score's stated certainty instead of silently faking a value.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests

from .paths import CACHE_DIR, PROPRIETARY_DIR

log = logging.getLogger(__name__)

SIGNAL_CACHE_DIR = CACHE_DIR / "signals"
WIKI_TITLE_CACHE = SIGNAL_CACHE_DIR / "wikipedia_titles.json"

UA = {"User-Agent": "TrendPulse/2.0 (trend research; contact michael@mhtmedia.com)"}
TIMEOUT = 12

# Sources are individually switchable so a broken scraper can be disabled from
# the workflow file without a code change.
ENABLE_WIKIPEDIA = os.environ.get("SIGNAL_WIKIPEDIA", "1") != "0"
# Etsy 403s plain HTTP clients from datacenter ranges — verified failing from
# both a laptop and CI. Left in place because it costs nothing when off and may
# work through a proxy, but off by default so it never inflates the source count.
ENABLE_ETSY      = os.environ.get("SIGNAL_ETSY", "0") == "1"
YOUTUBE_API_KEY  = os.environ.get("YOUTUBE_API_KEY", "").strip()

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_SESSION = requests.Session()
_SESSION.headers.update(UA)


# ── Wikipedia pageviews ───────────────────────────────────────────────────────

def _load_title_cache() -> dict:
    if WIKI_TITLE_CACHE.exists():
        try:
            return json.loads(WIKI_TITLE_CACHE.read_text())
        except Exception:
            pass
    return {}


def _save_title_cache(cache: dict) -> None:
    SIGNAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    WIKI_TITLE_CACHE.write_text(json.dumps(cache, indent=2, sort_keys=True))


_title_cache: Optional[dict] = None


def resolve_wikipedia_title(keyword: str) -> Optional[str]:
    """Map a keyword to an article title. Cached forever — titles rarely move.

    A cached empty string means "resolved to nothing", so we don't re-query
    hundreds of product keywords that will never have an article.
    """
    global _title_cache
    if _title_cache is None:
        _title_cache = _load_title_cache()

    key = keyword.lower().strip()
    if key in _title_cache:
        return _title_cache[key] or None

    title = None
    try:
        resp = _SESSION.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "opensearch", "search": keyword,
                    "limit": 1, "namespace": 0, "format": "json"},
            timeout=TIMEOUT,
        )
        if resp.ok:
            data = resp.json()
            if len(data) >= 2 and data[1]:
                title = data[1][0]
    except Exception as exc:
        log.debug("wikipedia resolve failed for %s: %s", keyword, exc)
        return None      # transient — don't poison the cache with a negative

    _title_cache[key] = title or ""
    return title


def fetch_wikipedia_signal(keyword: str) -> Optional[dict]:
    """Recent pageview volume and its 30-day momentum for a keyword's article."""
    if not ENABLE_WIKIPEDIA:
        return None

    title = resolve_wikipedia_title(keyword)
    if not title:
        return None

    end   = datetime.utcnow().date() - timedelta(days=1)
    start = end - timedelta(days=59)
    url = (
        "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
        f"en.wikipedia/all-access/user/{quote(title.replace(' ', '_'), safe='')}"
        f"/daily/{start.strftime('%Y%m%d')}/{end.strftime('%Y%m%d')}"
    )
    try:
        resp = _SESSION.get(url, timeout=TIMEOUT)
        if not resp.ok:
            return None
        items = resp.json().get("items", [])
    except Exception as exc:
        log.debug("wikipedia pageviews failed for %s: %s", keyword, exc)
        return None

    if len(items) < 40:
        return None

    views = [it.get("views", 0) for it in items]
    recent = sum(views[-30:])
    prior  = sum(views[-60:-30])
    momentum = round((recent - prior) / prior * 100, 1) if prior > 0 else None

    return {
        "wiki_title":       title,
        "wiki_views_30d":   recent,
        "wiki_momentum":    momentum,
    }


# ── Etsy listing counts ───────────────────────────────────────────────────────

_ETSY_COUNT_RE = re.compile(r"([\d,]+)\s+(?:results|items)", re.I)


def fetch_etsy_signal(keyword: str) -> Optional[dict]:
    """Seller-side saturation on Etsy — an early read on where Amazon is heading."""
    if not ENABLE_ETSY:
        return None

    url = f"https://www.etsy.com/search?q={quote(keyword)}"
    try:
        resp = _SESSION.get(url, timeout=TIMEOUT, headers={"User-Agent": BROWSER_UA})
        if not resp.ok:
            return None
        match = _ETSY_COUNT_RE.search(resp.text)
        if not match:
            return None
        return {"etsy_listings": int(match.group(1).replace(",", ""))}
    except Exception as exc:
        log.debug("etsy failed for %s: %s", keyword, exc)
        return None


# ── YouTube creator supply (optional, needs a key) ────────────────────────────

def fetch_youtube_signal(keyword: str) -> Optional[dict]:
    """How much creator content already exists. Requires YOUTUBE_API_KEY."""
    if not YOUTUBE_API_KEY:
        return None
    try:
        resp = _SESSION.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={"part": "id", "q": keyword, "type": "video",
                    "maxResults": 1, "key": YOUTUBE_API_KEY},
            timeout=TIMEOUT,
        )
        if not resp.ok:
            return None
        total = resp.json().get("pageInfo", {}).get("totalResults")
        return {"youtube_results": total} if total is not None else None
    except Exception as exc:
        log.debug("youtube failed for %s: %s", keyword, exc)
        return None


# ── First-party / licensed drops ──────────────────────────────────────────────

_first_party: Optional[dict] = None


def load_first_party() -> dict:
    """Merge every JSON drop in data/proprietary/ into one keyword-indexed map.

    Expected shape per file — a list of records:

        [
          {
            "keyword": "Creatine Gummies",
            "source":  "mht-client-campaigns",
            "as_of":   "2026-08-01",
            "signal":  78,          # 0-100, higher = stronger observed demand
            "notes":   "3 clients, blended ROAS 2.4 across $48k spend"
          }
        ]

    `signal` is deliberately a normalised 0-100 rather than raw spend or revenue,
    so client-confidential figures never need to enter this repo.
    """
    global _first_party
    if _first_party is not None:
        return _first_party

    merged: dict[str, dict] = {}
    if PROPRIETARY_DIR.exists():
        for path in sorted(PROPRIETARY_DIR.glob("*.json")):
            try:
                records = json.loads(path.read_text())
            except Exception:
                log.warning("proprietary: could not parse %s", path.name)
                continue
            if not isinstance(records, list):
                log.warning("proprietary: %s is not a list of records", path.name)
                continue
            for rec in records:
                kw = str(rec.get("keyword", "")).lower().strip()
                signal = rec.get("signal")
                if not kw or signal is None:
                    continue
                existing = merged.get(kw)
                # Most recent observation wins.
                if existing and str(existing.get("as_of", "")) > str(rec.get("as_of", "")):
                    continue
                merged[kw] = {
                    "first_party_signal": max(0, min(100, float(signal))),
                    "first_party_source": rec.get("source", path.stem),
                    "first_party_as_of":  rec.get("as_of"),
                    "first_party_notes":  rec.get("notes"),
                }

    _first_party = merged
    if merged:
        log.info("proprietary: loaded first-party signal for %d keywords", len(merged))
    return merged


def fetch_first_party_signal(keyword: str) -> Optional[dict]:
    return load_first_party().get(keyword.lower().strip())


# ── Orchestration ─────────────────────────────────────────────────────────────

def fetch_all_signals(keyword: str, polite_delay: float = 0.8) -> dict:
    """Collect every available extra signal for one keyword.

    Returns a flat dict of whatever succeeded; callers should assume any key may
    be missing. Never raises.
    """
    out: dict = {}
    active = [
        (fetch_wikipedia_signal, ENABLE_WIKIPEDIA),
        (fetch_etsy_signal,      ENABLE_ETSY),
        (fetch_youtube_signal,   bool(YOUTUBE_API_KEY)),
    ]
    for fetcher, enabled in active:
        if not enabled:
            continue
        try:
            result = fetcher(keyword)
        except Exception as exc:                      # belt and braces
            log.debug("signal %s raised for %s: %s", fetcher.__name__, keyword, exc)
            result = None
        if result:
            out.update(result)
        if polite_delay:
            time.sleep(polite_delay)

    fp = fetch_first_party_signal(keyword)
    if fp:
        out.update(fp)
    return out


def flush_caches() -> None:
    """Persist anything worth keeping between runs."""
    if _title_cache is not None:
        _save_title_cache(_title_cache)


def enabled_sources() -> dict:
    """Which extra sources are live this run — surfaced in the API for transparency."""
    return {
        "wikipedia":   ENABLE_WIKIPEDIA,
        "etsy":        ENABLE_ETSY,
        "youtube":     bool(YOUTUBE_API_KEY),
        "first_party": bool(load_first_party()),
    }
