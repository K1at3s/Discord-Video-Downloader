"""Daily SQLite snapshots so request logs and quotas survive disk trouble."""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import time
from collections.abc import Sequence
from contextlib import closing
from datetime import datetime
from pathlib import Path

LOG = logging.getLogger(__name__)


def backup_databases(paths: Sequence[Path], root: Path, keep: int = 7,
                     *, now: float | None = None, max_age_days: int = 0) -> Path | None:
    """Copy every existing database into one timestamped folder.

    ``sqlite3.Connection.backup`` is used instead of ``shutil.copy`` so a write
    in progress cannot produce a torn file. Returns the created folder, or
    ``None`` when there was nothing to back up.
    """
    existing = [Path(path) for path in paths if Path(path).is_file()]
    if not existing:
        return None
    stamp = datetime.fromtimestamp(time.time() if now is None else now)
    folder = Path(root) / stamp.strftime("%Y%m%d-%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    copied = 0
    for source in existing:
        target = folder / source.name
        try:
            with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=10)) as src, \
                    closing(sqlite3.connect(target, timeout=10)) as dst:
                src.backup(dst)
            copied += 1
        except sqlite3.Error as exc:
            LOG.warning("Yedek alınamadı (%s): %s", source.name, exc)
            target.unlink(missing_ok=True)
            # A raw copy is still useful for a database that is not valid SQLite.
            try:
                shutil.copy2(source, target)
                copied += 1
            except OSError as copy_exc:
                LOG.warning("Dosya kopyası da başarısız (%s): %s", source.name, copy_exc)
    if not copied:
        shutil.rmtree(folder, ignore_errors=True)
        return None
    LOG.info("Veritabanı yedeği: %s (%d dosya)", folder, copied)
    prune_backups(root, keep, max_age_days=max_age_days)
    return folder


def latest_backup(root: Path) -> Path | None:
    """Return the newest timestamped backup folder, if any."""
    root = Path(root)
    if not root.is_dir():
        return None
    folders = [
        child for child in root.iterdir()
        if child.is_dir() and len(child.name) == 15 and child.name[8] == "-"
    ]
    return max(folders, key=lambda item: item.name, default=None)


def prune_backups(root: Path, keep: int = 7, *, max_age_days: int = 0,
                   now: float | None = None) -> list[Path]:
    """Delete old timestamped backup folders by count and optional age."""
    root = Path(root)
    if not root.is_dir():
        return []
    folders = sorted(
        (child for child in root.iterdir()
         if child.is_dir() and len(child.name) == 15 and child.name[8] == "-"),
        key=lambda child: child.name,
    )
    removed: list[Path] = []
    if keep >= 1 and len(folders) > keep:
        for folder in folders[:-keep]:
            shutil.rmtree(folder, ignore_errors=True)
            removed.append(folder)
            folders = [f for f in folders if f != folder]
    if max_age_days > 0:
        cutoff = (time.time() if now is None else now) - max_age_days * 86400
        for folder in folders:
            try:
                if folder.stat().st_mtime < cutoff:
                    shutil.rmtree(folder, ignore_errors=True)
                    removed.append(folder)
            except OSError:
                continue
    return removed


def database_paths(database_path: Path) -> list[Path]:
    """The bot's SQLite files next to ``DATABASE_PATH`` (WAL sidecars excluded)."""
    root = Path(database_path).parent
    names = {Path(database_path).name, "provider_metrics.sqlite3",
             "request_log.sqlite3", "guild_settings.sqlite3"}
    return [root / name for name in sorted(names)]


def cleanup_stale_temp_dirs(prefix: str = "discord-media-", max_age_hours: int = 24) -> int:
    """Remove stale temp download directories left behind by crashes."""
    import tempfile

    root = Path(tempfile.gettempdir())
    cutoff = time.time() - max(1, max_age_hours) * 3600
    removed = 0
    for child in root.glob(f"{prefix}*"):
        if not child.is_dir():
            continue
        try:
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    if removed:
        LOG.info("Stale temp dirs cleaned: %d", removed)
    return removed


def backup_enabled(default: bool = True) -> bool:
    raw = os.getenv("BACKUP_ENABLED", "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off", "hayir", "hayır"}
