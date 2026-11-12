"""Discord gateway entry point."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit

import discord
from dotenv import load_dotenv

from .adult import AdultQuota, channel_is_nsfw, istanbul_day, reddit_adult_status
from .captions import display_chunks, fetch_caption
from .download import DownloadError, download_media
from .links import PostLink, find_links, parse_link
from .limits import attachment_batches
from .metadata_fallback import Preview, fetch_preview, preview_embeds
from .reddit_card import render_reddit_cards
from .reddit_post import RedditPost, fetch_reddit_post
from .spotify import fetch_track, find_audio_source
from .x_card import create_card, fetch_post
from .youtube_view import YoutubeChoiceView
from .youtube import download_youtube

LOG = logging.getLogger(__name__)
COLORS = {"instagram": 0xC13584, "facebook": 0x1877F2, "reddit": 0xFF4500,
          "tiktok": 0x25F4EE, "youtube": 0xFF0033, "x": 0x20262E,
          "threads": 0x20262E, "douyin": 0x25F4EE, "weibo": 0xE6162D,
          "streamable": 0x0F8FFA}


def source_button(url: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Kaynak", emoji="🔗", style=discord.ButtonStyle.link, url=url))
    return view


def media_gallery_view(platform: str, files: list[Path], caption: str | None,
                       source_url: str, *, part: int = 1,
                       show_missing_caption: bool = True) -> discord.ui.LayoutView:
    label = {"instagram": "Instagram", "facebook": "Facebook", "reddit": "Reddit",
             "tiktok": "TikTok", "youtube": "YouTube", "x": "X", "threads": "Threads",
             "douyin": "Douyin", "weibo": "Weibo", "streamable": "Streamable"}[platform]
    heading = f"### {label} gönderisi" if part == 1 else f"### {label} gönderisi · {part}. bölüm"
    children: list[discord.ui.Item] = [discord.ui.TextDisplay(heading)]
    visual = [path for path in files if path.suffix.lower() != ".mp3"]
    audio = [path for path in files if path.suffix.lower() == ".mp3"]
    if visual:
        children.append(discord.ui.MediaGallery(*(
            discord.MediaGalleryItem(f"attachment://{path.name}") for path in visual
        )))
    for path in audio:
        children.append(discord.ui.File(f"attachment://{path.name}"))
    if caption:
        children.append(discord.ui.Separator())
        partial = platform == "facebook" and caption.rstrip().endswith(("...", "…"))
        title = "Açıklama önizlemesi" if partial else "Açıklama"
        children.append(discord.ui.TextDisplay(f"**{title}**\n{caption}"))
    elif part == 1 and show_missing_caption:
        children.append(discord.ui.TextDisplay("Gönderi açıklamasına erişilemiyor."))
    children.append(discord.ui.ActionRow(
        discord.ui.Button(label="Kaynak", emoji="🔗", style=discord.ButtonStyle.link, url=source_url)
    ))
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
        self.cookie_file = cookie_file
        self.cobalt_url = cobalt_url
        self.cobalt_key = cobalt_key
        self.gallery_rs_path = gallery_rs_path
        self.adult_quota = AdultQuota(database_path or Path(__file__).resolve().parent.parent / "data" / "bot.sqlite3")

    async def on_ready(self) -> None:
        LOG.info("Hazır: %s (%s)", self.user, self.user.id if self.user else "?")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild or (self.channel_ids and message.channel.id not in self.channel_ids):
            return
        links = find_links(message.content, self.max_links)
        if not links:
            return
        async with self.jobs:
            for link in links:
                try:
                    await self._handle_link(message, link)
                except discord.Forbidden:
                    LOG.warning("Kanal için mesaj veya dosya gönderme izni yok: %s", message.channel.id)
                    return
                except discord.HTTPException as exc:
                    LOG.warning("Discord upload error: %s", exc)
                    await self._reply(message, "Dosya Discord'a yüklenemedi. Kanal izinlerini ve yükleme sınırını kontrol edin.")
                except Exception:
                    LOG.exception("Unexpected error processing %s", link.platform)
                    await self._reply(message, "Bu bağlantı işlenirken beklenmeyen bir hata oluştu.")

    async def _reply(self, message: discord.Message, content: str = "", files: list[Path] | None = None,
                     *, embed: discord.Embed | None = None, view: discord.ui.View | None = None) -> None:
        with ExitStack() as stack:
            attachments = []
            for path in files or []:
                attachment = discord.File(path, filename=path.name)
                stack.callback(attachment.close)
                attachments.append(attachment)
            await message.reply(content=content[:1900] or None, files=attachments, embed=embed, view=view,
                                mention_author=False, suppress_embeds=embed is None)

    async def _handle_link(self, message: discord.Message, link: PostLink) -> None:
        if link.platform == "spotify":
            await self._handle_spotify(message, link)
            return
        if link.platform == "reddit":
            await self._handle_reddit(message, link)
            return
        if link.platform == "youtube":
            view = YoutubeChoiceView(bot=self, source_message=message, link=link, title="YouTube videosu")
            view.prompt_message = await message.reply(view=view, mention_author=False, suppress_embeds=True)
            return
        if link.platform == "x":
            await self._handle_x(message, link)
            return
        caption_task = asyncio.create_task(fetch_caption(link, self.cookie_file))
        with tempfile.TemporaryDirectory(prefix="discord-media-") as tmp:
            folder = Path(tmp)
            media: list[Path] = []
            error: DownloadError | None = None
            try:
                media, _ = await download_media(link, folder, self.upload_bytes, self.cookie_file, self.cobalt_url, self.cobalt_key, self.gallery_rs_path)
            except DownloadError as exc:
                error = exc
            if not media:
                caption = await caption_task
                preview = await self._reddit_message_preview(message, link) if link.platform == "reddit" else Preview()
                if not preview.has_content:
                    preview = await fetch_preview(link)
                if caption or preview.has_content:
                    for embed in preview_embeds(link, preview, caption):
                        await self._reply(message, embed=embed, view=source_button(link.url))
                else:
                    await self._reply(message, f"{link.platform.upper()}: {error or 'Medya bulunamadı.'}")
                return
            try:
                caption = await caption_task
                chunks = display_chunks(caption)
                for index, chunk in enumerate(attachment_batches(media)):
                    view = media_gallery_view(link.platform, chunk,
                                              chunks[0] if index == 0 and chunks else None,
                                              link.url, part=index + 1)
                    await self._reply(message, files=chunk, view=view)
                for index, text_chunk in enumerate(chunks[1:], 2):
                    await self._reply(message, view=media_gallery_view(link.platform, [], text_chunk,
                                                                       link.url, part=index))
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

    async def _handle_reddit(self, message: discord.Message, link: PostLink) -> None:
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
            return
        if adult:
            if not channel_is_nsfw(message.channel):
                await self._reply(message, "+18 Reddit gönderileri yalnızca NSFW olarak işaretlenmiş kanallarda kullanılabilir.")
                return
            day = istanbul_day()
            if not await self.adult_quota.aclaim(message.author.id, day):
                await self._reply(message, "Günlük +18 indirme hakkını kullandın. Yeni hak Türkiye saatiyle gece yarısında açılır.")
                return
            await MediaBot._deliver_reddit(self, message, link, post, preview, title_hint)
            return
        await MediaBot._deliver_reddit(self, message, link, post, preview, title_hint)

    async def _deliver_reddit(self, message: discord.Message, link: PostLink,
                              post: RedditPost | None, preview: Preview,
                              title_hint: str | None) -> None:
        source_url = post.url if post else link.url
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
            else:
                await self._reply(message, "Reddit gönderi bilgisi alınamadı.", view=source_button(link.url))
            if post and post.is_self is True:
                return
            try:
                media_link = parse_link(source_url) or link
                media, _ = await download_media(
                    media_link, Path(tmp), self.upload_bytes, self.cookie_file,
                    self.cobalt_url, self.cobalt_key, self.gallery_rs_path,
                )
            except DownloadError:
                return
            for index, batch in enumerate(attachment_batches(media), 1):
                await self._reply(message, files=batch,
                                  view=media_gallery_view("reddit", batch, None, source_url,
                                                          part=index, show_missing_caption=False))

    async def _handle_spotify(self, message: discord.Message, link: PostLink) -> None:
        try:
            track = await fetch_track(link)
            source = await find_audio_source(track)
            with tempfile.TemporaryDirectory(prefix="discord-spotify-") as tmp:
                file, provider = await download_youtube(
                    source, Path(tmp), "mp3", self.upload_bytes,
                    self.cookie_file, self.cobalt_url, self.cobalt_key,
                )
                safe_title = re.sub(r"[^\w .-]", "", track.title, flags=re.UNICODE).strip()[:90] or "parca"
                output = Path(tmp) / f"{safe_title}.mp3"
                file.rename(output)
                await self._reply(message, files=[output])
                LOG.info("Spotify track delivered as MP3 via %s", provider)
        except DownloadError as exc:
            await self._reply(message, f"Spotify parçası indirilemedi: {exc}", view=source_button(link.url))

    async def _handle_x(self, message: discord.Message, link: PostLink) -> None:
        with tempfile.TemporaryDirectory(prefix="discord-x-") as tmp:
            folder = Path(tmp)
            post = await fetch_post(link.post_id or "")
            media_task = None
            if post is None or _has_media(post):
                media_task = asyncio.create_task(download_media(
                    link, folder / "media", self.upload_bytes, self.cookie_file,
                    self.cobalt_url, self.cobalt_key, self.gallery_rs_path,
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
                        await self._send_x_fallback(message, link, post, "Bu gönderide indirilecek medya yok.")
                    return

                try:
                    media, _ = await media_task
                except DownloadError as exc:
                    if not card_sent:
                        await self._send_x_fallback(message, link, post, str(exc))
                    return
                for index, chunk in enumerate(attachment_batches(media)):
                    content = "**X medyası**" if index == 0 else "**X medyası** · devam"
                    await self._reply(message, content, chunk, view=source_button(link.url))
            finally:
                if media_task is not None:
                    if not media_task.done():
                        media_task.cancel()
                    await asyncio.gather(media_task, return_exceptions=True)

    async def _send_x_fallback(self, message: discord.Message, link: PostLink,
                               post: dict | None, error: str) -> None:
        preview = (Preview(description=post.get("text")) if post else await fetch_preview(link))
        if preview.has_content:
            for embed in preview_embeds(link, preview):
                await self._reply(message, embed=embed, view=source_button(link.url))
        else:
            await self._reply(message, f"X: {error}")


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
        bot = MediaBot(
            channel_ids=channels,
            upload_bytes=_positive_int("MAX_UPLOAD_MB", 20, 500) * 1024 * 1024,
            max_links=_positive_int("MAX_LINKS_PER_MESSAGE", 3, 10),
            concurrency=_positive_int("MAX_CONCURRENT_JOBS", 2, 10),
            cookie_file=cookie_file,
            cobalt_url=cobalt_url,
            cobalt_key=os.getenv("COBALT_API_KEY", "").strip() or None,
            gallery_rs_path=gallery_rs_path,
            database_path=Path(os.getenv("DATABASE_PATH", "data/bot.sqlite3")).resolve(),
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
