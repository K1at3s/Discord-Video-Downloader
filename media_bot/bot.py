"""Discord gateway entry point."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

import discord

from .adult import AdultQuota, reddit_adult_status  # noqa: F401
from .backup import backup_databases, database_paths, latest_backup
from .bluesky import download_bluesky_media, fetch_bluesky_post  # noqa: F401
from .captions import _clean, display_chunks, fetch_caption  # noqa: F401
from .card_cache import CARD_CACHE
from .circuit_breaker import BREAKERS
from .cookiefile import cookie_report, cookie_warning
from .cooldowns import Cooldowns
from .direct_providers import x_media_urls  # noqa: F401
from .disk import disk_status, free_mb, min_free_mb
from .download import DownloadError, download_media  # noqa: F401
from . import i18n
from .health import start_health_server
from .highlight_state import HIGHLIGHTS, format_wait
from .http import close_shared_clients
from .indownio import is_rate_limited_message, resolve_indownio
from .job_slots import JobSlots
from .links import PostLink, find_links, parse_link  # noqa: F401
from .maintenance import MAINTENANCE, env_enabled
from .media_cache import MEDIA_CACHE
from .metadata_fallback import Preview, fetch_preview, preview_embeds  # noqa: F401
from .provider_metrics import METRICS
from .reddit_card import render_reddit_cards  # noqa: F401
from .reddit_post import RedditPost, fetch_reddit_post  # noqa: F401
from .request_log import REQUESTS  # noqa: F401
from .sizes import estimate_reddit_media_bytes, estimate_urls_bytes  # noqa: F401
from .spotify import fetch_track, find_audio_source  # noqa: F401
from .threads_links import highlight_post_link, resolve_threads_link  # noqa: F401
from .user_quota import GuildQuota, UserQuota
from .video_fit import fit_video  # noqa: F401
from .x_card import create_card, fetch_post  # noqa: F401
from .youtube import download_youtube  # noqa: F401
from .youtube_view import YoutubeChoiceView, deliver_youtube, status_view  # noqa: F401

from .bot_runtime import _bounded_int
from .bot_slash_commands import BotTranslator, register_commands
from .bot_commands_mixin import AdminCommandsMixin
from .bot_link_handlers_mixin import LinkHandlersMixin
from .bot_orchestration_mixin import OrchestrationMixin
from .discord_support import (
    COLORS as COLORS,  # noqa: F401
    InteractionMessage as InteractionMessage,  # noqa: F401
    ThreadMessage as ThreadMessage,  # noqa: F401
    UPLOAD_CEILING as UPLOAD_CEILING,  # noqa: F401
    VIDEO_SUFFIXES as VIDEO_SUFFIXES,  # noqa: F401
    _instagram_story_meta as _instagram_story_meta,  # noqa: F401
    media_gallery_view as media_gallery_view,  # noqa: F401
    reddit_embed_preview as reddit_embed_preview,  # noqa: F401
    source_button as source_button,  # noqa: F401
    upload_limit as upload_limit,  # noqa: F401
)

LOG = logging.getLogger(__name__)
STATS_OWNER_ID = int(os.getenv("STATS_OWNER_ID", "1065352859195015260"))
# Discord'da "Oynuyor: made by hakixld" olarak görünür; kimlik doğrulama
# yüküne eklenir ve yeniden bağlanmalarda korunur.
PRESENCE_TEXT = "made by hakixld"


def _has_media(post: dict) -> bool:
    """True when the post or its quoted post carries downloadable media."""
    quoted = post.get("quote") if isinstance(post.get("quote"), dict) else {}
    media = post.get("media") or {}
    quote_media = quoted.get("media") or {}
    return any(media.get(key) or quote_media.get(key)
               for key in ("all", "photos", "videos", "external"))


def _is_admin(message: discord.Message) -> bool:
    permissions = getattr(getattr(message, "author", None), "guild_permissions", None)
    return bool(getattr(permissions, "administrator", False) or getattr(permissions, "manage_guild", False))


def _extract_download_caption(folder: Path) -> str | None:
    for info_file in folder.rglob("*.info.json"):
        try:
            data = json.loads(info_file.read_text(encoding="utf-8", errors="replace"))
            desc = (data.get("description") or data.get("title") or "").strip()
            cleaned = _clean(desc)
            if cleaned:
                return cleaned
        except Exception:
            pass
    return None


class MediaBot(AdminCommandsMixin, LinkHandlersMixin, OrchestrationMixin, discord.Client):
    def __init__(self, *, channel_ids: set[int], upload_bytes: int, max_links: int, concurrency: int, cookie_file: Path | None, cobalt_url: str | None, cobalt_key: str | None, gallery_rs_path: Path | None, database_path: Path | None = None, command_guild_id: int | None = None, user_hourly_limit: int = 30, user_daily_limit: int = 200, guild_hourly_limit: int = 0, guild_daily_limit: int = 0):
        intents = discord.Intents.default()
        intents.message_content = True
        # Presence is part of the IDENTIFY payload, so it applies on every
        # reconnect without an extra gateway round-trip.
        super().__init__(
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
            activity=discord.Activity(type=discord.ActivityType.playing, name=PRESENCE_TEXT),
            status=discord.Status.idle,
        )
        self._background_tasks: set[asyncio.Task] = set()
        self.channel_ids = channel_ids
        self.upload_bytes = upload_bytes
        self.max_links = max_links
        self.jobs = asyncio.Semaphore(concurrency)
        self.job_slots = JobSlots(self.jobs)
        self.cookie_file = cookie_file
        self.cobalt_url = cobalt_url
        self.cobalt_key = cobalt_key
        self.gallery_rs_path = gallery_rs_path
        self.owner_id = STATS_OWNER_ID
        self.database_path = database_path or Path(__file__).resolve().parent.parent / "data" / "bot.sqlite3"
        self.concurrency = concurrency
        self.started_at = time.time()
        self.adult_quota = AdultQuota(self.database_path)
        self.user_quota = UserQuota(self.database_path, user_hourly_limit, user_daily_limit)
        self.guild_quota = GuildQuota(self.database_path, guild_hourly_limit, guild_daily_limit)
        self._health_task: asyncio.Task | None = None
        self._health_skips = 0
        self._backup_task: asyncio.Task | None = None
        self._health_server = None
        self._cookie_checked = False
        self._disk_warned_at = 0.0
        self._last_vacuum_at = 0.0
        try:
            cooldown = float(os.getenv("COMMAND_COOLDOWN_SECONDS", "2") or 2)
        except ValueError:
            cooldown = 2.0
        self.command_cooldowns = Cooldowns(max(0.0, min(cooldown, 60.0)))
        self.command_guild_id = command_guild_id
        self._register_commands()

    async def setup_hook(self) -> None:
        try:
            await self.tree.set_translator(BotTranslator())
            if self.command_guild_id:
                guild = discord.Object(id=self.command_guild_id)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                LOG.info("Slash komutları %s sunucusuna eşitlendi", self.command_guild_id)
            else:
                await self.tree.sync()
                LOG.info("Slash komutları global olarak eşitlendi")
        except discord.HTTPException as exc:
            LOG.warning("Slash komutları eşitlenemedi: %s", exc)

    def _command_allowed(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id == self.owner_id:
            return True
        return bool(interaction.user and self.command_cooldowns.allow(interaction.user.id))

    async def _owner_command(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            locale = i18n.locale_code(interaction)
            await interaction.response.send_message(i18n.t("owner_only", locale), ephemeral=True)
            return False
        return True

    def _register_commands(self) -> None:
        register_commands(self)
    async def close(self) -> None:
        tasks = [
            task for task in (self._health_task, self._backup_task, *tuple(self._background_tasks))
            if task is not None and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._health_task = None
        self._backup_task = None
        self._background_tasks.clear()
        if self._health_server is not None:
            try:
                self._health_server.shutdown()
                self._health_server.server_close()
            except OSError:
                pass
            self._health_server = None
        try:
            await close_shared_clients()
        finally:
            await super().close()

    async def on_ready(self) -> None:
        LOG.info("Hazır: %s (%s)", self.user, self.user.id if self.user else "?")
        if self._health_server is None:
            self._health_server = start_health_server(
                os.getenv("HEALTH_HOST", "127.0.0.1").strip() or "127.0.0.1",
                _bounded_int("HEALTH_PORT", 0, 0, 65535),
                self.health_payload,
                self.metrics_payload,
            )
        if self._backup_task is None or self._backup_task.done():
            if env_enabled("BACKUP_ENABLED") is not False:
                self._backup_task = asyncio.create_task(self._backup_loop())
        if not self._cookie_checked:
            self._cookie_checked = True
            await self._warn_cookie_expiry()
        await self._warn_disk_space()
        if (HIGHLIGHTS.enabled and HIGHLIGHTS.health_check_enabled
                and (self._health_task is None or self._health_task.done())):
            self._health_task = asyncio.create_task(self._highlight_health_loop())

    def health_payload(self) -> dict:
        """Machine-readable status for the local ``/healthz`` endpoint."""
        cookies = cookie_report(self.cookie_file)
        breakers = BREAKERS.snapshot()
        try:
            ready = self.is_ready()
        except (AttributeError, RuntimeError):
            ready = False
        cache = MEDIA_CACHE.stats()
        cards = CARD_CACHE.stats()
        slots = self.job_slots.snapshot()
        backup = latest_backup(self.database_path.parent / "backups")
        backup_info: dict[str, str | float | None] = {"last": None, "age_hours": None}
        if backup is not None:
            backup_info["last"] = backup.name
            try:
                backup_info["age_hours"] = round((time.time() - backup.stat().st_mtime) / 3600, 1)
            except OSError:
                pass
        return {
            "status": "ok" if ready else "starting",
            "uptime_seconds": int(time.time() - self.started_at),
            "user": str(self.user) if self.user else None,
            "guilds": len(self.guilds),
            "latency_ms": int(self.latency * 1000) if self.latency and self.latency == self.latency else 0,
            "concurrency": self.concurrency,
            "queue": {
                "active": slots["active"],
                "waiting": slots["waiting"],
                "capacity": self.concurrency,
            },
            "disk": disk_status(self.database_path.parent),
            "backup": backup_info,
            "providers": breakers,
            "cobalt": bool(self.cobalt_url),
            "maintenance": MAINTENANCE.active,
            "cookies": {key: cookies[key] for key in ("total", "expired", "expiring", "netscape")},
            "cache": {
                "enabled": cache.get("enabled"),
                "entries": cache.get("entries", 0),
                "bytes": cache.get("bytes", 0),
                "hits": cache.get("hits", 0),
                "misses": cache.get("misses", 0),
                "stores": cache.get("stores", 0),
            },
            "card_cache": {
                "enabled": cards.get("enabled"),
                "entries": cards.get("entries", 0),
                "bytes": cards.get("bytes", 0),
                "hits": cards.get("hits", 0),
                "misses": cards.get("misses", 0),
                "stores": cards.get("stores", 0),
            },
        }

    def metrics_payload(self) -> str:
        """Prometheus text exposition for the local ``/metrics`` endpoint."""
        payload = self.health_payload()
        lines: list[str] = []

        def gauge(name: str, value: float | int, help_text: str) -> None:
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {float(value)}")

        gauge("discord_video_bot_up", 1 if payload.get("status") == "ok" else 0,
              "Whether the bot is ready.")
        gauge("discord_video_bot_uptime_seconds", payload.get("uptime_seconds", 0),
              "Process uptime in seconds.")
        gauge("discord_video_bot_guilds", payload.get("guilds", 0),
              "Number of guilds the bot is in.")
        gauge("discord_video_bot_latency_ms", payload.get("latency_ms", 0),
              "Discord gateway latency in milliseconds.")
        queue = payload.get("queue") or {}
        gauge("discord_video_bot_queue_active", queue.get("active", 0),
              "Active download jobs.")
        gauge("discord_video_bot_queue_waiting", queue.get("waiting", 0),
              "Waiting download jobs.")
        gauge("discord_video_bot_queue_capacity", queue.get("capacity", 0),
              "Maximum concurrent download jobs.")
        disk = payload.get("disk") or {}
        if disk.get("ok") and disk.get("free_bytes") is not None:
            gauge("discord_video_bot_disk_free_bytes", disk["free_bytes"],
                  "Free disk bytes on the data volume.")
            if disk.get("total_bytes") is not None:
                gauge("discord_video_bot_disk_total_bytes", disk["total_bytes"],
                      "Total disk bytes on the data volume.")
        for cache_name, key in (("media_cache", "cache"), ("card_cache", "card_cache")):
            cache = payload.get(key) or {}
            for field in ("entries", "bytes", "hits", "misses", "stores"):
                gauge(f"discord_video_bot_{cache_name}_{field}", cache.get(field, 0),
                      f"{cache_name} {field}.")
        quota = getattr(self, "user_quota", None)
        if quota is not None and quota.enabled:
            try:
                summary = quota.summary()
            except Exception:
                summary = {}
            gauge("discord_video_bot_quota_hourly_used", summary.get("hourly", 0),
                  "Hourly quota usage across users.")
            gauge("discord_video_bot_quota_daily_used", summary.get("daily", 0),
                  "Daily quota usage across users.")
            gauge("discord_video_bot_quota_hourly_limit", summary.get("hourly_limit", 0),
                  "Configured hourly quota limit.")
            gauge("discord_video_bot_quota_daily_limit", summary.get("daily_limit", 0),
                  "Configured daily quota limit.")
        guild_quota = getattr(self, "guild_quota", None)
        if guild_quota is not None and guild_quota.enabled:
            try:
                summary = guild_quota.summary()
            except Exception:
                summary = {}
            gauge("discord_video_bot_guild_quota_hourly_used", summary.get("hourly", 0),
                  "Hourly quota usage across guilds.")
            gauge("discord_video_bot_guild_quota_daily_used", summary.get("daily", 0),
                  "Daily quota usage across guilds.")
            gauge("discord_video_bot_guild_quota_hourly_limit", summary.get("hourly_limit", 0),
                  "Configured guild hourly quota limit.")
            gauge("discord_video_bot_guild_quota_daily_limit", summary.get("daily_limit", 0),
                  "Configured guild daily quota limit.")
        lines.append("")
        return "\n".join(lines)

    async def _warn_cookie_expiry(self) -> None:
        warning = cookie_warning(self.cookie_file)
        if not warning:
            return
        LOG.warning(warning)
        try:
            owner = await self.fetch_user(self.owner_id)
            await owner.send(f"⚠️ {warning}")
        except (discord.HTTPException, AttributeError):
            LOG.warning("Çerez uyarısı sahibine gönderilemedi")

    async def _backup_loop(self) -> None:
        interval_hours = _bounded_int("BACKUP_INTERVAL_HOURS", 24, 1, 168)
        keep = _bounded_int("BACKUP_KEEP", 7, 1, 90)
        max_age_days = _bounded_int("BACKUP_MAX_AGE_DAYS", 30, 0, 3650)
        vacuum_hours = _bounded_int("DB_VACUUM_HOURS", 168, 0, 24 * 30)
        root = self.database_path.parent / "backups"
        # Give startup work (migrations, log panel) a moment before touching disks.
        await asyncio.sleep(60)
        while not self.is_closed():
            try:
                paths = database_paths(self.database_path)
                await asyncio.to_thread(backup_databases, paths, root, keep, max_age_days=max_age_days)
                from .backup import cleanup_stale_temp_dirs
                await asyncio.to_thread(cleanup_stale_temp_dirs)
                now = time.time()
                vacuum_due = bool(vacuum_hours) and (now - self._last_vacuum_at >= vacuum_hours * 3600)
                from .sqlite_util import maintain_databases
                results = await asyncio.to_thread(maintain_databases, paths, vacuum_enabled=vacuum_due)
                if vacuum_due and any(info.get("vacuumed") for info in results.values()):
                    self._last_vacuum_at = now
                for db_path, info in results.items():
                    if not info.get("integrity_ok", True):
                        LOG.warning("SQLite integrity_check failed: %s -> %s", db_path, info.get("integrity"))
                        MAINTENANCE.set(True)
                        try:
                            owner = await self.fetch_user(self.owner_id)
                            await owner.send(f"⚠️ SQLite integrity_check başarısız: `{db_path}` -> `{info.get('integrity')}`. Bakım modu açıldı.")
                        except Exception:
                            LOG.warning("Integrity failure notice could not be sent")
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception("Veritabanı yedeği/bakımı başarısız")
            await self._warn_disk_space()
            await asyncio.sleep(interval_hours * 3600)

    async def _warn_disk_space(self) -> None:
        now = time.time()
        if now - self._disk_warned_at < 12 * 3600:
            return
        minimum = min_free_mb()
        available = free_mb(self.database_path.parent)
        if available is None or minimum <= 0 or available >= minimum:
            return
        self._disk_warned_at = now
        warning = (
            f"Boş disk alanı az: {available} MB kaldı (eşik {minimum} MB). "
            "Yeni indirmeler reddedilecek."
        )
        LOG.warning(warning)
        try:
            owner = await self.fetch_user(self.owner_id)
            await owner.send(f"⚠️ {warning}")
        except (discord.HTTPException, AttributeError):
            LOG.warning("Disk uyarısı sahibine gönderilemedi")

    async def _highlight_health_loop(self) -> None:
        # Periodic health check: natgeo highlight 18023864549864045 (expects ~8 videos).
        # Resolve-only (no media download). Consumes indown.io quota once per run.
        interval = HIGHLIGHTS.health_check_interval
        reserve = HIGHLIGHTS.health_check_reserve
        await asyncio.sleep(interval)
        url = "https://www.instagram.com/stories/highlights/18023864549864045/"
        link = PostLink("instagram", url)
        while not self.is_closed() and HIGHLIGHTS.enabled and HIGHLIGHTS.health_check_enabled:
            try:
                # Reserve tokens for user requests; this probe also consumes quota.
                if HIGHLIGHTS.bucket.remaining() < reserve:
                    wait = HIGHLIGHTS.bucket.wait_seconds()
                    self._health_skips += 1
                    METRICS.record("instagram", "indownio-health", "skipped", 0)
                    LOG.info("Highlight health check skipped: low quota reserve (~%s kaldı)", format_wait(wait))
                    if self._health_skips >= 3:
                        LOG.warning("Highlight health check art arda %d kez atlandı; servis durumu doğrulanamıyor", self._health_skips)
                    await asyncio.sleep(interval)
                    continue
                allowed, wait = HIGHLIGHTS.bucket.try_consume()
                if not allowed:
                    self._health_skips += 1
                    METRICS.record("instagram", "indownio-health", "skipped", 0)
                    LOG.info("Highlight health check skipped: quota reserve exhausted (~%s kaldı)", format_wait(wait))
                    if self._health_skips >= 3:
                        LOG.warning("Highlight health check art arda %d kez atlandı; servis durumu doğrulanamıyor", self._health_skips)
                else:
                    self._health_skips = 0
                    count, detail = await resolve_indownio(link)
                    if detail and is_rate_limited_message(detail):
                        HIGHLIGHTS.bucket.mark_exhausted()
                        METRICS.record("instagram", "indownio-health", "quota", 0)
                        LOG.info("Highlight health check: quota/rate-limited by service")
                    elif detail:
                        METRICS.record("instagram", "indownio-health", "error", 0)
                        LOG.warning("Highlight health check failed: %s", detail)
                    else:
                        ok = count >= 8
                        METRICS.record("instagram", "indownio-health", "success" if ok else "error", 0)
                        LOG.info("Highlight health check: resolved=%d ok=%s", count, ok)
            except Exception as exc:
                self._health_skips = 0
                METRICS.record("instagram", "indownio-health", "error", 0)
                LOG.warning("Highlight health check failed: %s", exc)
            await asyncio.sleep(interval)


def main() -> None:
    from .bot_entrypoint import main as _main
    _main()


if __name__ == "__main__":
    main()
