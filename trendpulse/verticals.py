"""Vertical profiles — depth instead of breadth.

Tracking 823 keywords across 13 categories is a mile wide and an inch deep, and
a generic cross-category trend list is the easiest thing in this product to copy.
A vertical narrows the tracked universe and re-weights scoring for what actually
predicts success in that niche, which is far harder to replicate without knowing
the niche.

Selection order: TRENDPULSE_VERTICAL env var, then `active` in config/verticals.json.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from .paths import VERTICALS_FILE

log = logging.getLogger(__name__)

_config: Optional[dict] = None


def load_config() -> dict:
    global _config
    if _config is not None:
        return _config
    try:
        _config = json.loads(VERTICALS_FILE.read_text())
    except Exception:
        log.warning("verticals: config missing or unreadable — defaulting to 'all'")
        _config = {"active": "all",
                   "verticals": {"all": {"label": "All Categories",
                                         "categories": None,
                                         "weight_overrides": {}}}}
    return _config


def active_vertical_id() -> str:
    cfg = load_config()
    chosen = os.environ.get("TRENDPULSE_VERTICAL", "").strip() or cfg.get("active", "all")
    if chosen not in cfg.get("verticals", {}):
        log.warning("verticals: unknown vertical %r — falling back to 'all'", chosen)
        return "all"
    return chosen


def active_vertical() -> dict:
    vid = active_vertical_id()
    return {"id": vid, **load_config()["verticals"][vid]}


def list_verticals() -> list[dict]:
    cfg = load_config()
    active = active_vertical_id()
    return [
        {"id": vid, "label": v.get("label", vid),
         "description": v.get("description"),
         "categories": v.get("categories"),
         "active": vid == active}
        for vid, v in cfg.get("verticals", {}).items()
    ]


def categories_for(vertical_id: Optional[str] = None) -> Optional[list[str]]:
    """Categories in scope, or None meaning 'everything'."""
    cfg = load_config()
    vid = vertical_id or active_vertical_id()
    return cfg["verticals"].get(vid, {}).get("categories")


def in_scope(category: str, vertical_id: Optional[str] = None) -> bool:
    cats = categories_for(vertical_id)
    return True if cats is None else category in cats


def filter_keywords(keywords: list[dict], vertical_id: Optional[str] = None) -> list[dict]:
    cats = categories_for(vertical_id)
    if cats is None:
        return keywords
    allowed = set(cats)
    return [k for k in keywords if k.get("category") in allowed]


def discovery_seeds(vertical_id: Optional[str] = None) -> list[str]:
    cfg = load_config()
    vid = vertical_id or active_vertical_id()
    return cfg["verticals"].get(vid, {}).get("discovery_seeds", []) or []


def apply_scoring_weights() -> dict:
    """Push the active vertical's weight overrides into the scoring module.

    Call once at the start of a run, before any scoring happens.
    """
    from . import scoring

    overrides = active_vertical().get("weight_overrides") or {}
    if overrides:
        scoring.set_weight_profile(overrides)
        log.info("verticals: applied '%s' scoring weights %s", active_vertical_id(), overrides)
    return overrides
