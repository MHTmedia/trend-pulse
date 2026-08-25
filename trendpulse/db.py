"""User-data store: accounts, watchlists, notes and launch outcomes.

The public dashboard stays entirely file-based, so the read path keeps working
with zero infrastructure. Anything a *user* writes cannot live on disk — Vercel
functions get a read-only filesystem and are recycled between requests — so this
module talks to a real database instead.

    DATABASE_URL=postgresql://…   production (Neon, Supabase, RDS, anything)
    DATABASE_URL unset            local dev, falls back to a SQLite file

When no database is configured in production the app degrades to read-only:
the dashboard works, the account features report themselves as unavailable
rather than erroring at the user.

SQL is written with `?` placeholders and translated for Postgres, and timestamps
are stored as ISO-8601 text, so the same statements run on both engines.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse

from .paths import CACHE_DIR

log = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
SQLITE_PATH  = os.environ.get("SQLITE_PATH", str(CACHE_DIR / "trendpulse.db"))
IS_SERVERLESS = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))

_local = threading.local()
_schema_ready = False
_schema_lock = threading.Lock()


def dialect() -> str:
    if DATABASE_URL.startswith(("postgres://", "postgresql://")):
        return "postgres"
    return "sqlite"


def is_available() -> bool:
    """False when running serverless with no database configured."""
    if dialect() == "postgres":
        return True
    return not IS_SERVERLESS      # SQLite on a read-only FS is not usable


def unavailable_reason() -> Optional[str]:
    if is_available():
        return None
    return ("No DATABASE_URL configured. Account features need a Postgres "
            "database — set DATABASE_URL in the Vercel project settings.")


# ── Connection ────────────────────────────────────────────────────────────────

def _connect():
    if dialect() == "postgres":
        import psycopg                                  # imported lazily
        conn = psycopg.connect(DATABASE_URL, autocommit=True)
        return conn

    path = SQLITE_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def connection():
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _local.conn = _connect()
    ensure_schema()
    return conn


def _adapt(sql: str) -> str:
    """`?` placeholders are portable in this codebase; Postgres wants `%s`."""
    return sql.replace("?", "%s") if dialect() == "postgres" else sql


def query(sql: str, params: tuple = ()) -> list[dict]:
    conn = connection()
    cur = conn.cursor()
    cur.execute(_adapt(sql), params)
    cols = [d[0] for d in cur.description] if cur.description else []
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.close()
    return rows


def query_one(sql: str, params: tuple = ()) -> Optional[dict]:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple = (), returning_id: bool = False) -> Any:
    conn = connection()
    cur = conn.cursor()
    if returning_id and dialect() == "postgres":
        cur.execute(_adapt(sql) + " RETURNING id", params)
        new_id = cur.fetchone()[0]
        cur.close()
        return new_id
    cur.execute(_adapt(sql), params)
    new_id = cur.lastrowid if returning_id else None
    if dialect() == "sqlite":
        conn.commit()
    cur.close()
    return new_id


def now() -> str:
    return datetime.utcnow().isoformat()


# ── Schema ────────────────────────────────────────────────────────────────────

def _schema_statements() -> list[str]:
    pk = ("INTEGER PRIMARY KEY AUTOINCREMENT" if dialect() == "sqlite"
          else "SERIAL PRIMARY KEY")
    return [
        f"""CREATE TABLE IF NOT EXISTS users (
              id            {pk},
              email         TEXT UNIQUE NOT NULL,
              password_hash TEXT NOT NULL,
              vertical      TEXT,
              created_at    TEXT NOT NULL,
              plan          TEXT,
              trial_started_at      TEXT,
              trial_ends_at         TEXT,
              plan_updated_at       TEXT,
              stripe_customer_id    TEXT,
              stripe_subscription_id TEXT
            )""",
        f"""CREATE TABLE IF NOT EXISTS watchlist (
              id         {pk},
              user_id    INTEGER NOT NULL,
              keyword    TEXT NOT NULL,
              category   TEXT,
              added_at   TEXT NOT NULL,
              viability_at_add INTEGER
            )""",
        """CREATE UNIQUE INDEX IF NOT EXISTS watchlist_user_kw
             ON watchlist (user_id, keyword)""",
        f"""CREATE TABLE IF NOT EXISTS notes (
              id         {pk},
              user_id    INTEGER NOT NULL,
              keyword    TEXT NOT NULL,
              body       TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )""",
        """CREATE INDEX IF NOT EXISTS notes_user_kw ON notes (user_id, keyword)""",
        # The outcome table is the actual moat: it is the only place where a
        # recommendation gets connected to what happened next.
        f"""CREATE TABLE IF NOT EXISTS outcomes (
              id                    {pk},
              user_id               INTEGER NOT NULL,
              keyword               TEXT NOT NULL,
              stage                 TEXT NOT NULL,
              decided_at            TEXT NOT NULL,
              viability_at_decision INTEGER,
              result                TEXT,
              revenue_band          TEXT,
              would_repeat          INTEGER,
              notes                 TEXT,
              created_at            TEXT NOT NULL,
              updated_at            TEXT NOT NULL
            )""",
        """CREATE INDEX IF NOT EXISTS outcomes_keyword ON outcomes (keyword)""",
        """CREATE UNIQUE INDEX IF NOT EXISTS outcomes_user_kw
             ON outcomes (user_id, keyword)""",
        # Quota ledger. One row per (user, day, feature, subject), so the unit
        # metered is a *distinct* subject rather than a request: re-reading one
        # keyword's chart all afternoon is free, and breadth — the shape actual
        # extraction takes — is what runs the counter up.
        f"""CREATE TABLE IF NOT EXISTS usage_events (
              id         {pk},
              user_id    INTEGER NOT NULL,
              day        TEXT NOT NULL,
              feature    TEXT NOT NULL,
              subject    TEXT NOT NULL,
              created_at TEXT NOT NULL
            )""",
        """CREATE UNIQUE INDEX IF NOT EXISTS usage_unique
             ON usage_events (user_id, day, feature, subject)""",
        """CREATE INDEX IF NOT EXISTS usage_user_day
             ON usage_events (user_id, day, feature)""",
    ]


# Columns added after the first release. CREATE TABLE IF NOT EXISTS silently
# does nothing to an existing table, so a database made before plans existed
# needs these bolted on explicitly or every plan read returns NULL.
_ADDED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "users": [
        ("plan",                   "TEXT"),
        ("trial_started_at",       "TEXT"),
        ("trial_ends_at",          "TEXT"),
        ("plan_updated_at",        "TEXT"),
        ("stripe_customer_id",     "TEXT"),
        ("stripe_subscription_id", "TEXT"),
    ],
}


def _existing_columns(cur, table: str) -> set:
    if dialect() == "sqlite":
        cur.execute(f"PRAGMA table_info({table})")
        return {r[1] for r in cur.fetchall()}
    cur.execute("SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s", (table,))
    return {r[0] for r in cur.fetchall()}


def _migrate(cur) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        have = _existing_columns(cur, table)
        for name, decl in columns:
            if name not in have:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                log.info("db: added %s.%s", table, name)


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        conn = getattr(_local, "conn", None) or _connect()
        _local.conn = conn
        cur = conn.cursor()
        for stmt in _schema_statements():
            cur.execute(stmt)
        _migrate(cur)
        if dialect() == "sqlite":
            conn.commit()
        cur.close()
        _schema_ready = True
        log.info("db: schema ready (%s)", dialect())


# ── Users ─────────────────────────────────────────────────────────────────────

def create_user(email: str, password_hash: str, vertical: Optional[str] = None,
                plan: Optional[str] = None, trial_ends_at: Optional[str] = None) -> int:
    """Create an account. Callers pass the starting plan so that signup and the
    start of the trial clock are one write — a trial that has to be granted by a
    second statement is a trial that some code path will forget to grant."""
    ts = now()
    return execute(
        "INSERT INTO users (email, password_hash, vertical, created_at, plan, "
        "trial_started_at, trial_ends_at, plan_updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (email.lower().strip(), password_hash, vertical, ts, plan,
         ts if trial_ends_at else None, trial_ends_at, ts),
        returning_id=True,
    )


def user_by_email(email: str) -> Optional[dict]:
    return query_one("SELECT * FROM users WHERE email = ?", (email.lower().strip(),))


def user_by_id(user_id: int) -> Optional[dict]:
    return query_one("SELECT * FROM users WHERE id = ?", (user_id,))


def set_user_vertical(user_id: int, vertical: Optional[str]) -> None:
    execute("UPDATE users SET vertical = ? WHERE id = ?", (vertical, user_id))


# ── Plans and billing ─────────────────────────────────────────────────────────

def set_plan(user_id: int, plan: str, *, trial_ends_at: Optional[str] = None,
             stripe_customer_id: Optional[str] = None,
             stripe_subscription_id: Optional[str] = None) -> None:
    """Move an account between plans.

    Only non-None billing identifiers overwrite what is stored: Stripe sends the
    customer id on some webhook events and not others, and a plain UPDATE would
    blank the column on every event that omits it.
    """
    sets   = ["plan = ?", "plan_updated_at = ?"]
    params: list = [plan, now()]
    if trial_ends_at is not None:
        sets.append("trial_ends_at = ?");          params.append(trial_ends_at)
    if stripe_customer_id is not None:
        sets.append("stripe_customer_id = ?");     params.append(stripe_customer_id)
    if stripe_subscription_id is not None:
        sets.append("stripe_subscription_id = ?"); params.append(stripe_subscription_id)
    params.append(user_id)
    execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", tuple(params))


def start_trial(user_id: int, trial_ends_at: str) -> None:
    execute("UPDATE users SET plan = ?, trial_started_at = ?, trial_ends_at = ?, "
            "plan_updated_at = ? WHERE id = ?",
            ("trial", now(), trial_ends_at, now(), user_id))


def user_by_stripe_customer(customer_id: str) -> Optional[dict]:
    """Lookup path for billing webhooks, which know the customer, not the email."""
    return query_one("SELECT * FROM users WHERE stripe_customer_id = ?", (customer_id,))


# ── Usage quota ───────────────────────────────────────────────────────────────

def today() -> str:
    return datetime.utcnow().date().isoformat()


def quota_used(user_id: int, feature: str, day: Optional[str] = None) -> int:
    row = query_one("SELECT COUNT(*) AS n FROM usage_events "
                    "WHERE user_id = ? AND feature = ? AND day = ?",
                    (user_id, feature, day or today()))
    return int(row["n"]) if row else 0


def quota_charge(user_id: int, feature: str, subject: str,
                 limit: Optional[int]) -> dict:
    """Record one distinct subject against today's allowance.

    Returns {allowed, used, limit, charged}. A subject already seen today is
    always allowed and never charged twice, so the quota measures how much of the
    corpus an account has touched rather than how hard it clicked.

    Two concurrent requests can both observe used == limit - 1 and both pass.
    That is accepted: this is a quota, not a lock, and paying for a distributed
    lock to prevent a single extra row is the wrong trade.
    """
    day     = day_str = today()
    subject = (subject or "").strip().lower()[:200]

    if query_one("SELECT id FROM usage_events WHERE user_id = ? AND day = ? "
                 "AND feature = ? AND subject = ?", (user_id, day, feature, subject)):
        return {"allowed": True, "used": quota_used(user_id, feature, day),
                "limit": limit, "charged": False}

    used = quota_used(user_id, feature, day)
    if limit is not None and used >= limit:
        return {"allowed": False, "used": used, "limit": limit, "charged": False}

    try:
        execute("INSERT INTO usage_events (user_id, day, feature, subject, created_at) "
                "VALUES (?, ?, ?, ?, ?)", (user_id, day_str, feature, subject, now()))
    except Exception:
        # Lost a race against a concurrent identical charge; the row exists.
        log.debug("db: duplicate usage charge for %s/%s", feature, subject)
        return {"allowed": True, "used": used, "limit": limit, "charged": False}

    return {"allowed": True, "used": used + 1, "limit": limit, "charged": True}


# ── Watchlist ─────────────────────────────────────────────────────────────────

def add_watch(user_id: int, keyword: str, category: Optional[str] = None,
              viability: Optional[int] = None) -> None:
    existing = query_one(
        "SELECT id FROM watchlist WHERE user_id = ? AND keyword = ?", (user_id, keyword))
    if existing:
        return
    execute(
        "INSERT INTO watchlist (user_id, keyword, category, added_at, viability_at_add) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, keyword, category, now(), viability),
    )


def remove_watch(user_id: int, keyword: str) -> None:
    execute("DELETE FROM watchlist WHERE user_id = ? AND keyword = ?", (user_id, keyword))


def list_watches(user_id: int) -> list[dict]:
    return query(
        "SELECT keyword, category, added_at, viability_at_add FROM watchlist "
        "WHERE user_id = ? ORDER BY added_at DESC", (user_id,))


# ── Notes ─────────────────────────────────────────────────────────────────────

def upsert_note(user_id: int, keyword: str, body: str) -> None:
    existing = query_one(
        "SELECT id FROM notes WHERE user_id = ? AND keyword = ?", (user_id, keyword))
    if existing:
        execute("UPDATE notes SET body = ?, updated_at = ? WHERE id = ?",
                (body, now(), existing["id"]))
    else:
        execute("INSERT INTO notes (user_id, keyword, body, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)", (user_id, keyword, body, now(), now()))


def get_note(user_id: int, keyword: str) -> Optional[dict]:
    return query_one(
        "SELECT keyword, body, updated_at FROM notes WHERE user_id = ? AND keyword = ?",
        (user_id, keyword))


def list_notes(user_id: int) -> list[dict]:
    return query("SELECT keyword, body, updated_at FROM notes WHERE user_id = ? "
                 "ORDER BY updated_at DESC", (user_id,))


def delete_note(user_id: int, keyword: str) -> None:
    execute("DELETE FROM notes WHERE user_id = ? AND keyword = ?", (user_id, keyword))


# ── Outcomes ──────────────────────────────────────────────────────────────────

STAGES  = ("considering", "launched", "passed")
RESULTS = ("too_early", "flop", "breakeven", "worked", "hit")
REVENUE_BANDS = ("none", "under_1k", "1k_10k", "10k_50k", "50k_plus")


def upsert_outcome(user_id: int, keyword: str, stage: str,
                   viability_at_decision: Optional[int] = None,
                   result: Optional[str] = None,
                   revenue_band: Optional[str] = None,
                   would_repeat: Optional[bool] = None,
                   notes: Optional[str] = None) -> None:
    existing = query_one(
        "SELECT id FROM outcomes WHERE user_id = ? AND keyword = ?", (user_id, keyword))
    repeat_val = None if would_repeat is None else (1 if would_repeat else 0)

    if existing:
        execute(
            "UPDATE outcomes SET stage = ?, result = ?, revenue_band = ?, "
            "would_repeat = ?, notes = ?, updated_at = ? WHERE id = ?",
            (stage, result, revenue_band, repeat_val, notes, now(), existing["id"]),
        )
    else:
        execute(
            "INSERT INTO outcomes (user_id, keyword, stage, decided_at, "
            "viability_at_decision, result, revenue_band, would_repeat, notes, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, keyword, stage, now(), viability_at_decision, result,
             revenue_band, repeat_val, notes, now(), now()),
        )


def get_outcome(user_id: int, keyword: str) -> Optional[dict]:
    return query_one("SELECT * FROM outcomes WHERE user_id = ? AND keyword = ?",
                     (user_id, keyword))


def list_outcomes(user_id: int) -> list[dict]:
    return query("SELECT * FROM outcomes WHERE user_id = ? ORDER BY updated_at DESC",
                 (user_id,))


def all_outcomes_for_calibration() -> list[dict]:
    """Anonymous, cross-user outcome records — the training signal for the score."""
    return query(
        "SELECT keyword, stage, viability_at_decision, result, revenue_band, "
        "would_repeat, decided_at FROM outcomes WHERE result IS NOT NULL")


def outcome_stats() -> dict:
    rows = query("SELECT stage, COUNT(*) AS n FROM outcomes GROUP BY stage")
    total = sum(r["n"] for r in rows)
    return {"total": total, "by_stage": {r["stage"]: r["n"] for r in rows}}
