"""Discord gateway entry point."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import discord
import httpx
from dotenv import load_dotenv

from .adult import AdultQuota, channel_is_nsfw, istanbul_day, reddit_adult_status
from .bluesky import download_bluesky_media, fetch_bluesky_post
from .captions import display_chunks, fetch_caption
from .download import DownloadError, download_media
from .highlight_state import HIGHLIGHTS, format_wait
from .indownio import is_rate_limited_message, resolve_indownio
from .links import PostLink, find_links, parse_link
from .job_slots import JobSlots
from .limits import attachment_batches
from .maintenance import MAINTENANCE, env_enabled
from .metadata_fallback import Preview, fetch_preview, preview_embeds
from .media_cache import MEDIA_CACHE
from .provider_metrics import METRICS
from .request_log import REQUESTS
from .reddit_card import render_reddit_cards
from .reddit_post import RedditPost, fetch_reddit_post
from .spotify import fetch_track, find_audio_source
from .threads_links import highlight_post_link, resolve_threads_link
from .x_card import create_card, fetch_post
from .youtube_view import YoutubeChoiceView
from .youtube import download_youtube

LOG = logging.getLogger(__name__)
STATS_OWNER_ID = int(os.getenv("STATS_OWNER_ID", "1065352859195015260"))
COLORS = {"instagram": 0xC13584, "facebook": 0x1877F2, "reddit": 0xFF4500,
          "tiktok": 0x25F4EE, "youtube": 0xFF0033, "x": 0x20262E,
          "threads": 0x20262E, "douyin": 0x25F4EE, "weibo": 0xE6162D,
          "streamable": 0x0F8FFA, "bluesky": 0x1083FE}
def source_button(url: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Kaynak", emoji="🔗", style=discord.ButtonStyle.link, url=url))
    return view


def _reel_caption(caption: str) -> tuple[str, str | None, str | None]:
    """Split Instagram's "<text> - @user on <date>" tail into handle and date."""
    match = re.search(r"\s*-\s*@([\w.]+)\s+on\s+(.+?)\s*$", caption, re.S)
    if not match:
        return caption.strip(), None, None
    return caption[:match.start()].rstrip(), match.group(1), match.group(2).strip()


_MONTHS_TR = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
              "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")


def _instagram_date(media_id: str) -> str | None:
    """Decode the creation date from an Instagram media id (no request needed)."""
    if not media_id:
        return None
    try:
        moment = datetime.fromtimestamp(((int(media_id) >> 23) + 1314220021721) / 1000)
    except (ValueError, OverflowError, OSError):
        return None
    if not 2011 <= moment.year <= datetime.now().year + 1:
        return None
    return f"{moment.day} {_MONTHS_TR[moment.month - 1]} {moment.year}"


def _instagram_story_meta(url: str) -> str | None:
    """`@handle · date` footer for a story link; captions are never shown."""
    match = re.fullmatch(r"/stories/([^/]+)/(\d*)/?", urlsplit(url).path, re.I)
    if not match or match.group(1).lower() == "highlights":
        return None
    username = match.group(1)
    parts = [f"[@{username}](https://www.instagram.com/{username}/)"]
    posted = _instagram_date(match.group(2))
    if posted:
        parts.append(posted)
    return " · ".join(parts)


def _quote(text: str) -> str:
    """Bold every caption line inside a blockquote."""
    return "\n".join("> **" + line + "**" if line else ">" for line in text.splitlines())


