"""
TrendPulse – Flask backend.

Two distinct paths:

  Read  — the public dashboard, trend history, movers and track record. Entirely
          file-based out of cache/, populated nightly by GitHub Actions. Needs no
          infrastructure and keeps working if the database is absent.

  Write — watchlists, notes and launch outcomes. These need a real database
          because serverless filesystems are read-only and ephemeral; see
          trendpulse/db.py. Absent DATABASE_URL the app degrades to read-only
          rather than failing.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, request, send_from_directory

from trendpulse import auth, db, history, plans, signals, verticals
from trendpulse.paths import CALIBRATION_FILE, REPORT_DIR, TRENDS_FILE
from trendpulse.scoring import FACTOR_WEIGHTS, bands, confidence_label, label_for

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static")
auth.configure(app)


# ── Cache readers ─────────────────────────────────────────────────────────────

def load_cache() -> Optional[dict]:
    if not TRENDS_FILE.exists():
        return None
    try:
        return json.loads(TRENDS_FILE.read_text())
    except Exception:
        log.exception("could not read trends cache")
        return None


def load_calibration() -> Optional[dict]:
    if not CALIBRATION_FILE.exists():
        return None
    try:
        return json.loads(CALIBRATION_FILE.read_text())
    except Exception:
        return None


# ── Public pages ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/track-record")
def track_record_page():
    return send_from_directory("static", "track-record.html")


@app.route("/reports/<path:name>")
def report_page(name: str):
    if not REPORT_DIR.exists():
        return jsonify({"ok": False, "error": "No reports generated yet."}), 404
    ent = auth.entitlements()
    if plans.report_locked(name, ent):
        return jsonify({"ok": False, "reason": "plan_required", "feature": "report",
                        "error": "This week's report is for subscribers. "
                                 "Older reports are free to read.",
                        "plan": plans.public(ent)}), 402
    return send_from_directory(REPORT_DIR, name)


# ── Trends ────────────────────────────────────────────────────────────────────

@app.route("/api/trends")
def api_trends():
    data = load_cache()
    if not data:
        return jsonify({"ok": False, "error": "No data yet — nightly job hasn't run."}), 503

    keywords = data["keywords"]

    # Restrict to the active vertical unless the caller explicitly asks for all.
    requested = request.args.get("vertical") or None
    vertical_id = requested or verticals.active_vertical_id()
    if vertical_id != "all":
        keywords = verticals.filter_keywords(keywords, vertical_id)

    # The snapshot stays public — it is reproducible from public inputs, so
    # withholding it costs reach and protects nothing. The trajectory attached
    # below is not reproducible after the fact, so that is what the tier gates.
    ent = auth.entitlements()
    if ent["limits"].get("series_in_list"):
        deltas = history.load_deltas()
        for k in keywords:
            d = deltas.get(history.norm(k["keyword"]))
            if d:
                k["d7"]           = d["d7"]
                k["d30"]          = d["d30"]
                k["days_tracked"] = d["days_tracked"]
                k["first_seen"]   = d["first_seen"]

    return jsonify({
        "ok":             True,
        "keywords":       keywords,
        "plan":           plans.public(ent),
        "fetched_at":     data["fetched_at"],
        "vertical":       vertical_id,
        "verticals":      verticals.list_verticals(),
        "bands":          [{"min": m, "label": l, "color": c} for m, l, c in bands()],
        "signal_sources": data.get("signal_sources") or signals.enabled_sources(),
        "factor_weights": dict(FACTOR_WEIGHTS),
        "history":        history.coverage(),
    })


@app.route("/api/status")
def api_status():
    data = load_cache()
    cal  = load_calibration()
    return jsonify({
        "ok":             True,
        "fetched_at":     data["fetched_at"] if data else None,
        "keyword_count":  len(data["keywords"]) if data else 0,
        "vertical":       verticals.active_vertical_id(),
        "history":        history.coverage(),
        "accounts_enabled": db.is_available(),
        "calibrated":     bool(cal),
        "calibration_provisional": cal.get("provisional") if cal else None,
        "signal_sources": signals.enabled_sources(),
        "plan":           plans.public(auth.entitlements()),
    })


@app.route("/api/history/<path:keyword>")
@auth.requires_feature("history_per_day", subject_arg="keyword")
def api_history(user, keyword: str):
    hist = history.keyword_history(keyword)
    if not hist:
        return jsonify({"ok": False, "error": "No history for that keyword yet."}), 404
    return jsonify({"ok": True, **hist})


@app.route("/api/movers")
@auth.requires_feature("movers")
def api_movers(user):
    movers = history.load_movers()
    if not movers:
        return jsonify({"ok": False, "error": "History has not been built yet."}), 503
    window = request.args.get("window", "7")
    if window not in movers.get("windows", {}):
        window = next(iter(movers.get("windows", {"7": {}})))
    return jsonify({"ok": True, "window": window,
                    "available_windows": sorted(movers.get("windows", {}).keys(), key=int),
                    **movers["windows"][window]})


@app.route("/api/verticals")
def api_verticals():
    return jsonify({"ok": True, "active": verticals.active_vertical_id(),
                    "verticals": verticals.list_verticals()})


@app.route("/api/track-record")
def api_track_record():
    cal = load_calibration()
    if not cal:
        return jsonify({"ok": False,
                        "error": "No calibration yet — run scripts/calibrate.py."}), 503
    return jsonify({"ok": True, **cal})


# ── Watchlist ─────────────────────────────────────────────────────────────────

@app.route("/api/watchlist", methods=["GET"])
@auth.login_required
def get_watchlist(user):
    watches = db.list_watches(user["id"])
    data    = load_cache() or {"keywords": []}
    current = {history.norm(k["keyword"]): k for k in data["keywords"]}
    deltas  = history.load_deltas()

    enriched = []
    for w in watches:
        key  = history.norm(w["keyword"])
        live = current.get(key, {})
        d    = deltas.get(key, {})
        viability = live.get("viability")
        enriched.append({
            **w,
            "viability":     viability,
            "status":        live.get("status"),
            "growth":        live.get("growth"),
            "confidence":    live.get("confidence"),
            "d7":            d.get("d7"),
            "d30":           d.get("d30"),
            "since_added":   (viability - w["viability_at_add"]
                              if viability is not None and w["viability_at_add"] is not None
                              else None),
            "band":          label_for(viability)["text"] if viability is not None else None,
        })
    return jsonify({"ok": True, "watchlist": enriched})


@app.route("/api/watchlist", methods=["POST"])
@auth.login_required
def add_watchlist(user):
    body    = request.get_json(silent=True) or {}
    keyword = str(body.get("keyword", "")).strip()
    if not keyword:
        return jsonify({"ok": False, "error": "keyword is required"}), 400

    data = load_cache() or {"keywords": []}
    live = next((k for k in data["keywords"]
                 if history.norm(k["keyword"]) == history.norm(keyword)), {})
    db.add_watch(user["id"], keyword,
                 category=live.get("category"), viability=live.get("viability"))
    return jsonify({"ok": True})


@app.route("/api/watchlist", methods=["DELETE"])
@auth.login_required
def delete_watchlist(user):
    keyword = request.args.get("keyword", "").strip()
    if not keyword:
        return jsonify({"ok": False, "error": "keyword is required"}), 400
    db.remove_watch(user["id"], keyword)
    return jsonify({"ok": True})


# ── Notes ─────────────────────────────────────────────────────────────────────

@app.route("/api/notes", methods=["GET"])
@auth.login_required
def get_notes(user):
    keyword = request.args.get("keyword")
    if keyword:
        return jsonify({"ok": True, "note": db.get_note(user["id"], keyword)})
    return jsonify({"ok": True, "notes": db.list_notes(user["id"])})


@app.route("/api/notes", methods=["POST"])
@auth.login_required
def post_note(user):
    body    = request.get_json(silent=True) or {}
    keyword = str(body.get("keyword", "")).strip()
    text    = str(body.get("body", "")).strip()
    if not keyword:
        return jsonify({"ok": False, "error": "keyword is required"}), 400
    if not text:
        db.delete_note(user["id"], keyword)
        return jsonify({"ok": True, "deleted": True})
    if len(text) > 20_000:
        return jsonify({"ok": False, "error": "Note is too long."}), 400
    db.upsert_note(user["id"], keyword, text)
    return jsonify({"ok": True})


# ── Outcomes ──────────────────────────────────────────────────────────────────

@app.route("/api/outcomes", methods=["GET"])
@auth.login_required
def get_outcomes(user):
    keyword = request.args.get("keyword")
    if keyword:
        return jsonify({"ok": True, "outcome": db.get_outcome(user["id"], keyword)})
    return jsonify({"ok": True, "outcomes": db.list_outcomes(user["id"])})


@app.route("/api/outcomes", methods=["POST"])
@auth.login_required
def post_outcome(user):
    body    = request.get_json(silent=True) or {}
    keyword = str(body.get("keyword", "")).strip()
    stage   = str(body.get("stage", "")).strip()

    if not keyword:
        return jsonify({"ok": False, "error": "keyword is required"}), 400
    if stage not in db.STAGES:
        return jsonify({"ok": False,
                        "error": f"stage must be one of {', '.join(db.STAGES)}"}), 400

    result = body.get("result") or None
    if result and result not in db.RESULTS:
        return jsonify({"ok": False,
                        "error": f"result must be one of {', '.join(db.RESULTS)}"}), 400
    revenue = body.get("revenue_band") or None
    if revenue and revenue not in db.REVENUE_BANDS:
        return jsonify({"ok": False,
                        "error": f"revenue_band must be one of {', '.join(db.REVENUE_BANDS)}"}), 400

    # Freeze the score at the moment of the decision — that pairing is what makes
    # the record usable for calibration later.
    data = load_cache() or {"keywords": []}
    live = next((k for k in data["keywords"]
                 if history.norm(k["keyword"]) == history.norm(keyword)), {})

    db.upsert_outcome(
        user["id"], keyword, stage,
        viability_at_decision=live.get("viability"),
        result=result,
        revenue_band=revenue,
        would_repeat=body.get("would_repeat"),
        notes=(str(body.get("notes", "")).strip() or None),
    )
    return jsonify({"ok": True})


# ── Personal dashboard ────────────────────────────────────────────────────────

@app.route("/api/me/dashboard")
@auth.login_required
def my_dashboard(user):
    watches  = db.list_watches(user["id"])
    outcomes = db.list_outcomes(user["id"])
    deltas   = history.load_deltas()

    moving = []
    for w in watches:
        d = deltas.get(history.norm(w["keyword"]))
        if d and d.get("d7") is not None and abs(d["d7"]) >= 3:
            moving.append({"keyword": w["keyword"], "d7": d["d7"],
                           "viability": d["viability"]})
    moving.sort(key=lambda m: abs(m["d7"]), reverse=True)

    return jsonify({
        "ok":            True,
        "watch_count":   len(watches),
        "outcome_count": len(outcomes),
        "moving":        moving[:10],
        "stats":         db.outcome_stats(),
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
