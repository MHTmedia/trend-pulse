"""
TrendPulse – Flask backend (cache reader only)
Data is populated nightly by GitHub Actions running scripts/fetch_trends.py.
This app just reads cache/trends.json and serves it — no live fetching.
"""

import json
import os
import logging
from typing import Optional
from pathlib import Path

from flask import Flask, jsonify, send_from_directory

# ── Config ────────────────────────────────────────────────────────────────────
CACHE_FILE = Path("cache/trends.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static")


# ── Cache ─────────────────────────────────────────────────────────────────────
def load_cache() -> Optional[dict]:
    """Read cache/trends.json — populated nightly by GitHub Actions."""
    if not CACHE_FILE.exists():
        return None
    try:
        return json.loads(CACHE_FILE.read_text())
    except Exception:
        return None


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/trends")
def api_trends():
    data = load_cache()
    if not data:
        return jsonify({"ok": False, "error": "No data yet — nightly job hasn't run."}), 503
    return jsonify({"ok": True, "keywords": data["keywords"], "fetched_at": data["fetched_at"]})


@app.route("/api/status")
def api_status():
    data = load_cache()
    return jsonify({
        "ok": True,
        "fetched_at": data["fetched_at"] if data else None,
        "keyword_count": len(data["keywords"]) if data else 0,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
