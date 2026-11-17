"""SQLite connections tuned for the bot's small, concurrent stores."""

from __future__ import annotations

import sqlite3
from pathlib import Path


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