def media_gallery_view(platform: str, files: list[Path], caption: str | None,
                       source_url: str, *, part: int = 1,
                       show_missing_caption: bool = True,
                       descriptions: list[str] | None = None) -> discord.ui.LayoutView:
    label = {"instagram": "Instagram", "facebook": "Facebook", "reddit": "Reddit",
             "tiktok": "TikTok", "youtube": "YouTube", "x": "X", "threads": "Threads",
             "douyin": "Douyin", "weibo": "Weibo", "streamable": "Streamable",
             "bluesky": "Bluesky"}[platform]
    heading = f"### {label} gönderisi" if part == 1 else f"### {label} gönderisi · {part}. bölüm"
    children: list[discord.ui.Item] = [discord.ui.TextDisplay(heading)]
    visual = [path for path in files if path.suffix.lower() != ".mp3"]
    audio = [path for path in files if path.suffix.lower() == ".mp3"]
    if (platform == "instagram" and part == 1 and len(visual) == 1
            and visual[0].suffix.lower() in {".mp4", ".mov", ".webm", ".m4v"}
            and "/reel/" in urlsplit(source_url).path.lower()):
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(discord.ui.MediaGallery(
            discord.MediaGalleryItem(f"attachment://{visual[0].name}")
        ))
        details: list[discord.ui.Item] = [
            discord.ui.TextDisplay("## Instagram Reel"),
            discord.ui.Separator(),
        ]
        body, username, posted = _reel_caption(caption) if caption else ("", None, None)
        if body:
            details.append(discord.ui.TextDisplay(_quote(body[:3600])))
        elif show_missing_caption:
            details.append(discord.ui.TextDisplay("-# Gönderi açıklamasına erişilemiyor."))
        meta = " · ".join(part for part in (
            f"[@{username}](https://www.instagram.com/{username}/)" if username else "",
            posted or "",
        ) if part)
        source = discord.ui.Button(label="Kaynak", emoji="🔗",
                                   style=discord.ButtonStyle.link, url=source_url)
        details.append(discord.ui.Section(discord.ui.TextDisplay("-# " + meta), accessory=source)
                       if meta else discord.ui.ActionRow(source))
        view.add_item(discord.ui.Container(*details, accent_color=COLORS[platform]))
        return view
    if visual:
        children.append(discord.ui.MediaGallery(*(
            discord.MediaGalleryItem(
                f"attachment://{path.name}",
                description=(descriptions[index][:1024] or None) if descriptions and index < len(descriptions) else None,
            ) for index, path in enumerate(visual)
        )))
    for path in audio:
        children.append(discord.ui.File(f"attachment://{path.name}"))
    story_meta = _instagram_story_meta(source_url) if platform == "instagram" else None
    if caption:
        children.append(discord.ui.Separator())
        if platform == "facebook" and caption.rstrip().endswith(("...", "…")):
            children.append(discord.ui.TextDisplay("-# Açıklama önizlemesi"))
        children.append(discord.ui.TextDisplay(_quote(caption[:3600])))
    elif part == 1 and not story_meta and show_missing_caption:
        children.append(discord.ui.TextDisplay("-# Gönderi açıklamasına erişilemiyor."))
    source = discord.ui.Button(label="Kaynak", emoji="🔗", style=discord.ButtonStyle.link, url=source_url)
    if story_meta:
        children.append(discord.ui.Section(discord.ui.TextDisplay("-# " + story_meta), accessory=source))
    else:
        children.append(discord.ui.ActionRow(source))
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*children, accent_color=COLORS[platform]))
    return view


def reddit_embed_preview(link: PostLink, embeds: list[discord.Embed]) -> Preview:
    """Use Discord's Reddit unfurl when Reddit blocks anonymous requests."""
    subreddit = re.search(r"/r/([^/]+)", urlsplit(link.url).path, re.I)
    for embed in embeds:
        data = embed.to_dict()
        provider = (data.get("provider") or {}).get("name", "").lower()
        embed_url = data.get("url") or ""
        host = (urlsplit(embed_url).hostname or "").lower()
        if provider != "reddit" and host not in {"reddit.com", "www.reddit.com", "old.reddit.com"}:
            continue
        title = data.get("title") or None
        if subreddit and title:
            title = f"r/{subreddit.group(1)} · {title}"
        elif subreddit:
            title = f"r/{subreddit.group(1)}"
        description = data.get("description") or None
        image = (data.get("image") or {}).get("url") or (data.get("thumbnail") or {}).get("url")
        return Preview(title=title, description=description, image=image)
    return Preview()


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


def _positive_int(name: str, default: int, maximum: int) -> int:
    value = int(os.getenv(name, str(default)))
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} 1 ile {maximum} arasında olmalı")
    return value


