"""SQLite connections tuned for the bot's small, concurrent stores."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from pathlib import Path

LOG = logging.getLogger(__name__)


def open_db(path: Path | str, timeout: float = 5) -> sqlite3.Connection:
    """Open a connection with WAL and a busy timeout.

    WAL lets a read (stats panels) proceed while an attempt is being recorded.
    It is best effort: databases on filesystems without shared-memory support
    fall back to the default journal mode.
    """
    db = sqlite3.connect(path, timeout=timeout)
    try:
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        db.close()
        raise
    return db


def integrity_check(path: Path | str) -> tuple[bool, str]:
    """Run SQLite integrity_check and return (ok, detail)."""
    try:
        with closing(open_db(path, timeout=10)) as db:
            row = db.execute("PRAGMA integrity_check").fetchone()
    except Exception as exc:
        return False, str(exc)
    detail = str(row[0]) if row else "no result"
    return detail.lower() == "ok", detail


def vacuum(path: Path | str) -> bool:
    """Run VACUUM; returns True on success."""
    try:
        with closing(open_db(path, timeout=30)) as db:
            db.execute("VACUUM")
        return True
    except Exception as exc:
        LOG.warning("SQLite VACUUM failed for %s: %s", path, exc)
        return False


def maintain_databases(paths: list[Path], *, vacuum_enabled: bool = False) -> dict[str, dict[str, str | bool]]:
    """Run integrity checks and optional VACUUM for each database path."""
    results: dict[str, dict[str, str | bool]] = {}
    for path in paths:
        if not path.exists():
            continue
        ok, detail = integrity_check(path)
        entry: dict[str, str | bool] = {"integrity_ok": ok, "integrity": detail}
        if not ok:
            LOG.warning("SQLite integrity_check failed for %s: %s", path, detail)
        if vacuum_enabled and ok:
            entry["vacuumed"] = vacuum(path)
        results[str(path)] = entry
    return results
