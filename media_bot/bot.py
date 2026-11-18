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
from .backup import backup_databases, database_paths
from .bluesky import download_bluesky_media, fetch_bluesky_post  # noqa: F401
from .captions import _clean, display_chunks, fetch_caption  # noqa: F401
from .circuit_breaker import BREAKERS
from .cookiefile import cookie_report, cookie_warning
from .cooldowns import Cooldowns
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
from .spotify import fetch_track, find_audio_source  # noqa: F401
from .threads_links import highlight_post_link, resolve_threads_link  # noqa: F401
from .video_fit import fit_video  # noqa: F401
from .x_card import create_card, fetch_post  # noqa: F401
from .youtube import download_youtube  # noqa: F401
from .youtube_view import YoutubeChoiceView, deliver_youtube, status_view  # noqa: F401

from .bot_runtime import _bounded_int
from .bot_slash_commands import register_commands
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


HELP_TEXT = """**Discord Video Bot**
Bağlantı gönder: Instagram, Threads, TikTok, X, Facebook, Reddit, YouTube, Spotify, Bluesky, Douyin, Weibo, Streamable.

**Komutlar**
`/stats [24s|7g|30g] [platform]` · işlem paneli (sahip)
`/info [kullanıcı]` · kişisel özet
`/cache` · önbellek durumu
`/hatalar [aralık]` · son hatalar (sahip)
`/bakim [ac|kapat]` · bakım modu (sahip)
`/ayar` · otomatik thread ve YouTube kalitesi (yönetici)
`/metrics` · sağlayıcı metrikleri

Eski `!` komutları da çalışır (`!ayar`, `!ayar thread ac`)."""


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
    def __init__(self, *, channel_ids: set[int], upload_bytes: int, max_links: int, concurrency: int, cookie_file: Path | None, cobalt_url: str | None, cobalt_key: str | None, gallery_rs_path: Path | None, database_path: Path | None = None, command_guild_id: int | None = None):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
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
        self._health_task: asyncio.Task | None = None
        self._health_skips = 0
        self._backup_task: asyncio.Task | None = None
        self._health_server = None
        self._cookie_checked = False
        try:
            cooldown = float(os.getenv("COMMAND_COOLDOWN_SECONDS", "2") or 2)
        except ValueError:
            cooldown = 2.0
        self.command_cooldowns = Cooldowns(max(0.0, min(cooldown, 60.0)))
        self.command_guild_id = command_guild_id
        self._register_commands()

    async def setup_hook(self) -> None:
        try:
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
        for task in (self._health_task, self._backup_task):
            if task and not task.done():
                task.cancel()
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
            )
        if self._backup_task is None or self._backup_task.done():
            if env_enabled("BACKUP_ENABLED") is not False:
                self._backup_task = asyncio.create_task(self._backup_loop())
        if not self._cookie_checked:
            self._cookie_checked = True
            await self._warn_cookie_expiry()
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
        return {
            "status": "ok" if ready else "starting",
            "uptime_seconds": int(time.time() - self.started_at),
            "user": str(self.user) if self.user else None,
            "guilds": len(self.guilds),
            "latency_ms": int(self.latency * 1000) if self.latency and self.latency == self.latency else 0,
            "concurrency": self.concurrency,
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
        }

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
        root = self.database_path.parent / "backups"
        # Give startup work (migrations, log panel) a moment before touching disks.
        await asyncio.sleep(60)
        while not self.is_closed():
            try:
                await asyncio.to_thread(
                    backup_databases, database_paths(self.database_path), root, keep,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception("Veritabanı yedeği alınamadı")
            await asyncio.sleep(interval_hours * 3600)

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
