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
LOCALE_CHOICES = ("auto", "tr", "en")
DEFAULTS = {"auto_thread": False, "youtube_quality": "sor", "locale": "tr"}


@dataclass(frozen=True)
class GuildSettings:
    auto_thread: bool = False
    youtube_quality: str = "sor"
    reply_messages: bool = True
    locale: str = "tr"


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
                "reply_messages INTEGER NOT NULL DEFAULT 1, "
                "locale TEXT NOT NULL DEFAULT 'tr', "
                "updated_at INTEGER NOT NULL DEFAULT 0)"
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(guild_settings)")}
            if "reply_messages" not in columns:
                db.execute("ALTER TABLE guild_settings ADD COLUMN reply_messages INTEGER NOT NULL DEFAULT 1")
            if "locale" not in columns:
                db.execute("ALTER TABLE guild_settings ADD COLUMN locale TEXT NOT NULL DEFAULT 'tr'")

    def get(self, guild_id: int) -> GuildSettings:
        if self.path is None or not guild_id:
            return GuildSettings()
        try:
            with self._lock, closing(open_db(self.path)) as db:
                row = db.execute(
                    "SELECT auto_thread, youtube_quality, reply_messages, locale "
                    "FROM guild_settings WHERE guild_id = ?",
                    (guild_id,),
                ).fetchone()
        except Exception as exc:
            LOG.warning("Sunucu ayarları okunamadı: %s", exc)
            return GuildSettings()
        if not row:
            return GuildSettings()
        quality = row[1] if row[1] in QUALITY_CHOICES else "sor"
        reply_messages = True if row[2] is None else bool(row[2])
        locale = row[3] if row[3] in LOCALE_CHOICES else "tr"
        return GuildSettings(auto_thread=bool(row[0]), youtube_quality=quality,
                             reply_messages=reply_messages, locale=locale)

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
        if "reply_messages" in fields and fields["reply_messages"] is not None:
            changes["reply_messages"] = bool(fields["reply_messages"])
        if "locale" in fields and fields["locale"] is not None:
            locale = str(fields["locale"]).strip().lower()
            if locale not in LOCALE_CHOICES:
                raise ValueError(f"Geçersiz dil: {locale}")
            changes["locale"] = locale
        if not changes:
            return current
        updated = replace(current, **changes)
        if self.path is not None and guild_id:
            try:
                with self._lock, closing(open_db(self.path)) as db, db:
                    db.execute(
                        "INSERT INTO guild_settings (guild_id, auto_thread, youtube_quality, reply_messages, locale, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET "
                        "auto_thread = excluded.auto_thread, "
                        "youtube_quality = excluded.youtube_quality, "
                        "reply_messages = excluded.reply_messages, "
                        "locale = excluded.locale, "
                        "updated_at = excluded.updated_at",
                        (guild_id, int(updated.auto_thread), updated.youtube_quality,
                         int(updated.reply_messages), updated.locale, int(time.time())),
                    )
            except Exception as exc:
                LOG.warning("Sunucu ayarları yazılamadı: %s", exc)
                raise
        return updated


SETTINGS = GuildSettingsStore()


def describe(settings: GuildSettings) -> str:
    thread = "açık" if settings.auto_thread else "kapalı"
    reply = "açık" if settings.reply_messages else "kapalı"
    return (f"🧵 Otomatik thread: **{thread}**\n"
            f"🎬 YouTube varsayılanı: **{settings.youtube_quality}**\n"
            f"↩️ Mesaja yanıt (reply): **{reply}**")
