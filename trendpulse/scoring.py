"""Viability scoring — single source of truth for the nightly job, the API,
the calibration backtest and the weekly report.

Two things changed from the original implementation in fetch_trends.py:

1. Missing data no longer earns points. The old version handed out 12/25 for
   competition and 5/10 for price whenever Amazon returned nothing, so the
   keywords we knew least about quietly scored mid-table. Factors are now scored
   only over the weight that actually has data, and the result is scaled by a
   confidence multiplier — so an unmeasured keyword reads as uncertain rather
   than average.

2. Two factors were added for signals a competitor cannot cheaply copy:
   first-party campaign data and encyclopedic attention (Wikipedia pageviews).

Bands are read from cache/calibration.json when present so thresholds can be
re-derived from the observed distribution rather than hard-coded guesses.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from .paths import CALIBRATION_FILE

log = logging.getLogger(__name__)

# (key, weight) — weights sum to 100.
FACTOR_WEIGHTS = {
    "trend_momentum":   26,
    "current_interest": 16,
    "competition":      20,
    "price_viability":   8,
    "social_demand":    12,
    "first_party":      10,
    "web_attention":     8,
}
TOTAL_WEIGHT = sum(FACTOR_WEIGHTS.values())

# How hard thin data is punished. At full coverage the multiplier is 1.0; with
# only Google Trends available (42 of 100 weight) it lands near 0.74.
CONFIDENCE_FLOOR = 0.55


def set_weight_profile(overrides: dict) -> None:
    """Replace factor weights (used by vertical profiles) and renormalise.

    Only known factors are accepted; anything else is ignored rather than
    silently changing the denominator.
    """
    global TOTAL_WEIGHT
    for key, weight in overrides.items():
        if key in FACTOR_WEIGHTS:
            FACTOR_WEIGHTS[key] = weight
        else:
            log.warning("scoring: ignoring unknown weight override %r", key)
    TOTAL_WEIGHT = sum(FACTOR_WEIGHTS.values())

DEFAULT_BANDS = [
    (75, "Strong Opportunity", "#22c55e"),
    (60, "Good Potential",     "#86efac"),
    (45, "Moderate Risk",      "#facc15"),
    (30, "High Risk",          "#f97316"),
    (0,  "Not Recommended",    "#ef4444"),
]

_bands_cache: Optional[list] = None


def bands() -> list:
    """Score bands, preferring calibrated thresholds when a backtest has run."""
    global _bands_cache
    if _bands_cache is not None:
        return _bands_cache

    _bands_cache = DEFAULT_BANDS
    if CALIBRATION_FILE.exists():
        try:
            data = json.loads(CALIBRATION_FILE.read_text())
            raw = data.get("bands")
            if raw:
                _bands_cache = [(b["min"], b["label"], b["color"]) for b in raw]
        except Exception:
            log.warning("scoring: calibration.json unreadable — using default bands")
    return _bands_cache


def label_for(score: float) -> dict:
    for minimum, label, color in bands():
        if score >= minimum:
            return {"text": label, "color": color, "min": minimum}
    return {"text": "Not Recommended", "color": "#ef4444", "min": 0}


# ── Individual factors ────────────────────────────────────────────────────────
# Each returns (points, has_data). Points are on the factor's own weight scale.

def _f_trend_momentum(growth: float, status: str):
    w = FACTOR_WEIGHTS["trend_momentum"]
    if status == "flat":
        return 0.0, True
    # The sweet spot is proven-but-not-saturated. Extreme breakouts are scored
    # down because most of them are fads that peak before inventory lands.
    if   growth >= 2000: frac = 0.80
    elif growth >= 1000: frac = 0.90
    elif growth >=  500: frac = 1.00
    elif growth >=  200: frac = 0.93
    elif growth >=  100: frac = 0.73
    elif growth >=   50: frac = 0.53
    elif growth >    0:  frac = 0.33
    else:                frac = 0.10
    return w * frac, True


def _f_current_interest(series: list):
    w = FACTOR_WEIGHTS["current_interest"]
    if not series:
        return 0.0, False
    return w * (max(0.0, min(100.0, float(series[-1]))) / 100.0), True


def _f_competition(listings: Optional[int]):
    w = FACTOR_WEIGHTS["competition"]
    if listings is None:
        return 0.0, False          # unknown, not "average"
    if   listings <    100: frac = 1.00
    elif listings <    500: frac = 0.88
    elif listings <  2_000: frac = 0.68
    elif listings <  5_000: frac = 0.48
    elif listings < 15_000: frac = 0.28
    else:                   frac = 0.12
    return w * frac, True


def _f_price(price: Optional[float]):
    w = FACTOR_WEIGHTS["price_viability"]
    if price is None:
        return 0.0, False
    if   price >= 100: frac = 1.00
    elif price >=  60: frac = 0.90
    elif price >=  35: frac = 0.70
    elif price >=  20: frac = 0.40
    else:              frac = 0.10
    return w * frac, True


def _f_social(reddit_30d: Optional[int], velocity: Optional[float]):
    w = FACTOR_WEIGHTS["social_demand"]
    if reddit_30d is None:
        return 0.0, False
    r = reddit_30d
    if   r >= 100: frac = 0.93
    elif r >=  50: frac = 0.80
    elif r >=  20: frac = 0.67
    elif r >=  10: frac = 0.47
    elif r >=   3: frac = 0.27
    else:          frac = 0.07
    if velocity is not None and velocity >= 50:
        frac = min(1.0, frac + 0.13)
    return w * frac, True


def _f_first_party(signal: Optional[float]):
    """MHT's own campaign evidence. Absent for most keywords — that's expected."""
    w = FACTOR_WEIGHTS["first_party"]
    if signal is None:
        return 0.0, False
    return w * (max(0.0, min(100.0, float(signal))) / 100.0), True


def _f_web_attention(views_30d: Optional[int], momentum: Optional[float]):
    """Wikipedia pageviews: absolute attention, plus whether it is accelerating."""
    w = FACTOR_WEIGHTS["web_attention"]
    if views_30d is None:
        return 0.0, False
    v = views_30d
    if   v >= 200_000: frac = 1.00
    elif v >=  50_000: frac = 0.85
    elif v >=  15_000: frac = 0.70
    elif v >=   5_000: frac = 0.50
    elif v >=   1_000: frac = 0.30
    else:              frac = 0.12
    if momentum is not None:
        if momentum >= 25:
            frac = min(1.0, frac + 0.15)
        elif momentum <= -25:
            frac = max(0.0, frac - 0.15)
    return w * frac, True


# ── Composite ─────────────────────────────────────────────────────────────────

def compute_viability(
    growth: float,
    series: list,
    status: str,
    reddit_30d: Optional[int] = 0,
    reddit_velocity: Optional[float] = None,
    amazon_result_count: Optional[int] = None,
    amazon_avg_price: Optional[float] = None,
    amazon_avg_rating: Optional[float] = None,
    amazon_top_reviews: Optional[int] = None,
    amazon_best_seller: bool = False,
    amazons_choice: bool = False,
    first_party_signal: Optional[float] = None,
    wiki_views_30d: Optional[int] = None,
    wiki_momentum: Optional[float] = None,
):
    """Return (score, breakdown). Score is 1-100.

    breakdown carries per-factor points, which factors had data, and the
    confidence rating, so the UI can show *why* a score is what it is and how
    much of it rests on actual measurement.
    """
    results = {
        "trend_momentum":   _f_trend_momentum(growth, status),
        "current_interest": _f_current_interest(series),
        "competition":      _f_competition(amazon_result_count),
        "price_viability":  _f_price(amazon_avg_price),
        "social_demand":    _f_social(reddit_30d, reddit_velocity),
        "first_party":      _f_first_party(first_party_signal),
        "web_attention":    _f_web_attention(wiki_views_30d, wiki_momentum),
    }

    breakdown  = {k: round(v[0], 1) for k, v in results.items()}
    measured   = {k: v[1] for k, v in results.items()}
    avail_w    = sum(FACTOR_WEIGHTS[k] for k, v in results.items() if v[1])
    earned     = sum(v[0] for v in results.values())

    if avail_w == 0:
        return 1, {**breakdown, "measured": measured, "confidence": 0.0,
                   "bonus": 0, "available_weight": 0}

    # Score the factors we could actually measure, then discount for the rest.
    raw        = earned / avail_w * 100.0
    confidence = avail_w / TOTAL_WEIGHT
    adjusted   = raw * (CONFIDENCE_FLOOR + (1 - CONFIDENCE_FLOOR) * confidence)

    bonus = 0
    if amazon_best_seller or amazons_choice:
        bonus += 3                       # proven buyers exist in the category
    if (amazon_top_reviews or 0) >= 1000:
        bonus += 2                       # market already validated
    if amazon_avg_rating is not None and amazon_avg_rating < 3.5:
        bonus -= 3                       # incumbents are weak, but so is demand
    adjusted += bonus

    if status == "flat":
        adjusted = min(adjusted, 30)

    score = max(1, min(100, int(round(adjusted))))
    breakdown.update({
        "bonus":            bonus,
        "measured":         measured,
        "confidence":       round(confidence, 3),
        "available_weight": avail_w,
        "raw":              round(raw, 1),
    })
    return score, breakdown


def confidence_label(confidence: float) -> str:
    if confidence >= 0.85: return "High"
    if confidence >= 0.60: return "Medium"
    if confidence >= 0.40: return "Low"
    return "Very Low"
