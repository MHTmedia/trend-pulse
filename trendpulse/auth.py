"""Account handling — the prerequisite for watchlists, notes and outcome tracking.

Deliberately minimal: email plus password, hashed with werkzeug's scrypt default,
carried in Flask's signed session cookie. No email delivery is wired up because
no SMTP credentials exist in this project, so there is no password-reset flow yet;
that is the one gap to close before charging real money.
"""

from __future__ import annotations

import functools
import logging
import os
import re
import time
from typing import Optional

from flask import Blueprint, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from . import db, plans

log = logging.getLogger(__name__)

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
MIN_PASSWORD_LEN = 10

# werkzeug 3 defaults to scrypt, which needs an OpenSSL build that exposes it —
# absent on some macOS/LibreSSL Pythons. pbkdf2:sha256 is available everywhere
# and werkzeug runs it at 600k iterations, so portability costs nothing here.
PASSWORD_METHOD = "pbkdf2:sha256"

# Gating is on unless explicitly disabled. Defaulting closed means a missing env
# var costs a support email; defaulting open means it silently publishes the
# thing being sold, and only one of those is recoverable.
PAYWALL_ENABLED = os.environ.get("TRENDPULSE_PAYWALL", "on").strip().lower() not in (
    "off", "0", "false", "no")

# Best-effort throttle. Serverless instances are recycled, so this slows down a
# casual attacker but is not a substitute for a real rate limiter at the edge.
_ATTEMPTS: dict[str, list[float]] = {}
MAX_ATTEMPTS = 8
ATTEMPT_WINDOW = 300


def _throttled(key: str) -> bool:
    now = time.time()
    hits = [t for t in _ATTEMPTS.get(key, []) if now - t < ATTEMPT_WINDOW]
    _ATTEMPTS[key] = hits
    return len(hits) >= MAX_ATTEMPTS


def _record_attempt(key: str) -> None:
    _ATTEMPTS.setdefault(key, []).append(time.time())


def current_user() -> Optional[dict]:
    uid = session.get("uid")
    if not uid:
        return None
    if not db.is_available():
        return None
    try:
        return db.user_by_id(uid)
    except Exception:
        log.exception("auth: could not load session user")
        return None


def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not db.is_available():
            return jsonify({"ok": False, "error": db.unavailable_reason()}), 503
        user = current_user()
        if not user:
            return jsonify({"ok": False, "error": "Sign in required."}), 401
        return fn(user, *args, **kwargs)
    return wrapper


def entitlements() -> dict:
    """Effective plan for the current caller.

    Routes that *trim* their response (rather than refuse it) consult this
    directly; routes that refuse outright use @requires_feature below.
    """
    if not PAYWALL_ENABLED:
        return plans.resolve({"plan": plans.PRO})
    return plans.resolve(current_user() if db.is_available() else None)


def _denied(user: Optional[dict], ent: dict, feature: str):
    """401 for anonymous, 402 for signed-in-but-not-entitled.

    Kept distinct on purpose: the first wants a sign-up prompt and the second
    wants an upgrade prompt, and a single status for both leaves the browser
    unable to tell which to show.
    """
    if not user:
        return jsonify({"ok": False, "reason": "auth_required", "feature": feature,
                        "error": "Create a free account to use this."}), 401
    return jsonify({"ok": False, "reason": "plan_required", "feature": feature,
                    "error": "Your plan does not include this.",
                    "plan": plans.public(ent)}), 402


