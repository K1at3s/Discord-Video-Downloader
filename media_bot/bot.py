"""Discord gateway entry point."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import discord
import httpx

from .adult import AdultQuota, channel_is_nsfw, istanbul_day, reddit_adult_status
from .backup import backup_databases, database_paths
from .bluesky import download_bluesky_media, fetch_bluesky_post
from .captions import display_chunks, fetch_caption
from .circuit_breaker import BREAKERS
from .cookiefile import cookie_report, cookie_warning
from .cooldowns import Cooldowns
from .download import DownloadError, download_media
from . import i18n
from .guild_settings import QUALITY_CHOICES, SETTINGS, describe
from .health import start_health_server
from .highlight_state import HIGHLIGHTS, format_wait
from .http import close_shared_clients
from .indownio import is_rate_limited_message, resolve_indownio
from .job_slots import JobSlots
from .limits import attachment_batches
from .links import PostLink, find_links, parse_link
from .maintenance import MAINTENANCE, env_enabled
from .media_cache import MEDIA_CACHE
from .metadata_fallback import Preview, fetch_preview, preview_embeds
from .provider_metrics import METRICS
from .reddit_card import render_reddit_cards
from .reddit_post import RedditPost, fetch_reddit_post
from .request_log import REQUESTS, _human_bytes, parse_range, resolve_platform
from .retry_view import RetryView
from .spotify import fetch_track, find_audio_source
from .threads_links import highlight_post_link, resolve_threads_link
from .video_fit import fit_video
from .x_card import create_card, fetch_post
from .youtube import download_youtube
from .youtube_view import YoutubeChoiceView, deliver_youtube, status_view

from .bot_runtime import _bounded_int, _upload_too_large
from .bot_slash_commands import register_commands
from .discord_support import (
    COLORS as COLORS,  # noqa: F401
    InteractionMessage as InteractionMessage,  # noqa: F401
    ThreadMessage as ThreadMessage,  # noqa: F401
    UPLOAD_CEILING as UPLOAD_CEILING,  # noqa: F401
    VIDEO_SUFFIXES,
    _instagram_story_meta as _instagram_story_meta,  # noqa: F401
    media_gallery_view,
    reddit_embed_preview,
    source_button,
    upload_limit,
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

class MediaBot(discord.Client):
    def __init__(self, *, channel_ids: set[int], upload_bytes: int, max_links: int, concurrency: int, cookie_file: Path | None, cobalt_url: str | None, cobalt_key: str | None, gallery_rs_path: Path | None, database_path: Path | None = None, command_guild_id: int | None = None):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
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

    async def _reply_metrics(self, message: discord.Message) -> None:
        parts = [
            METRICS.format_text(24),
            "",
            METRICS.format_text(168, limit=8),
        ]
        breakers = BREAKERS.snapshot()
        if breakers.get("open"):
            parts.append("")
            items = ", ".join(f"{name} (~{sec}s)" for name, sec in list(breakers["open"].items())[:8])
            parts.append(f"Devre kesici açık: {items}")
        if HIGHLIGHTS.enabled and HIGHLIGHTS.bucket:
            remaining = HIGHLIGHTS.bucket.remaining()
            wait = HIGHLIGHTS.bucket.wait_seconds()
            parts.append("")
            parts.append(
                f"Highlight bucket (indown.io, lokal tahmin): kalan={remaining} "
                f"bekleme={format_wait(wait) if wait > 0 else '0 dk'} "
                f"health={'açık' if HIGHLIGHTS.health_check_enabled else 'kapalı'}"
            )
        text = "\n".join(parts).strip()
        await self._reply(message, text[:1900])

    async def _reply_request_stats(self, message: discord.Message, argument: str = "") -> None:
        hours = 24
        platform: str | None = None
        for token in argument.split():
            parsed = parse_range(token)
            if parsed is not None:
                hours = parsed
                continue
            key = resolve_platform(token)
            if key:
                platform = key
                continue
            await self._reply(
                message,
                f"Bilinmeyen argüman: `{token}`\n"
                "Kullanım: `!stats` · `!stats 7g` · `!stats 30g` · `!stats 7g instagram`",
            )
            return
        if platform:
            text = REQUESTS.format_platform(platform, hours)
        else:
            role = "Yönetici" if _is_admin(message) else "Kullanıcı"
            text = REQUESTS.format_panel(hours, role=role, maintenance=MAINTENANCE.active)
        await self._reply(message, text[:1900])

    async def _reply_maintenance(self, message: discord.Message, argument: str = "") -> None:
        action = argument.strip().lower()
        if not action:
            state = "Açık" if MAINTENANCE.active else "Kapalı"
            await self._reply(
                message,
                f"🧰 Bakım modu: **{state}**\n"
                "Açmak için `!bakim ac`, kapatmak için `!bakim kapat`.",
            )
            return
        if action in {"ac", "aç", "on", "1", "açık", "acik"}:
            MAINTENANCE.set(True)
            await self._reply(message, f"🧰 Bakım modu açıldı. {MAINTENANCE.message}")
            return
        if action in {"kapat", "off", "0", "kapalı", "kapali"}:
            MAINTENANCE.set(False)
            await self._reply(message, "✅ Bakım modu kapatıldı; yeni indirmeler yeniden açık.")
            return
        await self._reply(message, "Kullanım: `!bakim` · `!bakim ac` · `!bakim kapat`")

    def _can_manage_guild(self, author) -> bool:
        permissions = getattr(author, "guild_permissions", None)
        return bool((permissions and permissions.manage_guild) or getattr(author, "id", 0) == self.owner_id)

    async def _reply_settings(self, message: discord.Message, argument: str = "") -> None:
        guild = getattr(message, "guild", None)
        if guild is None:
            await self._reply(message, "Bu ayar yalnızca sunucularda kullanılır.")
            return
        if not self._can_manage_guild(getattr(message, "author", None)):
            await self._reply(message, "Bu komut için **Sunucuyu yönet** izni gerekir.")
            return
        usage = ("Kullanım: `!ayar` · `!ayar thread ac|kapat` · `!ayar yanit ac|kapat` · "
                 "`!ayar youtube sor|mp3|360|720|1080` · `!ayar sifirla`")
        tokens = argument.split()
        changes: dict = {}
        index = 0
        while index < len(tokens):
            token = tokens[index].lower()
            if token in {"thread", "otomatik_thread", "otothread"}:
                if index + 1 >= len(tokens):
                    await self._reply(message, usage)
                    return
                value = tokens[index + 1].lower()
                if value in {"ac", "aç", "on", "1", "evet"}:
                    changes["auto_thread"] = True
                elif value in {"kapat", "off", "0", "hayir", "hayır"}:
                    changes["auto_thread"] = False
                else:
                    await self._reply(message, usage)
                    return
                index += 2
            elif token in {"youtube", "kalite"}:
                if index + 1 >= len(tokens):
                    await self._reply(message, usage)
                    return
                changes["youtube_quality"] = tokens[index + 1]
                index += 2
            elif token in {"yanit", "yanıt", "yanitla", "reply", "cevapla"}:
                if index + 1 >= len(tokens):
                    await self._reply(message, usage)
                    return
                value = tokens[index + 1].lower()
                if value in {"ac", "aç", "on", "1", "evet"}:
                    changes["reply_messages"] = True
                elif value in {"kapat", "off", "0", "hayir", "hayır"}:
                    changes["reply_messages"] = False
                else:
                    await self._reply(message, usage)
                    return
                index += 2
            elif token in {"sifirla", "sıfırla", "reset"}:
                changes.update(auto_thread=False, youtube_quality="sor", reply_messages=True)
                index += 1
            else:
                await self._reply(message, usage)
                return
        try:
            settings = SETTINGS.update(guild.id, **changes)
        except ValueError as exc:
            await self._reply(message, f"{exc}\nKalite: `sor`, `mp3`, `360`, `720`, `1080`")
            return
        await self._reply(message, describe(settings))

    def _youtube_quality(self, message) -> str:
        guild = getattr(message, "guild", None)
        if guild is None:
            return "sor"
        quality = SETTINGS.get(guild.id).youtube_quality
        return quality if quality in QUALITY_CHOICES else "sor"

    async def _auto_thread_message(self, message: discord.Message, links: list[PostLink]):
        """Open a thread for a media post when the guild prefers it."""
        guild = getattr(message, "guild", None)
        if guild is None or isinstance(getattr(message, "channel", None), discord.Thread):
            return message
        if not SETTINGS.get(guild.id).auto_thread:
            return message
        name = " ".join(re.sub(r"https?://\S+", " ", message.content or "").split())[:80]
        try:
            thread = await message.create_thread(name=name or "🎬 video",
                                                 auto_archive_duration=1440)
        except (discord.HTTPException, AttributeError, TypeError) as exc:
            LOG.info("Otomatik thread açılamadı: %s", exc)
            return message
        LOG.info("Otomatik thread açıldı: %s (%s)", thread.id, name or "video")
        return ThreadMessage(message, thread)

    async def _reply_log_clear(self, message: discord.Message, argument: str) -> None:
        confirmed = "onay" in {token.lower() for token in argument.split()}
        if not confirmed:
            total = REQUESTS.count()
            await self._reply(
                message,
                f"⚠️ Tüm işlem kayıtları silinecek ({total} satır).\n"
                "Onaylamak için `!logtemizle onay` yazın.",
            )
            return
        removed = REQUESTS.clear()
        await self._reply(message, f"🧹 {removed} işlem kaydı silindi.")

    async def _reply_log_delete_user(self, message: discord.Message, argument: str) -> None:
        owner = message.author.id == STATS_OWNER_ID
        tokens = argument.split()
        confirmed = any(token.lower() in {"onay", "confirm"} for token in tokens)
        target = message.author
        target_name = getattr(target, "display_name", None) or getattr(target, "name", None) or str(target)
        if message.mentions:
            target = message.mentions[0]
            target_name = getattr(target, "display_name", None) or getattr(target, "name", None) or str(target)
        else:
            for token in tokens:
                if token.isdigit():
                    target = SimpleNamespace(id=int(token), display_name=token, name=token)
                    target_name = token
                    break
        is_other = getattr(target, "id", None) != message.author.id
        if is_other and not owner:
            await self._reply(message, "Başka bir kullanıcının kayıtlarını yalnızca bot sahibi silebilir.")
            return
        if not confirmed:
            count = REQUESTS.count(user_id=getattr(target, "id", 0))
            hint = "!logsil @kullanıcı onay" if is_other else "!logsil onay"
            await self._reply(
                message,
                f"⚠️ {target_name} için {count} kayıt silinecek.\nOnaylamak için `{hint}` yazın.",
            )
            return
        removed = REQUESTS.clear(user_id=getattr(target, "id", None))
        await self._reply(message, f"🧹 {target_name} için {removed} kayıt silindi.")

    async def _reply_errors(self, message: discord.Message, argument: str = "") -> None:
        hours = 24
        for token in argument.split():
            parsed = parse_range(token)
            if parsed is not None:
                hours = parsed
        await self._reply(message, REQUESTS.format_errors(hours)[:1900])

    async def _reply_cache(self, message: discord.Message) -> None:
        def ttl_text(seconds: float) -> str:
            if seconds >= 3600:
                return f"{seconds / 3600:g} saat"
            return f"{seconds / 60:g} dk"

        media = MEDIA_CACHE.stats()
        hits = media.get("hits", 0)
        misses = media.get("misses", 0)
        stores = media.get("stores", 0)
        lookups = hits + misses
        hit_rate = (f"{(hits / lookups) * 100:.0f}%" if lookups else "—")
        lines = [
            "📦 Önbellek durumu",
            f"Genel medya: {'açık' if media['enabled'] else 'kapalı'} · "
            f"{media['entries']} kayıt · {media['files']} dosya · {_human_bytes(media['bytes'])} "
            f"(sınır {_human_bytes(media['max_bytes'])}, TTL {ttl_text(media['ttl'])})",
            f"İsabet/kaçırma: {hits}/{misses} (oran {hit_rate}) · kayıt ekleme: {stores}",
        ]
        if media["stale"]:
            lines.append(f"♻️ Temizlenmeyi bekleyen eski kayıt: {media['stale']}")
        highlight = HIGHLIGHTS.cache.stats() if HIGHLIGHTS.cache else None
        if highlight is not None:
            lines.append(
                f"Highlight: {highlight['entries']} kayıt · {highlight['files']} dosya · "
                f"{_human_bytes(highlight['bytes'])} (sınır {_human_bytes(highlight['max_bytes'])}, "
                f"TTL {ttl_text(highlight['ttl'])})"
            )
            if highlight["stale"]:
                lines.append(f"♻️ Eski highlight kaydı: {highlight['stale']}")
        lines.append("")
        lines.append("Temizlemek için (sahip): `!cachetemizle`")
        await self._reply(message, "\n".join(lines)[:1900])

    async def _reply_cache_clear(self, message: discord.Message) -> None:
        media = MEDIA_CACHE.clear() if MEDIA_CACHE.enabled else 0
        highlight = HIGHLIGHTS.cache.clear() if HIGHLIGHTS.cache else 0
        await self._reply(
            message,
            f"🧹 Önbellek temizlendi · genel medya: {media} kayıt · highlight: {highlight} kayıt",
        )

    async def _reply_user_info(self, message: discord.Message, argument: str = "") -> None:
        hours = 24
        id_token = ""
        for token in argument.split():
            if token.startswith("<@") or token.startswith("<@!") or token.isdigit():
                id_token = token
                continue
            parsed = parse_range(token)
            if parsed is not None:
                hours = parsed
        target = message.author
        target_name = getattr(target, "display_name", None) or getattr(target, "name", None) or str(target)
        if message.mentions:
            target = message.mentions[0]
            target_name = getattr(target, "display_name", None) or getattr(target, "name", None) or str(target)
        elif id_token.isdigit():
            target_id = int(id_token)
            target = SimpleNamespace(id=target_id, display_name=str(target_id), name=str(target_id))
            target_name = str(target_id)
        if getattr(target, "id", None) != message.author.id and message.author.id != STATS_OWNER_ID:
            await self._reply(message, "Başka bir kullanıcının istatistiğini yalnızca bot sahibi görebilir.")
            return
        text = REQUESTS.format_user(getattr(target, "id", 0), hours, display_name=target_name)
        await self._reply(message, text[:1900])

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild or (self.channel_ids and message.channel.id not in self.channel_ids):
            return
        raw_content = (message.content or "").strip()
        content = raw_content.lower()
        cooldowns = getattr(self, "command_cooldowns", None)
        if (cooldowns is not None and raw_content.startswith("!")
                and message.author.id != STATS_OWNER_ID
                and not cooldowns.allow(message.author.id)):
            return
        if content.split(maxsplit=1)[0] in {"!yardim", "!yardım", "!help", "!komutlar"}:
            await self._reply(message, HELP_TEXT)
            return
        if content in {"!metrics", "!botmetrics", "!durum", "!botdurum"}:
            await self._reply_metrics(message)
            return
        if content.split(maxsplit=1)[0] in {"!stats", "!istatistik", "!log", "!logs", "!islem", "!işlem",
                                            "!panel", "!admin", "!kontrol", "!kontrolpanel",
                                            "!platform", "!platformdetay", "!detay"}:
            if message.author.id != STATS_OWNER_ID:
                await self._reply(message, "Bu komut yalnızca bot sahibine açık.")
                return
            argument = raw_content.split(maxsplit=1)[1] if len(raw_content.split(maxsplit=1)) > 1 else ""
            await self._reply_request_stats(message, argument)
            return
        if content.split(maxsplit=1)[0] in {"!logtemizle", "!logclear"}:
            if message.author.id != STATS_OWNER_ID:
                await self._reply(message, "Bu komut yalnızca bot sahibine açık.")
                return
            argument = raw_content.split(maxsplit=1)[1] if len(raw_content.split(maxsplit=1)) > 1 else ""
            await self._reply_log_clear(message, argument)
            return
        if content.split(maxsplit=1)[0] in {"!logsil", "!logdelete"}:
            argument = raw_content.split(maxsplit=1)[1] if len(raw_content.split(maxsplit=1)) > 1 else ""
            await self._reply_log_delete_user(message, argument)
            return
        if content.split(maxsplit=1)[0] in {"!hatalar", "!hataları", "!errors"}:
            if message.author.id != STATS_OWNER_ID:
                await self._reply(message, "Bu komut yalnızca bot sahibine açık.")
                return
            argument = raw_content.split(maxsplit=1)[1] if len(raw_content.split(maxsplit=1)) > 1 else ""
            await self._reply_errors(message, argument)
            return
        if content.split(maxsplit=1)[0] in {"!cache", "!önbellek", "!onbellek"}:
            await self._reply_cache(message)
            return
        if content.split(maxsplit=1)[0] in {"!cachetemizle", "!önbellektemizle", "!onbellektemizle"}:
            if message.author.id != STATS_OWNER_ID:
                await self._reply(message, "Bu komut yalnızca bot sahibine açık.")
                return
            await self._reply_cache_clear(message)
            return
        if content.split(maxsplit=1)[0] in {"!info", "!profil", "!me", "!kimlik"}:
            argument = raw_content.split(maxsplit=1)[1] if len(raw_content.split(maxsplit=1)) > 1 else ""
            await self._reply_user_info(message, argument)
            return
        if content.split(maxsplit=1)[0] in {"!bakim", "!maintenance"}:
            if message.author.id != STATS_OWNER_ID:
                await self._reply(message, "Bu komut yalnızca bot sahibine açık.")
                return
            argument = raw_content.split(maxsplit=1)[1] if len(raw_content.split(maxsplit=1)) > 1 else ""
            await self._reply_maintenance(message, argument)
            return
        if content.split(maxsplit=1)[0] in {"!ayar", "!ayarlar", "!settings"}:
            argument = raw_content.split(maxsplit=1)[1] if len(raw_content.split(maxsplit=1)) > 1 else ""
            await self._reply_settings(message, argument)
            return
        links = find_links(message.content, self.max_links)
        if not links:
            return
        if MAINTENANCE.active and message.author.id != STATS_OWNER_ID:
            await self._reply(message, MAINTENANCE.message)
            return
        auto_thread = getattr(self, "_auto_thread_message", None)
        if auto_thread:
            message = await auto_thread(message, links)
        queue_notice: discord.Message | None = None

        async def on_wait() -> None:
            nonlocal queue_notice
            try:
                queue_notice = await self._reply(message, "⏳ Sıradasın; bağlantın işlenecek.")
            except discord.HTTPException:
                pass

        try:
            async with self.job_slots.slot(message.author.id, on_wait):
                if queue_notice:
                    try:
                        await queue_notice.edit(content="⏳ Bağlantın hazırlanıyor.")
                    except discord.HTTPException:
                        pass
                async def process_link(link: PostLink, status_message: discord.Message | None) -> None:
                    outcome = "failure"
                    detail = ""
                    files: list[Path] = []
                    pending_target = None
                    precomputed_bytes: int | None = None
                    started = time.monotonic()
                    try:
                        result = await self._handle_link(message, link, status_message=status_message)
                        if isinstance(result, tuple):
                            outcome, files = result[0], result[1]
                            if len(result) > 2:
                                third = result[2]
                                if isinstance(third, int) and not hasattr(third, "request_id"):
                                    precomputed_bytes = third
                                elif third is not None:
                                    pending_target = third
                            if len(result) > 3 and isinstance(result[3], int):
                                precomputed_bytes = result[3]
                        elif result in {"success", "failure", "pending", "skipped"}:
                            outcome = result
                        elif result is None:
                            outcome = "success"
                        else:
                            outcome = "success"
                    except discord.Forbidden:
                        LOG.warning("Kanal için mesaj veya dosya gönderme izni yok: %s", message.channel.id)
                        outcome = "failure"
                        detail = "forbidden"
                        REQUESTS.record(
                            guild_id=getattr(message.guild, "id", None),
                            guild_name=getattr(message.guild, "name", None),
                            channel_id=getattr(message.channel, "id", None),
                            user_id=getattr(message.author, "id", None),
                            user_name=str(getattr(message.author, "name", message.author)),
                            platform=link.platform,
                            url=link.url,
                            outcome=outcome,
                            detail=detail,
                            elapsed_ms=int((time.monotonic() - started) * 1000),
                        )
                        return
                    except discord.HTTPException as exc:
                        LOG.warning("Discord upload error: %s", exc)
                        text = str(exc).lower()
                        if exc.status == 413 or "entity too large" in text or "larger than" in text or "40005" in text:
                            await self._reply(message, "Dosya Discord yükleme sınırını aşıyor (sunucu/kanal limiti nedeniyle gönderilemedi).")
                        else:
                            await self._reply(message, "Dosya Discord'a yüklenemedi. Kanal izinlerini ve yükleme sınırını kontrol edin.")
                        outcome = "failure"
                        detail = f"http:{exc.status}"
                    except Exception:
                        LOG.exception("Unexpected error processing %s", link.platform)
                        await self._reply(message, "Bu bağlantı işlenirken beklenmeyen bir hata oluştu.")
                        outcome = "failure"
                        detail = "exception"
                    finally:
                        file_count = len(files)
                        if precomputed_bytes is not None:
                            total_bytes = precomputed_bytes
                        else:
                            total_bytes = sum(p.stat().st_size for p in files if p.is_file())
                        record_id = REQUESTS.record(
                            guild_id=getattr(message.guild, "id", None),
                            guild_name=getattr(message.guild, "name", None),
                            channel_id=getattr(message.channel, "id", None),
                            user_id=getattr(message.author, "id", None),
                            user_name=str(getattr(message.author, "name", message.author)),
                            platform=link.platform,
                            url=link.url,
                            outcome=outcome,
                            detail=detail,
                            elapsed_ms=int((time.monotonic() - started) * 1000),
                            file_count=file_count,
                            bytes=total_bytes,
                        )
                        if pending_target is not None and record_id:
                            pending_target.request_id = record_id
                    if outcome == "failure" and detail != "forbidden":
                        try:
                            await self._reply(
                                message,
                                "↩️ İşlem başarısız oldu. Tekrar denemek için:",
                                view=RetryView(bot=self, source_message=message, link=link),
                            )
                        except discord.HTTPException:
                            pass

                if len(links) == 1:
                    await process_link(links[0], queue_notice)
                else:
                    # Multiple links used to run one after another. Download two
                    # at a time and let each upload as soon as it is ready; keep
                    # the shared status message static to avoid edit races.
                    if queue_notice:
                        try:
                            await queue_notice.edit(content=f"⏳ {len(links)} bağlantı paralel işleniyor...")
                        except discord.HTTPException:
                            pass
                    link_slots = asyncio.Semaphore(2)

                    async def run_link(link: PostLink) -> None:
                        async with link_slots:
                            await process_link(link, None)

                    await asyncio.gather(*(run_link(link) for link in links))
        finally:
            if queue_notice:
                try:
                    await queue_notice.delete()
                except discord.HTTPException:
                    pass

    async def _reply(self, message: discord.Message, content: str = "", files: list[Path] | None = None,
                     *, embed: discord.Embed | None = None, view: discord.ui.View | None = None) -> discord.Message:
        try:
            return await self._reply_once(message, content, files, embed=embed, view=view)
        except discord.HTTPException as exc:
            if not files or not _upload_too_large(exc):
                raise
            shrunk = await self._fit_attachments(files, message)
            if not shrunk:
                raise
            LOG.info("Discord rejected an upload as too large; retrying %d file(s) smaller", len(shrunk))
            return await self._reply_once(message, content, shrunk, embed=embed, view=view)

    async def _reply_once(self, message: discord.Message, content: str = "", files: list[Path] | None = None,
                          *, embed: discord.Embed | None = None,
                          view: discord.ui.View | None = None) -> discord.Message:
        with ExitStack() as stack:
            attachments = []
            for path in files or []:
                attachment = discord.File(path, filename=path.name)
                stack.callback(attachment.close)
                attachments.append(attachment)
            use_reply = True
            if isinstance(message, discord.Message):
                guild = getattr(message, "guild", None)
                if guild is not None:
                    try:
                        use_reply = SETTINGS.get(guild.id).reply_messages
                    except Exception:
                        use_reply = True
            content_out = content[:1900] or None
            if use_reply:
                return await message.reply(content=content_out, files=attachments, embed=embed, view=view,
                                           mention_author=False, suppress_embeds=embed is None)
            channel = getattr(message, "channel", None)
            send = getattr(channel, "send", None) if channel is not None else None
            if send is None:
                return await message.reply(content=content_out, files=attachments, embed=embed, view=view,
                                           mention_author=False, suppress_embeds=embed is None)
            return await send(content=content_out, files=attachments, embed=embed, view=view,
                              suppress_embeds=embed is None)

    async def _fit_attachments(self, files: list[Path], message: object) -> list[Path] | None:
        """Reencode oversized videos after a 413 so one retry can succeed."""
        limit = upload_limit(message, self.upload_bytes)
        reduced: list[Path] = []
        changed = False
        for path in files:
            try:
                size = path.stat().st_size
            except OSError:
                reduced.append(path)
                continue
            if path.suffix.lower() not in VIDEO_SUFFIXES or size < 2 * 1024 * 1024:
                reduced.append(path)
                continue
            target = path.with_name(f"{path.stem}.fit{path.suffix}")
            budget = max(1, int(min(size * 0.7, (limit or size) * 0.9)))
            if await fit_video(path, target, budget, force=True, max_height=480):
                reduced.append(target)
                changed = True
            else:
                reduced.append(path)
        return reduced if changed else None

    async def _handle_link(self, message: discord.Message, link: PostLink, status_message: discord.Message | None = None) -> str | tuple[str, list[Path]] | tuple[str, list[Path], YoutubeChoiceView]:
        async def set_status(text: str) -> None:
            if not status_message:
                return
            try:
                await status_message.edit(content=text)
            except discord.HTTPException:
                pass

        if link.platform == "threads":
            await set_status("⏳ Bağlantı çözümleniyor...")
            link = await resolve_threads_link(link, self.cookie_file) or link
            if highlight := highlight_post_link(link.url):
                # Threads-only short links like /s/highlight:<ig_id> point at an
                # Instagram highlight album, so hand off to the Instagram flow.
                link = highlight
        if link.platform == "spotify":
            await set_status("⏳ Parça hazırlanıyor...")
            return await self._handle_spotify(message, link, status_message=status_message)
        if link.platform == "bluesky":
            await set_status("⏳ Gönderi alınıyor...")
            return await self._handle_bluesky(message, link, status_message=status_message)
        if link.platform == "reddit":
            await set_status("⏳ Gönderi hazırlanıyor...")
            return await self._handle_reddit(message, link, status_message=status_message)
        if link.platform == "youtube":
            limit = upload_limit(message, self.upload_bytes)
            quality = self._youtube_quality(message)
            if quality == "sor":
                view = YoutubeChoiceView(bot=self, source_message=message, link=link,
                                         title="YouTube videosu", upload_limit=limit)
                view.prompt_message = await message.reply(view=view, mention_author=False, suppress_embeds=True)
                return "pending", [], view
            started = time.monotonic()
            status = None
            try:
                status = await message.reply(
                    view=status_view("YouTube videosu", f"⏳ **{quality}** hazırlanıyor…"),
                    mention_author=False, suppress_embeds=True,
                )
            except discord.HTTPException as exc:
                LOG.warning("YouTube durum kartı gönderilemedi: %s", exc)
            ok, files, detail = await deliver_youtube(
                self, message, link, quality, limit, started=started,
            )
            if status:
                text = (f"✅ **{detail}** gönderildi." if ok else f"❌ {detail[:300]}")
                try:
                    await status.edit(view=status_view("YouTube videosu", text))
                except discord.HTTPException as exc:
                    LOG.warning("YouTube durum kartı güncellenemedi: %s", exc)
            return ("success" if ok else "failure"), files
        if link.platform == "x":
            await set_status("⏳ Gönderi hazırlanıyor...")
            return await self._handle_x(message, link, status_message=status_message)
        caption_task = asyncio.create_task(fetch_caption(link, self.cookie_file))

        with tempfile.TemporaryDirectory(prefix="discord-media-") as tmp:
            folder = Path(tmp)
            media: list[Path] = []
            error: DownloadError | None = None
            user_id = getattr(getattr(message, "author", None), "id", None)
            await set_status("⏳ Medya indiriliyor...")
            limit_bytes = upload_limit(message, self.upload_bytes)
            sent_files: list[Path] = []
            uploaded: set[Path] = set()
            batches_sent = 0
            caption: str | None = None
            caption_resolved = False
            caption_pending = False
            chunks: list[str] = []
            first_media_message: discord.Message | None = None
            first_media_batch: list[Path] = []
            caption_included = False
            caption_patch_task: asyncio.Task | None = None

            async def resolve_caption(*, timeout: float | None = None, wait: bool = False) -> None:
                nonlocal caption, caption_resolved, caption_pending, chunks
                if caption_resolved:
                    return
                try:
                    if wait or timeout is None:
                        caption = await caption_task
                        caption_resolved = True
                        caption_pending = False
                    elif caption_task.done():
                        caption = await caption_task
                        caption_resolved = True
                        caption_pending = False
                    else:
                        caption = await asyncio.wait_for(asyncio.shield(caption_task), timeout=timeout)
                        caption_resolved = True
                        caption_pending = False
                except TimeoutError:
                    LOG.info("Caption fetch timed out for %s; will patch later if available", link.platform)
                    caption_pending = not caption_task.done()
                    if not caption_pending:
                        try:
                            caption = await caption_task
                        except Exception:
                            caption = None
                        caption_resolved = True
                    return
                except Exception:
                    LOG.exception("Caption unavailable for %s", link.platform)
                    caption = None
                    caption_resolved = True
                    caption_pending = False
                chunks = display_chunks(caption)

            def _track_caption_patch_task(task: asyncio.Task) -> None:
                try:
                    task.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    LOG.exception("Caption patch task failed for %s", link.platform)

            async def maybe_patch_caption() -> None:
                nonlocal caption, caption_resolved, caption_pending, chunks, caption_included
                if first_media_message is None or caption_included or not caption_pending:
                    return
                try:
                    caption = await caption_task
                except Exception:
                    caption = None
                caption_resolved = True
                caption_pending = False
                chunks = display_chunks(caption)
                if not caption or not chunks:
                    try:
                        await first_media_message.edit(view=media_gallery_view(
                            link.platform, first_media_batch, None, link.url, part=1,
                            show_missing_caption=True,
                        ))
                    except discord.HTTPException:
                        pass
                    return
                try:
                    await first_media_message.edit(view=media_gallery_view(
                        link.platform, first_media_batch, chunks[0], link.url, part=1,
                        show_missing_caption=False,
                    ))
                    caption_included = True
                except discord.HTTPException as exc:
                    LOG.warning("Caption edit failed for %s: %s", link.platform, exc)
                    return
                for index, text_chunk in enumerate(chunks[1:], batches_sent + 1):
                    try:
                        await self._reply(message, view=media_gallery_view(
                            link.platform, [], text_chunk, link.url, part=index,
                            show_missing_caption=False,
                        ))
                    except discord.HTTPException as exc:
                        LOG.warning("Caption continuation send failed for %s: %s", link.platform, exc)
                        break

            def schedule_caption_patch() -> None:
                nonlocal caption_patch_task
                if caption_patch_task or first_media_message is None or caption_included or not caption_pending:
                    return
                caption_patch_task = asyncio.create_task(maybe_patch_caption())
                caption_patch_task.add_done_callback(_track_caption_patch_task)

            async def send_paths(paths: list[Path]) -> None:
                nonlocal batches_sent, first_media_message, first_media_batch, caption_included
                batch = [p for p in paths if p not in uploaded and p.is_file()]
                if not batch:
                    return
                if batches_sent == 0:
                    await resolve_caption(timeout=1.0)
                batches_sent += 1
                is_first = first_media_message is None
                show_missing = caption_resolved or (not caption_pending and is_first)
                if is_first and caption_pending:
                    show_missing = False
                if status_message:
                    await set_status(f"📤 {batches_sent}. grup gönderiliyor ({len(sent_files) + len(batch)} dosya)…")
                view = media_gallery_view(
                    link.platform, batch,
                    chunks[0] if is_first and caption_resolved and chunks else None,
                    link.url, part=batches_sent,
                    show_missing_caption=show_missing,
                )
                sent = await self._reply(message, files=batch, view=view)
                for p in batch:
                    uploaded.add(p)
                sent_files.extend(batch)
                if is_first:
                    first_media_message = sent
                    first_media_batch = list(batch)
                    caption_included = bool(caption_resolved and caption)
                    if caption_pending and not caption_included:
                        schedule_caption_patch()

            async def on_stream_file(paths: list[Path]) -> None:
                # Upload streamed files promptly (progressive delivery).
                for batch in attachment_batches(paths):
                    await send_paths(batch)

            try:
                try:
                    media, _ = await download_media(
                        link, folder, limit_bytes, self.cookie_file,
                        self.cobalt_url, self.cobalt_key, self.gallery_rs_path,
                        user_id=user_id, on_file=on_stream_file,
                    )
                except DownloadError as exc:
                    error = exc
                    media = []
                except BaseException:
                    if caption_patch_task is None and not caption_task.done():
                        caption_task.cancel()
                    if caption_patch_task is None:
                        await asyncio.gather(caption_task, return_exceptions=True)
                    raise

                if not media and not sent_files:
                    await resolve_caption(wait=True)
                    preview = await self._reddit_message_preview(message, link) if link.platform == "reddit" else Preview()
                    if not preview.has_content:
                        preview = await fetch_preview(link)
                    if caption or preview.has_content:
                        for embed in preview_embeds(link, preview, caption):
                            await self._reply(message, embed=embed, view=source_button(link.url))
                        return "success", [], None, 0
                    else:
                        if link.platform == "threads" and re.search(r"/(?:share|t|s)/", link.url):
                            await self._reply(message, "Threads paylaşım bağlantısı çözülemedi; Threads gönderiyi bot için giriş sayfasına yönlendirmiş olabilir.", view=source_button(link.url))
                        else:
                            await self._reply(message, f"{link.platform.upper()}: {error or 'Medya bulunamadı.'}")
                        return "failure", [], None, 0

                # Flush any files from the authoritative result that were not streamed/sent.
                remaining = [p for p in media if p not in uploaded]
                for batch in attachment_batches(remaining):
                    await send_paths(batch)

                if first_media_message and caption_included and not caption_resolved:
                    # shouldn't happen, but ensure state
                    await resolve_caption(wait=True)

                if caption_pending and first_media_message and not caption_included:
                    # Caption still pending after delivery; patch/edit in background when ready.
                    schedule_caption_patch()
                else:
                    if not caption_resolved:
                        await resolve_caption(wait=True)
                    # If caption resolved only after sends (e.g. no media path handled above),
                    # or was included, send any continuation chunks now.
                    if caption_resolved and chunks and caption_included:
                        for index, text_chunk in enumerate(chunks[1:], batches_sent + 1):
                            await self._reply(message, view=media_gallery_view(
                                link.platform, [], text_chunk, link.url, part=index,
                                show_missing_caption=False,
                            ))
                    elif caption_resolved and chunks and first_media_message and not caption_included:
                        # Caption became available but was not included in first message; patch now.
                        try:
                            await first_media_message.edit(view=media_gallery_view(
                                link.platform, first_media_batch, chunks[0], link.url, part=1,
                                show_missing_caption=False,
                            ))
                            caption_included = True
                            for index, text_chunk in enumerate(chunks[1:], batches_sent + 1):
                                await self._reply(message, view=media_gallery_view(
                                    link.platform, [], text_chunk, link.url, part=index,
                                    show_missing_caption=False,
                                ))
                        except discord.HTTPException as exc:
                            LOG.warning("Caption edit failed for %s: %s", link.platform, exc)

                if status_message and (sent_files or media):
                    await set_status(f"✅ {len(sent_files) or len(media)} dosya gönderildi.")
                total_bytes = sum(p.stat().st_size for p in media if p.is_file())
                return "success", media, None, total_bytes
            finally:
                if caption_patch_task is None:
                    if not caption_task.done():
                        caption_task.cancel()
                    await asyncio.gather(caption_task, return_exceptions=True)

    async def _reddit_message_preview(self, message: discord.Message, link: PostLink) -> Preview:
        preview = reddit_embed_preview(link, getattr(message, "embeds", []))
        if preview.has_content:
            return preview
        for _ in range(3):
            await asyncio.sleep(0.5)
            try:
                refreshed = await message.channel.fetch_message(message.id)
            except (discord.HTTPException, AttributeError):
                return Preview()
            preview = reddit_embed_preview(link, getattr(refreshed, "embeds", []))
            if preview.has_content:
                return preview
        return Preview()

    async def _handle_bluesky(self, message: discord.Message, link: PostLink, status_message: discord.Message | None = None) -> str | tuple[str, list[Path]]:
        async def set_status(text: str) -> None:
            if not status_message:
                return
            try:
                await status_message.edit(content=text)
            except discord.HTTPException:
                pass

        try:
            post = await fetch_bluesky_post(link)
        except httpx.HTTPError as exc:
            LOG.warning("Bluesky API unavailable: %s", exc)
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            text = ("Bluesky hız sınırına ulaştı; kısa süre sonra tekrar dene." if status == 429 else
                    "Bluesky gönderisi bulunamadı veya erişim engellendi." if status in {400, 401, 403, 404} else
                    "Bluesky gönderisi şu anda alınamadı.")
            await self._reply(message, text, view=source_button(link.url))
            return "failure", []
        if post is None:
            await self._reply(message, "Bluesky gönderisi bulunamadı.", view=source_button(link.url))
            return "failure", []
        if post.adult:
            if not channel_is_nsfw(message.channel):
                await self._reply(message, "+18 Bluesky gönderileri yalnızca NSFW kanallarda kullanılabilir.")
                return "failure", []
            if not await self.adult_quota.aclaim(message.author.id, istanbul_day()):
                await self._reply(message, "Günlük +18 indirme hakkını kullandın.")
                return "failure", []
        with tempfile.TemporaryDirectory(prefix="discord-bluesky-") as tmp:
            await set_status("⏳ Medya indiriliyor...")
            try:
                media = await download_bluesky_media(post, Path(tmp), upload_limit(message, self.upload_bytes))
            except OSError as exc:
                LOG.warning("Bluesky media unavailable: %s", exc)
                media = []
            caption = post.caption
            if post.media and not media:
                caption += "\n\nMedya indirilemedi."
            chunks = display_chunks(caption) or [""]
            batches = list(attachment_batches(media)) or [[]]
            total = len([b for b in batches if b])
            sent = 0
            for index, batch in enumerate(batches):
                if batch and status_message and total > 1:
                    sent += 1
                    await set_status(f"📤 {sent}/{total} grup gönderiliyor…")
                elif batch and status_message:
                    await set_status("📤 Gönderiliyor…")
                await self._reply(message, files=batch,
                                  view=media_gallery_view("bluesky", batch,
                                                          chunks[0] if index == 0 else None,
                                                          link.url, part=index + 1,
                                                          show_missing_caption=False,
                                                          descriptions=list(post.alt_texts[index * 10:(index + 1) * 10])))
            for index, chunk in enumerate(chunks[1:], len(batches) + 1):
                await self._reply(message, view=media_gallery_view("bluesky", [], chunk,
                                                                   link.url, part=index,
                                                                   show_missing_caption=False))
            if status_message and total > 1:
                await set_status("✅ Gönderim tamamlandı.")
            return "success", media

    async def _handle_reddit(self, message: discord.Message, link: PostLink, status_message: discord.Message | None = None) -> str | tuple[str, list[Path]]:
        preview = (await self._reddit_message_preview(message, link)
                   if not link.post_id else Preview())
        subreddit_prefix = re.match(r"^r/[^ ]+ · ", preview.title or "")
        title_hint = (preview.title or "")[subreddit_prefix.end():] if subreddit_prefix else None
        try:
            post = await asyncio.wait_for(fetch_reddit_post(link, title_hint), timeout=6)
        except TimeoutError:
            post = None
        adult = await reddit_adult_status(post)
        if adult is None:
            await self._reply(message, "Reddit gönderisinin +18 durumu doğrulanamadı; güvenlik nedeniyle işlenmedi.")
            return "failure", []
        if adult:
            if not channel_is_nsfw(message.channel):
                await self._reply(message, "+18 Reddit gönderileri yalnızca NSFW olarak işaretlenmiş kanallarda kullanılabilir.")
                return "failure", []
            day = istanbul_day()
            if not await self.adult_quota.aclaim(message.author.id, day):
                await self._reply(message, "Günlük +18 indirme hakkını kullandın. Yeni hak Türkiye saatiyle gece yarısında açılır.")
                return "failure", []
            return await MediaBot._deliver_reddit(self, message, link, post, preview, title_hint, status_message=status_message)
        return await MediaBot._deliver_reddit(self, message, link, post, preview, title_hint, status_message=status_message)

    async def _deliver_reddit(self, message: discord.Message, link: PostLink,
                              post: RedditPost | None, preview: Preview,
                              title_hint: str | None, status_message: discord.Message | None = None) -> tuple[str, list[Path]]:
        source_url = post.url if post else link.url
        media_files: list[Path] = []
        with tempfile.TemporaryDirectory(prefix="discord-reddit-") as tmp:
            if not post and not preview.has_content:
                preview = await self._reddit_message_preview(message, link)
                if not preview.has_content:
                    preview = await fetch_preview(link)
            display_post = post
            if not display_post and preview.has_content:
                subreddit = re.search(r"/r/([^/]+)", urlsplit(link.url).path, re.IGNORECASE)
                preview_title = preview.title or "Reddit gönderisi"
                if " · " in preview_title and preview_title.startswith("r/"):
                    preview_title = preview_title.split(" · ", 1)[1]
                display_post = RedditPost(
                    title_hint or preview_title, preview.description or "",
                    subreddit.group(1) if subreddit else "reddit", "", link.url,
                )
            if display_post:
                bot_user = getattr(self, "user", None)
                footer = f"@{bot_user.name}" if bot_user else "Discord Video Bot"
                cards = await asyncio.to_thread(render_reddit_cards, display_post, Path(tmp) / "cards",
                                                footer, preview_only=post is None)
                for index, batch in enumerate(attachment_batches(cards), 1):
                    await self._reply(
                        message, files=batch,
                        view=media_gallery_view("reddit", batch, None, source_url,
                                                part=index, show_missing_caption=False),
                    )
                delivered_any = True
            else:
                await self._reply(message, "Reddit gönderi bilgisi alınamadı.", view=source_button(link.url))
                delivered_any = False
            if post and post.is_self is True:
                return ("success" if delivered_any else "failure"), media_files
            if status_message:
                try:
                    await status_message.edit(content="⏳ Medya indiriliyor...")
                except discord.HTTPException:
                    pass
            try:
                media_link = parse_link(source_url) or link
                user_id = getattr(getattr(message, "author", None), "id", None)
                media, _ = await download_media(
                    media_link, Path(tmp), upload_limit(message, self.upload_bytes), self.cookie_file,
                    self.cobalt_url, self.cobalt_key, self.gallery_rs_path,
                    user_id=user_id,
                )
            except DownloadError:
                return ("success" if delivered_any else "failure"), media_files
            media_files.extend(media)
            for index, batch in enumerate(attachment_batches(media), 1):
                await self._reply(message, files=batch,
                                  view=media_gallery_view("reddit", batch, None, source_url,
                                                          part=index, show_missing_caption=False))
                delivered_any = True
            return ("success" if delivered_any else "failure"), media_files

    async def _handle_spotify(self, message: discord.Message, link: PostLink, status_message: discord.Message | None = None) -> str | tuple[str, list[Path]]:
        try:
            track = await fetch_track(link)
            source = await find_audio_source(track)
            with tempfile.TemporaryDirectory(prefix="discord-spotify-") as tmp:
                if status_message:
                    try:
                        await status_message.edit(content="⏳ Ses indiriliyor...")
                    except discord.HTTPException:
                        pass
                file, provider = await download_youtube(
                    source, Path(tmp), "mp3", upload_limit(message, self.upload_bytes),
                    self.cookie_file, self.cobalt_url, self.cobalt_key,
                )
                safe_title = re.sub(r"[^\w .-]", "", track.title, flags=re.UNICODE).strip()[:90] or "parca"
                output = Path(tmp) / f"{safe_title}.mp3"
                file.rename(output)
                await self._reply(message, files=[output])
                LOG.info("Spotify track delivered as MP3 via %s", provider)
                return "success", [output]
        except DownloadError as exc:
            await self._reply(message, f"Spotify parçası indirilemedi: {exc}", view=source_button(link.url))
            return "failure", []

    async def _handle_x(self, message: discord.Message, link: PostLink, status_message: discord.Message | None = None) -> str | tuple[str, list[Path]]:
        with tempfile.TemporaryDirectory(prefix="discord-x-") as tmp:
            folder = Path(tmp)
            limit = upload_limit(message, self.upload_bytes)
            post = await fetch_post(link.post_id or "")
            media_task = None
            if post is None or _has_media(post):
                user_id = getattr(getattr(message, "author", None), "id", None)
                if status_message:
                    try:
                        await status_message.edit(content="⏳ Medya indiriliyor...")
                    except discord.HTTPException:
                        pass
                media_task = asyncio.create_task(download_media(
                    link, folder / "media", limit, self.cookie_file,
                    self.cobalt_url, self.cobalt_key, self.gallery_rs_path,
                    user_id=user_id,
                ))
            try:
                card_sent = False
                if post:
                    card = folder / "x_gonderisi.png"
                    try:
                        bot_user = getattr(self, "user", None)
                        footer = f"@{bot_user.name}" if bot_user else "Discord Video Bot"
                        await create_card(post, card, footer)
                    except Exception:
                        LOG.exception("Could not render X card")
                    else:
                        if card.is_file() and 0 < card.stat().st_size <= limit:
                            await self._reply(message, "**X gönderisi**", [card], view=source_button(link.url))
                            card_sent = True

                if media_task is None:
                    if not card_sent:
                        ok = await self._send_x_fallback(message, link, post, "Bu gönderide indirilecek medya yok.")
                        return ("success" if ok else "failure"), []
                    return "success", []

                try:
                    media, _ = await media_task
                except DownloadError as exc:
                    if not card_sent:
                        ok = await self._send_x_fallback(message, link, post, str(exc))
                        return ("success" if ok else "failure"), []
                    return "success", []
                for index, chunk in enumerate(attachment_batches(media)):
                    content = "**X medyası**" if index == 0 else "**X medyası** · devam"
                    await self._reply(message, content, chunk, view=source_button(link.url))
                return "success", media
            finally:
                if media_task is not None:
                    if not media_task.done():
                        media_task.cancel()
                    await asyncio.gather(media_task, return_exceptions=True)

    async def _send_x_fallback(self, message: discord.Message, link: PostLink,
                               post: dict | None, error: str) -> bool:
        preview = (Preview(description=post.get("text")) if post else await fetch_preview(link))
        if preview.has_content:
            for embed in preview_embeds(link, preview):
                await self._reply(message, embed=embed, view=source_button(link.url))
            return True
        else:
            await self._reply(message, f"X: {error}")
            return False


def main() -> None:
    from .bot_entrypoint import main as _main
    _main()


if __name__ == "__main__":
    main()
