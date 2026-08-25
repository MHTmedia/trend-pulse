"""Plan policy — what each tier is allowed to see.

Kept apart from auth (which decides *who* the caller is) and db (which stores
what they bought) because entitlement is the piece most likely to be
renegotiated: quotas, prices and the free/paid line move constantly, and none of
that should mean touching session handling or SQL.

The gating principle, in one line: **sell the time dimension, not the keyword
list.** Today's snapshot is reproducible by anyone with pytrends and a weekend,
so hiding it forfeits reach and protects nothing. The accumulated history, the
movers derived from it, and bulk export are what a competitor with identical
public inputs cannot reconstruct after the fact — so those are what the tiers
gate.

Two deliberate absences from the table below:

  * The track record is never gated. It is the product's credibility, and a
    scoreboard you have to pay to audit is not a scoreboard. This matters more
    than usual here because the score currently backtests at -0.132.
  * Export is off for trials at every tier but Pro. The realistic threat is not
    a user seeing too many screens, it is one scripted account draining the
    whole corpus inside the free week, and export is the fast path to that.

Quotas count *distinct* subjects per day rather than requests, so re-reading one
keyword's chart is free and breadth of extraction is what costs.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

TRIAL_DAYS = 7

# One paid tier, deliberately. Until there are features that genuinely separate
# two price points, a second tier only asks the buyer to do work the product
# cannot yet reward — so the ladder is free -> trial -> one subscription.
PRICE = {
    "amount":   "9.99",
    "currency": "USD",
    "symbol":   "$",
    "interval": "month",
    "display":  "$9.99/mo",
}

FREE  = "free"
TRIAL = "trial"
PRO   = "pro"

LIMITS: dict[str, dict] = {
    FREE: {
        "label":               "Free",
        # Enough to feel what history is worth; useless as an extraction rate.
        "history_per_day":     3,
        "movers":              False,
        "export":              False,
        # The d7/d30 trajectory attached to every card in the list response.
        "series_in_list":      False,
        # Current week stays paid; the archive is marketing.
        "report_min_age_days": 14,
    },
    TRIAL: {
        "label":               "Trial",
        "history_per_day":     25,
        "movers":              True,
        "export":              False,
        "series_in_list":      True,
        "report_min_age_days": 0,
    },
    PRO: {
        "label":               "Pro",
        "history_per_day":     None,        # unlimited
        "movers":              True,
        "export":              True,
        "series_in_list":      True,
        "report_min_age_days": 0,
    },
}


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", ""))
    except ValueError:
        return None


def trial_end(start: Optional[datetime] = None, days: int = TRIAL_DAYS) -> str:
    return ((start or datetime.utcnow()) + timedelta(days=days)).isoformat()


def resolve(user: Optional[dict]) -> dict:
    """Effective entitlement for a user row. Anonymous callers get FREE.

    Expiry is derived from trial_ends_at on every call rather than written back
    to the row by a cron job, so a lapsed trial degrades the moment it lapses
    even if nothing has run since.
    """
    plan  = (user or {}).get("plan") or FREE
    ends  = _parse((user or {}).get("trial_ends_at"))
    now   = datetime.utcnow()

    trialing = trial_expired = False
    days_left: Optional[int] = None

    if plan == PRO:
        tier = PRO
    elif plan == TRIAL and ends and now < ends:
        tier, trialing = TRIAL, True
        days_left = max(0, (ends - now).days + (1 if (ends - now).seconds else 0))
    else:
        tier = FREE
        trial_expired = plan == TRIAL and bool(ends) and now >= ends

    return {
        "tier":            tier,
        "label":           LIMITS[tier]["label"],
        "limits":          LIMITS[tier],
        "trialing":        trialing,
        "trial_expired":   trial_expired,
        "trial_ends_at":   (user or {}).get("trial_ends_at"),
        "trial_days_left": days_left,
    }


def public(ent: dict) -> dict:
    """The entitlement as the browser should see it — policy, never internals."""
    return {
        "price":           dict(PRICE),
        "tier":            ent["tier"],
        "label":           ent["label"],
        "trialing":        ent["trialing"],
        "trial_expired":   ent["trial_expired"],
        "trial_ends_at":   ent["trial_ends_at"],
        "trial_days_left": ent["trial_days_left"],
        "limits":          {k: v for k, v in ent["limits"].items() if k != "label"},
    }


def report_age_days(name: str) -> Optional[int]:
    """Age of a weekly report named `<year>-W<week>` (optionally with suffix).

    Returns None when the name does not carry a parseable week, in which case
    callers should not gate it — failing to parse is not evidence of freshness.
    """
    stem = str(name).split("/")[-1].split(".")[0]
    try:
        monday = datetime.strptime(f"{stem}-1", "%G-W%V-%u")
    except ValueError:
        return None
    return (datetime.utcnow() - monday).days


def report_locked(name: str, ent: dict) -> bool:
    min_age = ent["limits"].get("report_min_age_days") or 0
    if min_age <= 0:
        return False
    age = report_age_days(name)
    return age is not None and age < min_age
