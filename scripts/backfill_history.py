"""One-off backfill: recover the time series already sitting in git history.

The nightly job has been committing cache/trends.json since 2026-07-02. Each of
those commits is an observation of every tracked keyword on that date — several
weeks of data that no competitor can acquire retroactively. This walks the git
log, reads each historical blob, and replays it into the snapshot store.

Safe to re-run: snapshots are keyed by date and overwritten in place. Snapshots
already written by the nightly job are preserved unless --force is passed.

Usage:
    python scripts/backfill_history.py [--force] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trendpulse import history

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("backfill")

TRACKED = "cache/trends.json"


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        capture_output=True, text=True, check=True,
        cwd=Path(__file__).resolve().parent.parent,
    ).stdout


def commits_touching_cache() -> list[tuple[str, str]]:
    """(sha, YYYY-MM-DD) for every commit that changed trends.json, oldest first."""
    raw = git("log", "--reverse", "--format=%H %ad", "--date=short", "--", TRACKED)
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        sha, _, day = line.partition(" ")
        out.append((sha, day.strip()))
    return out


def blob_at(sha: str) -> dict | None:
    try:
        return json.loads(git("show", f"{sha}:{TRACKED}"))
    except subprocess.CalledProcessError:
        return None          # file didn't exist at that commit
    except json.JSONDecodeError:
        log.warning("  %s: unparseable trends.json — skipping", sha[:8])
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="overwrite snapshots that already exist")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    commits = commits_touching_cache()
    if not commits:
        log.error("No commits found touching %s — is this a git checkout?", TRACKED)
        return 1

    log.info("Found %d commits touching %s (%s → %s)",
             len(commits), TRACKED, commits[0][1], commits[-1][1])

    existing = set(history.snapshot_days())
    written, skipped, empty = 0, 0, 0

    # Oldest first, so when a day has several commits the last one wins.
    for sha, day in commits:
        if day in existing and not args.force:
            skipped += 1
            continue

        data = blob_at(sha)
        if not data:
            empty += 1
            continue

        keywords = data.get("keywords") or []
        if not keywords:
            empty += 1
            continue

        if args.dry_run:
            log.info("  would write %s ← %s (%d keywords)", day, sha[:8], len(keywords))
        else:
            history.write_snapshot(keywords, day=day, source=f"backfill:{sha[:8]}")
        written += 1

    log.info("Backfill complete — %d written, %d skipped (already present), %d empty/unreadable",
             written, skipped, empty)

    if not args.dry_run and written:
        log.info("Rebuilding rolled-up series…")
        series = history.rebuild_series()
        history.rebuild_movers(series=series)
        history.rebuild_deltas(series=series)
        cov = history.coverage()
        log.info("Coverage: %d snapshots, %s → %s, %d keywords",
                 cov["snapshot_count"], cov["first_date"], cov["last_date"],
                 cov["keyword_count"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
