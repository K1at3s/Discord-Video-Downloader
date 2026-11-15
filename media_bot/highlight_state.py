"""Quota, caching and daily/hourly limits for Instagram highlight downloads.

Highlights are fetched via indown.io which enforces ~20 requests/hour per IP.
State is persisted in SQLite (bucket events, per-user counters) so restarts do
not lose quota awareness. File cache is on disk with TTL and size limits.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

LOG = logging.getLogger(__name__)

HIGHLIGHT_RE = re.compile(r"/stories/highlights/(\d+)/?", re.I)
MEDIA_SUFFIXES = {".mp4", ".mov", ".webm", ".m4v", ".jpg", ".jpeg", ".png", ".webp", ".gif"}
ISTANBUL = timezone(timedelta(hours=3))


def highlight_id(url: str) -> str | None:
    if match := HIGHLIGHT_RE.search(urlsplit(url).path):
        return match.group(1)
    return None


def istanbul_day(now: datetime | None = None) -> str:
    return (now or datetime.now(ISTANBUL)).date().isoformat()


def istanbul_hour(now: datetime | None = None) -> str:
    current = now or datetime.now(ISTANBUL)
    return current.strftime("%Y-%m-%dT%H")


class TokenBucket:
    """Sliding-window limiter persisted as event timestamps in SQLite.

    indown.io enforces ~20 requests/hour per IP. The service does not expose a
    parseable "remaining/reset in" value in responses we consume, so this uses a
    local sliding 1-hour window estimate (events older than `period` expire) and
    resynchronizes pessimistically when the service reports rate limiting.
    Uses wall-clock time so state survives restarts. `db_path` may be None for
    in-memory-only behavior (tests).
    """

    def __init__(self, capacity: int = 20, period: float = 3600.0, db_path: Path | None = None):
        self.capacity = capacity
        self.period = period
        self.db_path = db_path
        self._events: list[float] = []
        self._lock = threading.Lock()
        if db_path is not None:
            self._init_db()
            self._load()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=5)  # type: ignore[arg-type]
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def _init_db(self) -> None:
        if self.db_path is None:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS highlight_bucket_events (ts REAL NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS highlight_bucket_events_ts ON highlight_bucket_events(ts)")

    def _load(self) -> None:
        if self.db_path is None:
            return
        cutoff = time.time() - self.period
        try:
            with closing(self._connect()) as db, db:
                db.execute("DELETE FROM highlight_bucket_events WHERE ts <= ?", (cutoff,))
                rows = db.execute("SELECT ts FROM highlight_bucket_events ORDER BY ts").fetchall()
            self._events = [float(r[0]) for r in rows]
        except (OSError, sqlite3.Error) as exc:
            LOG.warning("Highlight bucket load failed: %s", exc)
            self._events = []

    def _persist_add(self, ts: float) -> None:
        if self.db_path is None:
            return
        try:
            with closing(self._connect()) as db, db:
                db.execute("INSERT INTO highlight_bucket_events(ts) VALUES (?)", (ts,))
                db.execute("DELETE FROM highlight_bucket_events WHERE ts <= ?", (time.time() - self.period,))
        except (OSError, sqlite3.Error) as exc:
            LOG.warning("Highlight bucket persist failed: %s", exc)

    def _persist_trim(self, cutoff: float) -> None:
        if self.db_path is None:
            return
        try:
            with closing(self._connect()) as db, db:
                db.execute("DELETE FROM highlight_bucket_events WHERE ts <= ?", (cutoff,))
        except (OSError, sqlite3.Error):
            pass

    def _trim(self, now: float) -> None:
        cutoff = now - self.period
        changed = False
        while self._events and self._events[0] <= cutoff:
            self._events.pop(0)
            changed = True
        if changed:
            self._persist_trim(cutoff)

    def try_consume(self, now: float | None = None) -> tuple[bool, float]:
        now = now if now is not None else time.time()
        with self._lock:
            self._trim(now)
            if len(self._events) >= self.capacity:
                wait = max(0.0, self._events[0] + self.period - now)
                return False, wait
            self._events.append(now)
            self._persist_add(now)
            return True, 0.0

    def mark_exhausted(self, now: float | None = None) -> float:
        """Server reported rate limit: fill bucket to capacity."""
        now = now if now is not None else time.time()
        with self._lock:
            self._trim(now)
            while len(self._events) < self.capacity:
                self._events.append(now)
                self._persist_add(now)
            return max(0.0, self._events[0] + self.period - now)

    def remaining(self, now: float | None = None) -> int:
        now = now if now is not None else time.time()
        with self._lock:
            self._trim(now)
            return max(0, self.capacity - len(self._events))

    def wait_seconds(self, now: float | None = None) -> float:
        now = now if now is not None else time.time()
        with self._lock:
            self._trim(now)
            if len(self._events) < self.capacity:
                return 0.0
            return max(0.0, self._events[0] + self.period - now)


class HighlightUserQuota:
    """Per-user hourly and daily highlight download limits (SQLite-backed).

    Stores (user_id, period, value, count). Retains only recent rows.
    """

    def __init__(self, path: Path, daily_limit: int = 15, hourly_limit: int = 4):
        self.path = path
        self.daily_limit = daily_limit
        self.hourly_limit = hourly_limit
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS highlight_user_quota "
                "(user_id INTEGER NOT NULL, period TEXT NOT NULL, value TEXT NOT NULL, "
                "count INTEGER NOT NULL, PRIMARY KEY(user_id, period, value))"
            )
            self._cleanup(db)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def _cleanup(self, db: sqlite3.Connection, now: datetime | None = None) -> None:
        now = now or datetime.now(ISTANBUL)
        day_cutoff = (now - timedelta(days=2)).date().isoformat()
        hour_cutoff = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H")
        db.execute("DELETE FROM highlight_user_quota WHERE period='day' AND value < ?", (day_cutoff,))
        db.execute("DELETE FROM highlight_user_quota WHERE period='hour' AND value < ?", (hour_cutoff,))
        # Also purge legacy table rows older than 2 days if present.
        try:
            db.execute("DELETE FROM highlight_uses WHERE day < ?", (day_cutoff,))
        except sqlite3.OperationalError:
            pass

    def claim(self, user_id: int, now: datetime | None = None) -> tuple[bool, str | None]:
        """Try to claim one slot. Returns (ok, error_message)."""
        now = now or datetime.now(ISTANBUL)
        day = istanbul_day(now)
        hour = istanbul_hour(now)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self._cleanup(db, now)
            # hourly
            hour_row = db.execute(
                "SELECT count FROM highlight_user_quota WHERE user_id=? AND period='hour' AND value=?",
                (user_id, hour),
            ).fetchone()
            if hour_row and hour_row[0] >= self.hourly_limit:
                return False, "Bu saat için öne çıkan indirme limitine ulaştın. Bir süre sonra tekrar dene."
            # daily
            day_row = db.execute(
                "SELECT count FROM highlight_user_quota WHERE user_id=? AND period='day' AND value=?",
                (user_id, day),
            ).fetchone()
            if day_row and day_row[0] >= self.daily_limit:
                return False, "Günlük öne çıkan indirme limitine ulaştın. Yarın tekrar deneyebilirsin."
            # increment both
            db.execute(
                "INSERT INTO highlight_user_quota(user_id, period, value, count) VALUES(?,?,?,1) "
                "ON CONFLICT(user_id, period, value) DO UPDATE SET count=count+1",
                (user_id, "hour", hour),
            )
            db.execute(
                "INSERT INTO highlight_user_quota(user_id, period, value, count) VALUES(?,?,?,1) "
                "ON CONFLICT(user_id, period, value) DO UPDATE SET count=count+1",
                (user_id, "day", day),
            )
            return True, None

    def release(self, user_id: int, now: datetime | None = None) -> None:
        now = now or datetime.now(ISTANBUL)
        day = istanbul_day(now)
        hour = istanbul_hour(now)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            for period, value in (("hour", hour), ("day", day)):
                row = db.execute(
                    "SELECT count FROM highlight_user_quota WHERE user_id=? AND period=? AND value=?",
                    (user_id, period, value),
                ).fetchone()
                if not row:
                    continue
                if row[0] <= 1:
                    db.execute(
                        "DELETE FROM highlight_user_quota WHERE user_id=? AND period=? AND value=?",
                        (user_id, period, value),
                    )
                else:
                    db.execute(
                        "UPDATE highlight_user_quota SET count=count-1 WHERE user_id=? AND period=? AND value=?",
                        (user_id, period, value),
                    )

    async def aclaim(self, user_id: int) -> tuple[bool, str | None]:
        return await asyncio.to_thread(self.claim, user_id)

    async def arelease(self, user_id: int) -> None:
        await asyncio.to_thread(self.release, user_id)


@dataclass
class CacheEntry:
    path: Path
    files: list[str]
    created_at: float


class HighlightCache:
    def __init__(self, root: Path, ttl: float = 12 * 3600, max_bytes: int = 512 * 1024 * 1024):
        self.root = root
        self.ttl = ttl
        self.max_bytes = max_bytes
        self._inflight: dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()

    def _dir(self, hid: str) -> Path:
        return self.root / hid

    def _meta_path(self, hid: str) -> Path:
        return self._dir(hid) / "meta.json"

    def _entry_size(self, base: Path, files: list[str]) -> int:
        total = 0
        for name in files:
            try:
                total += (base / name).stat().st_size
            except OSError:
                pass
        return total

    def cleanup(self) -> None:
        if not self.root.exists():
            return
        now = time.time()
        entries: list[tuple[float, Path, int]] = []
        for child in self.root.iterdir():
            if not child.is_dir():
                continue
            meta_path = child / "meta.json"
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                created = float(meta.get("created_at", 0))
                files = [str(f) for f in meta.get("files", []) if isinstance(f, str)]
            except (OSError, ValueError, TypeError):
                shutil.rmtree(child, ignore_errors=True)
                continue
            if now - created > self.ttl:
                shutil.rmtree(child, ignore_errors=True)
                continue
            entries.append((created, child, self._entry_size(child, files)))
        total = sum(size for _, _, size in entries)
        if total <= self.max_bytes:
            return
        # Evict oldest first until under limit.
        for _, child, size in sorted(entries, key=lambda e: e[0]):
            shutil.rmtree(child, ignore_errors=True)
            total -= size
            if total <= self.max_bytes:
                break

    def get(self, hid: str) -> CacheEntry | None:
        meta_path = self._meta_path(hid)
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        created = float(meta.get("created_at", 0))
        if time.time() - created > self.ttl:
            shutil.rmtree(self._dir(hid), ignore_errors=True)
            return None
        files = [str(f) for f in meta.get("files", []) if isinstance(f, str)]
        base = self._dir(hid)
        resolved = [name for name in files if (base / name).is_file()]
        if not resolved:
            shutil.rmtree(base, ignore_errors=True)
            return None
        return CacheEntry(path=base, files=resolved, created_at=created)

    def put(self, hid: str, source_folder: Path) -> CacheEntry | None:
        base = self._dir(hid)
        try:
            self.cleanup()
            if base.exists():
                shutil.rmtree(base, ignore_errors=True)
            base.mkdir(parents=True, exist_ok=True)
            files: list[str] = []
            for p in sorted(source_folder.iterdir()):
                if p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES:
                    dest = base / p.name
                    shutil.copy2(p, dest)
                    files.append(p.name)
            if not files:
                shutil.rmtree(base, ignore_errors=True)
                return None
            # Skip caching if this single entry exceeds max (shouldn't happen often).
            if self._entry_size(base, files) > self.max_bytes:
                shutil.rmtree(base, ignore_errors=True)
                return None
            meta = {"created_at": time.time(), "files": files}
            self._meta_path(hid).write_text(json.dumps(meta), encoding="utf-8")
            self.cleanup()
            return CacheEntry(path=base, files=files, created_at=meta["created_at"])
        except OSError as exc:
            LOG.warning("Highlight cache write failed for %s: %s", hid, exc)
            return None

    def stats(self) -> dict:
        out = {
            "entries": 0,
            "files": 0,
            "bytes": 0,
            "stale": 0,
            "max_bytes": self.max_bytes,
            "ttl": self.ttl,
            "path": str(self.root),
        }
        if not self.root.exists():
            return out
        now = time.time()
        for child in self.root.iterdir():
            if not child.is_dir():
                continue
            try:
                meta = json.loads((child / "meta.json").read_text(encoding="utf-8"))
                created = float(meta.get("created_at", 0))
                names = [str(f) for f in meta.get("files", []) if isinstance(f, str)]
            except (OSError, ValueError, TypeError):
                out["stale"] += 1
                continue
            resolved = [name for name in names if (child / name).is_file()]
            if not resolved or now - created > self.ttl:
                out["stale"] += 1
                continue
            out["entries"] += 1
            out["files"] += len(resolved)
            out["bytes"] += self._entry_size(child, resolved)
        return out

    def clear(self) -> int:
        if not self.root.exists():
            return 0
        removed = sum(1 for child in self.root.iterdir() if child.is_dir() and (child / "meta.json").is_file())
        for child in list(self.root.iterdir()):
            try:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
            except OSError as exc:
                LOG.warning("Highlight cache clear failed for %s: %s", child, exc)
        return removed

    def copy_to(self, entry: CacheEntry, target_folder: Path) -> list[Path]:
        target_folder.mkdir(parents=True, exist_ok=True)
        copied: list[Path] = []
        for name in entry.files:
            src = entry.path / name
            if not src.is_file():
                continue
            dest = target_folder / name
            try:
                shutil.copy2(src, dest)
                copied.append(dest)
            except OSError:
                continue
        return copied

    async def singleflight_start(self, hid: str):
        async with self._lock:
            if hid in self._inflight:
                return False, self._inflight[hid]
            fut = asyncio.get_running_loop().create_future()
            self._inflight[hid] = fut
            return True, fut

    async def singleflight_finish(self, hid: str, result, exc: Exception | None = None):
        async with self._lock:
            fut = self._inflight.pop(hid, None)
        if fut and not fut.done():
            if exc is not None:
                fut.set_exception(exc)
            else:
                fut.set_result(result)


class HighlightState:
    def __init__(self, db_path: Path | None = None, cache_root: Path | None = None):
        self.bucket = TokenBucket(capacity=20, period=3600.0)
        self.user_quota: HighlightUserQuota | None = None
        self.cache: HighlightCache | None = None
        # Prevent concurrent "window-end" probes when local bucket estimate is full.
        self.probe_lock = asyncio.Lock()
        self.health_check_enabled = True
        self.health_check_interval = 24 * 3600
        self.health_check_reserve = 3
        self._configured = False
        if db_path is not None:
            self.configure(db_path, cache_root)

    def configure(
        self,
        db_path: Path,
        cache_root: Path | None = None,
        *,
        cache_ttl: float = 12 * 3600,
        cache_max_bytes: int = 512 * 1024 * 1024,
        daily_limit: int = 15,
        hourly_limit: int = 4,
        bucket_capacity: int = 20,
        bucket_period: float = 3600.0,
        health_check_enabled: bool = True,
        health_check_interval: float = 24 * 3600,
        health_check_reserve: int = 3,
    ):
        cache_root = cache_root or db_path.parent / "highlight_cache"
        self.bucket = TokenBucket(capacity=bucket_capacity, period=bucket_period, db_path=db_path)
        self.user_quota = HighlightUserQuota(db_path, daily_limit=daily_limit, hourly_limit=hourly_limit)
        self.cache = HighlightCache(cache_root, ttl=cache_ttl, max_bytes=cache_max_bytes)
        self.probe_lock = asyncio.Lock()
        self.health_check_enabled = health_check_enabled
        self.health_check_interval = max(60.0, float(health_check_interval))
        self.health_check_reserve = max(0, int(health_check_reserve))
        try:
            self.cache.cleanup()
        except OSError:
            pass
        self._configured = True

    @property
    def enabled(self) -> bool:
        return self._configured

    # Backward-compatible alias used by existing code paths; prefer user_quota.
    @property
    def daily(self) -> HighlightUserQuota | None:
        return self.user_quota


HIGHLIGHTS = HighlightState()


def format_wait(seconds: float) -> str:
    mins = max(1, int((seconds + 59) // 60))
    if mins >= 60:
        h = mins // 60
        m = mins % 60
        return f"{h} saat {m} dakika" if m else f"{h} saat"
    return f"{mins} dakika"
