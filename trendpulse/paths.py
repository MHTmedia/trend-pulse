"""Canonical filesystem locations, resolved relative to the repo root.

Resolving from __file__ (rather than cwd) keeps these correct whether the caller
is the nightly job run from the repo root or the Vercel function run from /var/task.
"""

from pathlib import Path

ROOT          = Path(__file__).resolve().parent.parent

CACHE_DIR     = ROOT / "cache"
TRENDS_FILE   = CACHE_DIR / "trends.json"
KEYWORDS_FILE = CACHE_DIR / "keywords.json"

HISTORY_DIR   = CACHE_DIR / "history"
SNAPSHOT_DIR  = HISTORY_DIR / "snapshots"
SERIES_FILE   = HISTORY_DIR / "series.json"
MOVERS_FILE   = HISTORY_DIR / "movers.json"

CALIBRATION_FILE = CACHE_DIR / "calibration.json"

CONFIG_DIR    = ROOT / "config"
VERTICALS_FILE = CONFIG_DIR / "verticals.json"

# First-party / licensed signal drops (MHT campaign data, supplier feeds, etc.)
PROPRIETARY_DIR = ROOT / "data" / "proprietary"

REPORT_DIR    = ROOT / "static" / "reports"