class MediaBot(discord.Client):
    def __init__(self, *, channel_ids: set[int], upload_bytes: int, max_links: int, concurrency: int, cookie_file: Path | None, cobalt_url: str | None, cobalt_key: str | None, gallery_rs_path: Path | None, database_path: Path | None = None):
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
        self.adult_quota = AdultQuota(database_path or Path(__file__).resolve().parent.parent / "data" / "bot.sqlite3")
        self._health_task: asyncio.Task | None = None
        self._health_skips = 0

    async def on_ready(self) -> None:
        LOG.info("Hazır: %s (%s)", self.user, self.user.id if self.user else "?")
        if (HIGHLIGHTS.enabled and HIGHLIGHTS.health_check_enabled
                and (self._health_task is None or self._health_task.done())):
            self._health_task = asyncio.create_task(self._highlight_health_loop())

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

    async def _reply_request_stats(self, message: discord.Message) -> None:
        role = "Yönetici" if _is_admin(message) else "Kullanıcı"
        text = REQUESTS.format_panel(24, role=role, maintenance=MAINTENANCE.active)
        await self._reply(message, text[:1900])

    async def _reply_maintenance(self, message: discord.Message, argument: str) -> None:
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

    async def _reply_user_info(self, message: discord.Message, argument: str = "") -> None:
        target = message.author
        target_name = getattr(target, "display_name", None) or getattr(target, "name", None) or str(target)
        if message.mentions:
            target = message.mentions[0]
            target_name = getattr(target, "display_name", None) or getattr(target, "name", None) or str(target)
        elif argument.strip().isdigit():
            target_id = int(argument.strip())
            target = SimpleNamespace(id=target_id, display_name=str(target_id), name=str(target_id))
            target_name = str(target_id)
        if getattr(target, "id", None) != message.author.id and message.author.id != STATS_OWNER_ID:
            await self._reply(message, "Başka bir kullanıcının istatistiğini yalnızca bot sahibi görebilir.")
            return
        text = REQUESTS.format_user(getattr(target, "id", 0), 24, display_name=target_name)
        await self._reply(message, text[:1900])

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild or (self.channel_ids and message.channel.id not in self.channel_ids):
            return
        raw_content = (message.content or "").strip()
        content = raw_content.lower()
        if content in {"!metrics", "!botmetrics", "!durum", "!botdurum"}:
            await self._reply_metrics(message)
            return
        if content in {"!stats", "!istatistik", "!log", "!logs", "!islem", "!işlem",
                       "!panel", "!admin", "!kontrol", "!kontrolpanel"}:
            if message.author.id != STATS_OWNER_ID:
                await self._reply(message, "Bu komut yalnızca bot sahibine açık.")
                return
            await self._reply_request_stats(message)
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
        links = find_links(message.content, self.max_links)
        if not links:
            return
        if MAINTENANCE.active and message.author.id != STATS_OWNER_ID:
            await self._reply(message, MAINTENANCE.message)
            return
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
                for link in links:
                    outcome = "failure"
                    detail = ""
                    files: list[Path] = []
                    pending_target = None
                    started = time.monotonic()
                    try:
                        result = await self._handle_link(message, link, status_message=queue_notice)
                        if isinstance(result, tuple):
                            outcome, files = result[0], result[1]
                            if len(result) > 2:
                                pending_target = result[2]
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
        finally:
            if queue_notice:
                try:
                    await queue_notice.delete()
                except discord.HTTPException:
                    pass

    async def _reply(self, message: discord.Message, content: str = "", files: list[Path] | None = None,
                     *, embed: discord.Embed | None = None, view: discord.ui.View | None = None) -> discord.Message:
        with ExitStack() as stack:
            attachments = []
            for path in files or []:
                attachment = discord.File(path, filename=path.name)
                stack.callback(attachment.close)
                attachments.append(attachment)
            return await message.reply(content=content[:1900] or None, files=attachments, embed=embed, view=view,
                                       mention_author=False, suppress_embeds=embed is None)

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
            view = YoutubeChoiceView(bot=self, source_message=message, link=link, title="YouTube videosu")
            view.prompt_message = await message.reply(view=view, mention_author=False, suppress_embeds=True)
            return "pending", [], view
        if link.platform == "x":
            await set_status("⏳ Gönderi hazırlanıyor...")
            return await self._handle_x(message, link, status_message=status_message)
        caption_task = asyncio.create_task(fetch_caption(link, self.cookie_file))

        async def caption_or_none() -> str | None:
            try:
                return await caption_task
            except Exception:
                LOG.exception("Caption unavailable for %s", link.platform)
                return None

        with tempfile.TemporaryDirectory(prefix="discord-media-") as tmp:
            folder = Path(tmp)
            media: list[Path] = []
            error: DownloadError | None = None
            user_id = getattr(getattr(message, "author", None), "id", None)
            await set_status("⏳ Medya indiriliyor...")
            try:
                media, _ = await download_media(link, folder, self.upload_bytes, self.cookie_file, self.cobalt_url, self.cobalt_key, self.gallery_rs_path, user_id=user_id)
            except DownloadError as exc:
                error = exc
            except BaseException:
                if not caption_task.done():
                    caption_task.cancel()
                await asyncio.gather(caption_task, return_exceptions=True)
                raise
            if not media:
                caption = await caption_or_none()
                preview = await self._reddit_message_preview(message, link) if link.platform == "reddit" else Preview()
                if not preview.has_content:
                    preview = await fetch_preview(link)
                if caption or preview.has_content:
                    for embed in preview_embeds(link, preview, caption):
                        await self._reply(message, embed=embed, view=source_button(link.url))
                    return "success", []
                else:
                    if link.platform == "threads" and re.search(r"/(?:share|t|s)/", link.url):
                        await self._reply(message, "Threads paylaşım bağlantısı çözülemedi; Threads gönderiyi bot için giriş sayfasına yönlendirmiş olabilir.", view=source_button(link.url))
                    else:
                        await self._reply(message, f"{link.platform.upper()}: {error or 'Medya bulunamadı.'}")
                    return "failure", []
            try:
                caption = await caption_or_none()
                chunks = display_chunks(caption)
                for index, chunk in enumerate(attachment_batches(media)):
                    view = media_gallery_view(link.platform, chunk,
                                              chunks[0] if index == 0 and chunks else None,
                                              link.url, part=index + 1)
                    await self._reply(message, files=chunk, view=view)
                for index, text_chunk in enumerate(chunks[1:], 2):
                    await self._reply(message, view=media_gallery_view(link.platform, [], text_chunk,
                                                                       link.url, part=index))
                return "success", media
            finally:
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
                media = await download_bluesky_media(post, Path(tmp), self.upload_bytes)
            except OSError as exc:
                LOG.warning("Bluesky media unavailable: %s", exc)
                media = []
            caption = post.caption
            if post.media and not media:
                caption += "\n\nMedya indirilemedi."
            chunks = display_chunks(caption) or [""]
            batches = list(attachment_batches(media)) or [[]]
            for index, batch in enumerate(batches):
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
            return "success", media

    async def _handle_reddit(self, message: discord.Message, link: PostLink, status_message: discord.Message | None = None) -> str | tuple[str, list[Path]]:
        preview = (await self._reddit_message_preview(message, link)
                   if not link.post_id else Preview())
        subreddit_prefix = re.match(r"^r/[^ ]+ · ", preview.title or "")
        title_hint = (preview.title or "")[subreddit_prefix.end():] if subreddit_prefix else None
        try:
            post = await asyncio.wait_for(fetch_reddit_post(link, title_hint), timeout=6)
        except asyncio.TimeoutError:
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
                subreddit = re.search(r"/r/([^/]+)", urlsplit(link.url).path, re.I)
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
                    media_link, Path(tmp), self.upload_bytes, self.cookie_file,
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
                    source, Path(tmp), "mp3", self.upload_bytes,
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
                    link, folder / "media", self.upload_bytes, self.cookie_file,
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
                        if card.is_file() and 0 < card.stat().st_size <= self.upload_bytes:
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
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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
            from urllib.parse import urlsplit
            parsed = urlsplit(cobalt_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
                raise ValueError("COBALT_API_URL örneği: http://127.0.0.1:9000/")
        rs_raw = os.getenv("GALLERY_DL_RS_PATH", "").strip()
        gallery_rs_path = Path(rs_raw).resolve() if rs_raw else None
        if gallery_rs_path and not gallery_rs_path.is_file():
            raise ValueError("GALLERY_DL_RS_PATH mevcut bir programı göstermeli")
        database_path = Path(os.getenv("DATABASE_PATH", "data/bot.sqlite3")).resolve()
        METRICS.configure(database_path.with_name("provider_metrics.sqlite3"))
        REQUESTS.configure(database_path.with_name("request_log.sqlite3"))
        MAINTENANCE.configure(
            database_path.parent / "maintenance.json",
            enabled=env_enabled("MAINTENANCE_MODE"),
            message=os.getenv("MAINTENANCE_MESSAGE", ""),
        )
        MEDIA_CACHE.configure(
            database_path.parent / "media_cache",
            ttl=_positive_int("MEDIA_CACHE_TTL_MINUTES", 20, 1440) * 60,
            max_bytes=_positive_int("MEDIA_CACHE_MAX_MB", 1024, 10240) * 1024 * 1024,
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
            upload_bytes=_positive_int("MAX_UPLOAD_MB", 20, 500) * 1024 * 1024,
            max_links=_positive_int("MAX_LINKS_PER_MESSAGE", 3, 10),
            concurrency=_positive_int("MAX_CONCURRENT_JOBS", 2, 10),
            cookie_file=cookie_file,
            cobalt_url=cobalt_url,
            cobalt_key=os.getenv("COBALT_API_KEY", "").strip() or None,
            gallery_rs_path=gallery_rs_path,
            database_path=database_path,
        )
    except ValueError as exc:
        raise SystemExit(f"Yapılandırma hatası: {exc}") from exc
    try:
        bot.run(token, log_handler=None)
    except discord.PrivilegedIntentsRequired:
        raise SystemExit(
            "Discord, Message Content Intent erişimini reddetti. "
            "Discord Developer Portal > uygulamanız > Bot > Privileged Gateway Intents "
            "bölümünde Message Content Intent'i açıp kaydedin; sonra botu yeniden başlatın."
        ) from None


if __name__ == "__main__":
    main()
