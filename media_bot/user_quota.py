"""Per-user hourly and daily link quota backed by SQLite.

The quota is intentionally separate from the +18 daily quota: those posts keep
their own stricter limit. A limit of ``0`` disables that window, and the bot
owner is never counted by the caller.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .sqlite_util import open_db

# Turkey has used UTC+3 all year since 2016, matching adult.istanbul_day().
DAY_OFFSET_SECONDS = 3 * 3600


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    kind: str = ""
    limit: int = 0
    retry_after: int = 0
    remaining: int = 0


def _hour_key(now: float) -> int:
    return int(now // 3600)


def _day_key(now: float) -> int:
    return int((now + DAY_OFFSET_SECONDS) // 86400)


class _WindowQuota:
    table: str
    id_column: str

    def __init__(self, path: Path, hourly_limit: int = 0, daily_limit: int = 0):
        self.path = path
        self.hourly_limit = max(0, int(hourly_limit))
        self.daily_limit = max(0, int(daily_limit))
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute(
                f"CREATE TABLE IF NOT EXISTS {self.table} ("
                f"{self.id_column} INTEGER PRIMARY KEY, "
                "hour_start INTEGER NOT NULL DEFAULT 0, "
                "hour_count INTEGER NOT NULL DEFAULT 0, "
                "day_start INTEGER NOT NULL DEFAULT 0, "
                "day_count INTEGER NOT NULL DEFAULT 0)"
            )

    def _connect(self) -> sqlite3.Connection:
        db = open_db(self.path, timeout=5)
        db.execute("PRAGMA busy_timeout=5000")
        return db

    @property
    def enabled(self) -> bool:
        return bool(self.hourly_limit or self.daily_limit)

    def claim(self, entity_id: int, now: float | None = None) -> QuotaDecision:
        """Atomically consume one request from both windows when allowed."""
        if not self.enabled:
            return QuotaDecision(allowed=True)
        now = time.time() if now is None else float(now)
        hour_key = _hour_key(now)
        day_key = _day_key(now)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                f"SELECT hour_start, hour_count, day_start, day_count "
                f"FROM {self.table} WHERE {self.id_column} = ?",
                (entity_id,),
            ).fetchone()
            hour_count = int(row[1]) if row and int(row[0]) == hour_key else 0
            day_count = int(row[3]) if row and int(row[2]) == day_key else 0

            blocked: list[tuple[int, str, int]] = []
            if self.hourly_limit and hour_count >= self.hourly_limit:
                blocked.append((int((hour_key + 1) * 3600 - now), "hourly", self.hourly_limit))
            if self.daily_limit and day_count >= self.daily_limit:
                wait = int((day_key + 1) * 86400 - (now + DAY_OFFSET_SECONDS))
                blocked.append((max(1, wait), "daily", self.daily_limit))
            if blocked:
                retry_after, kind, limit = max(blocked, key=lambda item: item[0])
                return QuotaDecision(
                    allowed=False, kind=kind, limit=limit,
                    retry_after=max(1, retry_after),
                )

            hour_count += 1
            day_count += 1
            db.execute(
                f"INSERT INTO {self.table} "
                f"({self.id_column}, hour_start, hour_count, day_start, day_count) "
                "VALUES (?, ?, ?, ?, ?) "
                f"ON CONFLICT({self.id_column}) DO UPDATE SET "
                "hour_start = excluded.hour_start, "
                "hour_count = excluded.hour_count, "
                "day_start = excluded.day_start, "
                "day_count = excluded.day_count",
                (entity_id, hour_key, hour_count, day_key, day_count),
            )
            remaining = []
            if self.hourly_limit:
                remaining.append(self.hourly_limit - hour_count)
            if self.daily_limit:
                remaining.append(self.daily_limit - day_count)
            return QuotaDecision(allowed=True, remaining=max(0, min(remaining or [0])))

    async def aclaim(self, entity_id: int, now: float | None = None) -> QuotaDecision:
        return await asyncio.to_thread(self.claim, entity_id, now)

    def summary(self, now: float | None = None) -> dict[str, int]:
        """Aggregate current-window usage across all tracked entities."""
        now = time.time() if now is None else float(now)
        hour_key = _hour_key(now)
        day_key = _day_key(now)
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT hour_start, hour_count, day_start, day_count FROM {self.table}"
            ).fetchall()
        return {
            "entities": len(rows),
            "hourly": sum(int(row[1]) for row in rows if int(row[0]) == hour_key),
            "daily": sum(int(row[3]) for row in rows if int(row[2]) == day_key),
            "hourly_limit": self.hourly_limit,
            "daily_limit": self.daily_limit,
        }

    def usage(self, entity_id: int, now: float | None = None) -> dict[str, int]:
        """Return current window usage without consuming a token."""
        now = time.time() if now is None else float(now)
        hour_key = _hour_key(now)
        day_key = _day_key(now)
        with closing(self._connect()) as db:
            row = db.execute(
                f"SELECT hour_start, hour_count, day_start, day_count "
                f"FROM {self.table} WHERE {self.id_column} = ?",
                (entity_id,),
            ).fetchone()
        hour_count = int(row[1]) if row and int(row[0]) == hour_key else 0
        day_count = int(row[3]) if row and int(row[2]) == day_key else 0
        return {
            "hourly": hour_count,
            "hourly_limit": self.hourly_limit,
            "daily": day_count,
            "daily_limit": self.daily_limit,
        }


class UserQuota(_WindowQuota):
    table = "user_quota"
    id_column = "user_id"

    def summary(self, now: float | None = None) -> dict[str, int]:
        data = super().summary(now)
        data["users"] = data.pop("entities")
        return data


class GuildQuota(_WindowQuota):
    table = "guild_quota"
    id_column = "guild_id"

    def summary(self, now: float | None = None) -> dict[str, int]:
        data = super().summary(now)
        data["guilds"] = data.pop("entities")
        return data
