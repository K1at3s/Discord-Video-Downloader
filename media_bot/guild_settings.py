"""Per-guild preferences such as auto-threading and the default YouTube quality."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path

from .sqlite_util import open_db

LOG = logging.getLogger(__name__)

QUALITY_CHOICES = ("sor", "mp3", "360", "720", "1080")
DEFAULTS = {"auto_thread": False, "youtube_quality": "sor"}


@dataclass(frozen=True)
class GuildSettings:
    auto_thread: bool = False
    youtube_quality: str = "sor"


class GuildSettingsStore:
    def __init__(self, path: Path | None = None):
        self.path = path
        self._lock = threading.Lock()
        if path is not None:
            self._migrate()

    def configure(self, path: Path | None) -> None:
        self.path = path
        if path is not None:
            self._migrate()

    def _migrate(self) -> None:
        if self.path is None:
            return
        with self._lock, closing(open_db(self.path)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS guild_settings ("
                "guild_id INTEGER PRIMARY KEY, "
                "auto_thread INTEGER NOT NULL DEFAULT 0, "
                "youtube_quality TEXT NOT NULL DEFAULT 'sor', "
                "updated_at INTEGER NOT NULL DEFAULT 0)"
            )

    def get(self, guild_id: int) -> GuildSettings:
        if self.path is None or not guild_id:
            return GuildSettings()
        try:
            with self._lock, closing(open_db(self.path)) as db:
                row = db.execute(
                    "SELECT auto_thread, youtube_quality FROM guild_settings WHERE guild_id = ?",
                    (guild_id,),
                ).fetchone()
        except Exception as exc:
            LOG.warning("Sunucu ayarları okunamadı: %s", exc)
            return GuildSettings()
        if not row:
            return GuildSettings()
        quality = row[1] if row[1] in QUALITY_CHOICES else "sor"
        return GuildSettings(auto_thread=bool(row[0]), youtube_quality=quality)

    def update(self, guild_id: int, **fields) -> GuildSettings:
        """Validate and persist a partial change, returning the new settings."""
        current = self.get(guild_id)
        changes = {}
        if "auto_thread" in fields and fields["auto_thread"] is not None:
            changes["auto_thread"] = bool(fields["auto_thread"])
        if "youtube_quality" in fields and fields["youtube_quality"] is not None:
            quality = str(fields["youtube_quality"]).strip().lower()
            if quality not in QUALITY_CHOICES:
                raise ValueError(f"Geçersiz kalite: {quality}")
            changes["youtube_quality"] = quality
        if not changes:
            return current
        updated = replace(current, **changes)
        if self.path is not None and guild_id:
            try:
                with self._lock, closing(open_db(self.path)) as db, db:
                    db.execute(
                        "INSERT INTO guild_settings (guild_id, auto_thread, youtube_quality, updated_at) "
                        "VALUES (?, ?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET "
                        "auto_thread = excluded.auto_thread, "
                        "youtube_quality = excluded.youtube_quality, "
                        "updated_at = excluded.updated_at",
                        (guild_id, int(updated.auto_thread), updated.youtube_quality, int(time.time())),
                    )
            except Exception as exc:
                LOG.warning("Sunucu ayarları yazılamadı: %s", exc)
                raise
        return updated


SETTINGS = GuildSettingsStore()


def describe(settings: GuildSettings) -> str:
    thread = "açık" if settings.auto_thread else "kapalı"
    return (f"🧵 Otomatik thread: **{thread}**\n"
            f"🎬 YouTube varsayılanı: **{settings.youtube_quality}**")