def requires_feature(feature: str, *, subject_arg: Optional[str] = None):
    """Gate a route on a plan entitlement, passing the user row to the view.

    `feature` names a key in plans.LIMITS. A boolean value gates outright; a
    numeric one meters per day (None meaning unlimited), charging one unit per
    distinct subject — taken from the view kwarg named by `subject_arg`, or the
    request path when the feature is not per-subject.

    Over-quota returns 429 rather than 402: the caller is entitled, just early.
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not PAYWALL_ENABLED:
                return fn(current_user() if db.is_available() else None, *args, **kwargs)
            if not db.is_available():
                return jsonify({"ok": False, "error": db.unavailable_reason()}), 503

            user  = current_user()
            ent   = plans.resolve(user)
            limit = ent["limits"].get(feature)

            if isinstance(limit, bool):
                return fn(user, *args, **kwargs) if limit else _denied(user, ent, feature)

            if limit is None:                       # unlimited
                return fn(user, *args, **kwargs)
            if limit <= 0 or not user:              # anonymous callers cannot be metered
                return _denied(user, ent, feature)

            subject = kwargs.get(subject_arg) if subject_arg else request.path
            charge  = db.quota_charge(user["id"], feature, str(subject), limit)
            if not charge["allowed"]:
                return jsonify({
                    "ok": False, "reason": "quota_exceeded", "feature": feature,
                    "error": f"Daily limit reached ({charge['limit']}). "
                             f"Resets at midnight UTC.",
                    "quota": charge, "plan": plans.public(ent)}), 429
            return fn(user, *args, **kwargs)
        return wrapper
    return decorator


def _public(user: dict) -> dict:
    return {"id": user["id"], "email": user["email"], "vertical": user.get("vertical"),
            "plan": plans.public(plans.resolve(user))}


@bp.route("/register", methods=["POST"])
def register():
    if not db.is_available():
        return jsonify({"ok": False, "error": db.unavailable_reason()}), 503

    data     = request.get_json(silent=True) or {}
    email    = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))

    if not EMAIL_RE.match(email):
        return jsonify({"ok": False, "error": "Enter a valid email address."}), 400
    if len(password) < MIN_PASSWORD_LEN:
        return jsonify({"ok": False,
                        "error": f"Password must be at least {MIN_PASSWORD_LEN} characters."}), 400
    if db.user_by_email(email):
        return jsonify({"ok": False, "error": "That email is already registered."}), 409

    uid = db.create_user(email,
                         generate_password_hash(password, method=PASSWORD_METHOD),
                         vertical=data.get("vertical"),
                         plan=plans.TRIAL,
                         trial_ends_at=plans.trial_end())
    session.clear()
    session["uid"] = uid
    session.permanent = True
    log.info("auth: registered user %s", email)
    return jsonify({"ok": True, "user": _public(db.user_by_id(uid))})


@bp.route("/login", methods=["POST"])
def login():
    if not db.is_available():
        return jsonify({"ok": False, "error": db.unavailable_reason()}), 503

    data     = request.get_json(silent=True) or {}
    email    = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    throttle_key = f"{request.remote_addr}:{email}"

    if _throttled(throttle_key):
        return jsonify({"ok": False,
                        "error": "Too many attempts. Try again in a few minutes."}), 429

    user = db.user_by_email(email)
    if not user or not check_password_hash(user["password_hash"], password):
        _record_attempt(throttle_key)
        # Same message either way — don't confirm which emails exist.
        return jsonify({"ok": False, "error": "Email or password is incorrect."}), 401

    session.clear()
    session["uid"] = user["id"]
    session.permanent = True
    return jsonify({"ok": True, "user": _public(user)})


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@bp.route("/me")
def me():
    if not db.is_available():
        return jsonify({"ok": True, "user": None, "accounts_enabled": False,
                        "reason": db.unavailable_reason()})
    user = current_user()
    return jsonify({"ok": True, "accounts_enabled": True,
                    "paywall": PAYWALL_ENABLED,
                    "plan": plans.public(entitlements()),
                    "user": _public(user) if user else None})


@bp.route("/vertical", methods=["POST"])
@login_required
def set_vertical(user):
    data = request.get_json(silent=True) or {}
    db.set_user_vertical(user["id"], data.get("vertical"))
    return jsonify({"ok": True})


def configure(app) -> None:
    """Attach session config. Call once during app setup."""
    secret = os.environ.get("SECRET_KEY", "").strip()
    if not secret:
        if db.IS_SERVERLESS:
            # Each cold start would otherwise mint a new key and silently
            # invalidate everyone's session.
            log.error("auth: SECRET_KEY is not set — sessions will not persist. "
                      "Set it in the Vercel project settings.")
        secret = os.urandom(32).hex()
    app.secret_key = secret
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(db.IS_SERVERLESS),
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
    )
    app.register_blueprint(bp)
