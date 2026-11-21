"""Process entrypoint and configuration for the Discord bot."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import discord
from dotenv import load_dotenv

from .bot_runtime import _bounded_int, _configure_logging, _positive_int, _prepare_temp_dir
from .card_cache import CARD_CACHE
from .guild_settings import SETTINGS
from .highlight_state import HIGHLIGHTS
from .maintenance import MAINTENANCE, env_enabled
from .media_cache import MEDIA_CACHE
from .provider_metrics import METRICS
from .request_log import REQUESTS


def main() -> None:
    from .bot import MediaBot

    load_dotenv()
    _configure_logging()
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise SystemExit("DISCORD_TOKEN .env dosyasında tanımlanmalı.")
    channels_raw = os.getenv("CHANNEL_IDS", "").strip()
    try:
        channels = {int(part.strip()) for part in channels_raw.split(",") if part.strip()}
        cookie_raw = os.getenv("COOKIE_FILE", "").strip()
        cookie_file = Path(cookie_raw).resolve() if cookie_raw else None
        if cookie_file and not cookie_file.is_file():
            raise ValueError("COOKIE_FILE mevcut bir dosyayı göstermeli")
        cobalt_url = os.getenv("COBALT_API_URL", "").strip() or None
        if cobalt_url:
            parsed = urlsplit(cobalt_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
                raise ValueError("COBALT_API_URL örneği: http://127.0.0.1:9000/")
        rs_raw = os.getenv("GALLERY_DL_RS_PATH", "").strip()
        gallery_rs_path = Path(rs_raw).resolve() if rs_raw else None
        if gallery_rs_path and not gallery_rs_path.is_file():
            raise ValueError("GALLERY_DL_RS_PATH mevcut bir programı göstermeli")
        database_path = Path(os.getenv("DATABASE_PATH", "data/bot.sqlite3")).resolve()
        _prepare_temp_dir(database_path.parent / "tmp")
        METRICS.configure(database_path.with_name("provider_metrics.sqlite3"))
        SETTINGS.configure(database_path.with_name("guild_settings.sqlite3"))
        REQUESTS.configure(
            database_path.with_name("request_log.sqlite3"),
            retention_days=_positive_int("REQUEST_LOG_RETENTION_DAYS", 30, 3650),
            store_url=os.getenv("REQUEST_LOG_STORE_URL", "1").strip().lower()
            not in {"0", "false", "no", "off", "hayir", "hayır"},
        )
        MAINTENANCE.configure(
            database_path.parent / "maintenance.json",
            enabled=env_enabled("MAINTENANCE_MODE"),
            message=os.getenv("MAINTENANCE_MESSAGE", ""),
        )
        MEDIA_CACHE.configure(
            database_path.parent / "media_cache",
            ttl=_positive_int("MEDIA_CACHE_TTL_MINUTES", 360, 10080) * 60,
            max_bytes=_positive_int("MEDIA_CACHE_MAX_MB", 1024, 10240) * 1024 * 1024,
        )
        CARD_CACHE.configure(
            database_path.parent / "card_cache",
            ttl=_positive_int("CARD_CACHE_TTL_MINUTES", 30, 1440) * 60,
            max_bytes=_positive_int("CARD_CACHE_MAX_MB", 256, 4096) * 1024 * 1024,
        )
        HIGHLIGHTS.configure(
            database_path,
            cache_ttl=_positive_int("HIGHLIGHT_CACHE_TTL_HOURS", 12, 168) * 3600,
            cache_max_bytes=_positive_int("HIGHLIGHT_CACHE_MAX_MB", 512, 4096) * 1024 * 1024,
            daily_limit=_positive_int("HIGHLIGHT_DAILY_LIMIT", 15, 200),
            hourly_limit=_positive_int("HIGHLIGHT_HOURLY_LIMIT", 4, 50),
            health_check_enabled=os.getenv("HIGHLIGHT_HEALTHCHECK_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"},
            health_check_interval=_positive_int("HIGHLIGHT_HEALTHCHECK_HOURS", 24, 168) * 3600,
            health_check_reserve=_positive_int("HIGHLIGHT_HEALTHCHECK_RESERVE", 3, 20),
        )
        bot = MediaBot(
            channel_ids=channels,
            upload_bytes=_bounded_int("MAX_UPLOAD_MB", 0, 0, 500) * 1024 * 1024,
            max_links=_positive_int("MAX_LINKS_PER_MESSAGE", 3, 10),
            concurrency=_positive_int("MAX_CONCURRENT_JOBS", 2, 10),
            user_hourly_limit=_bounded_int("USER_HOURLY_LIMIT", 30, 0, 10000),
            user_daily_limit=_bounded_int("USER_DAILY_LIMIT", 200, 0, 100000),
            guild_hourly_limit=_bounded_int("GUILD_HOURLY_LIMIT", 0, 0, 100000),
            guild_daily_limit=_bounded_int("GUILD_DAILY_LIMIT", 0, 0, 1000000),
            command_guild_id=_bounded_int("COMMAND_GUILD_ID", 0, 0, 2**63 - 1) or None,
            cookie_file=cookie_file,
            cobalt_url=cobalt_url,
            cobalt_key=os.getenv("COBALT_API_KEY", "").strip() or None,
            gallery_rs_path=gallery_rs_path,
            database_path=database_path,
        )
    except ValueError as exc:
        raise SystemExit(f"Yapılandırma hatası: {exc}") from exc
    if sys.platform != "win32":
        try:
            import uvloop
        except ImportError:
            pass
        else:
            if hasattr(uvloop, "install"):
                uvloop.install()
    try:
        bot.run(token, log_handler=None)
    except discord.PrivilegedIntentsRequired:
        raise SystemExit(
            "Discord, Message Content Intent erişimini reddetti. "
            "Discord Developer Portal > uygulamanız > Bot > Privileged Gateway Intents "
            "bölümünde Message Content Intent'i açıp kaydedin; sonra botu yeniden başlatın."
        ) from None
