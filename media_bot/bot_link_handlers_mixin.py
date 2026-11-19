"""Extracted link/media handler methods for MediaBot."""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import discord
import httpx

from . import i18n
from .adult import channel_is_nsfw, istanbul_day
from .discord_support import ThreadMessage, VIDEO_SUFFIXES
from .download import DownloadError
from .errors import user_message
from .guild_settings import QUALITY_CHOICES, SETTINGS
from .links import PostLink
from .limits import attachment_batches
from .metadata_fallback import Preview
from .progress import ProgressReporter
from .reddit_post import RedditPost
from .youtube_view import YoutubeChoiceView

LOG = logging.getLogger("media_bot.bot")


class LinkHandlersMixin:
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

    async def _fit_attachments(self, files: list[Path], message: object) -> list[Path] | None:
        B = __import__("media_bot.bot", fromlist=[""])
        """Reencode oversized videos after a 413 so one retry can succeed."""
        limit = B.upload_limit(message, self.upload_bytes)
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
            if await B.fit_video(path, target, budget, force=True, max_height=480):
                reduced.append(target)
                changed = True
            else:
                reduced.append(path)
        return reduced if changed else None

    async def _handle_link(self, message: discord.Message, link: PostLink, status_message: discord.Message | None = None) -> str | tuple[str, list[Path]] | tuple[str, list[Path], YoutubeChoiceView]:
        B = __import__("media_bot.bot", fromlist=[""])
        locale = i18n.locale_for_message(message)

        async def set_status(text: str) -> None:
            if not status_message:
                return
            try:
                await status_message.edit(content=text)
            except (discord.HTTPException, AttributeError, TypeError):
                pass

        if link.platform == "threads":
            await set_status(i18n.t("resolving", locale))
            link = await B.resolve_threads_link(link, self.cookie_file) or link
            if highlight := B.highlight_post_link(link.url):
                # Threads-only short links like /s/highlight:<ig_id> point at an
                # Instagram highlight album, so hand off to the Instagram flow.
                link = highlight
        if link.platform == "spotify":
            await set_status(i18n.t("preparing", locale))
            return await self._handle_spotify(message, link, status_message=status_message)
        if link.platform == "bluesky":
            await set_status(i18n.t("preparing", locale))
            return await self._handle_bluesky(message, link, status_message=status_message)
        if link.platform == "reddit":
            await set_status(i18n.t("preparing", locale))
            return await self._handle_reddit(message, link, status_message=status_message)
        if link.platform == "youtube":
            limit = B.upload_limit(message, self.upload_bytes)
            quality = self._youtube_quality(message)
            if quality == "sor":
                view = B.YoutubeChoiceView(bot=self, source_message=message, link=link,
                                         title=i18n.t("youtube_title", locale),
                                         upload_limit=limit)
                view.prompt_message = await message.reply(view=view, mention_author=False, suppress_embeds=True)
                return "pending", [], view
            started = time.monotonic()
            status = None
            try:
                status = await message.reply(
                    view=B.status_view(
                        i18n.t("youtube_title", locale),
                        i18n.t("youtube_preparing", locale, quality=quality),
                    ),
                    mention_author=False, suppress_embeds=True,
                )
            except discord.HTTPException as exc:
                LOG.warning("YouTube durum kartı gönderilemedi: %s", exc)
            ok, files, detail = await B.deliver_youtube(
                self, message, link, quality, limit, started=started,
            )
            if status:
                text = (i18n.t("youtube_sent", locale, detail=detail)
                        if ok else f"❌ {detail[:300]}")
                try:
                    await status.edit(view=B.status_view(
                        i18n.t("youtube_title", locale), text
                    ))
                except discord.HTTPException as exc:
                    LOG.warning("YouTube durum kartı güncellenemedi: %s", exc)
            return ("success" if ok else "failure"), files
        if link.platform == "x":
            await set_status(i18n.t("preparing", locale))
            return await self._handle_x(message, link, status_message=status_message)
        caption_task = asyncio.create_task(B.fetch_caption(link, self.cookie_file))

        with tempfile.TemporaryDirectory(prefix="discord-media-") as tmp:
            folder = Path(tmp)
            media: list[Path] = []
            error: DownloadError | None = None
            user_id = getattr(getattr(message, "author", None), "id", None)
            await set_status(i18n.t("downloading", locale))
            limit_bytes = B.upload_limit(message, self.upload_bytes)
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
                if caption_resolved and caption:
                    return
                if not caption:
                    info_caption = B._extract_download_caption(folder)
                    if info_caption:
                        caption = info_caption
                        caption_resolved = True
                        caption_pending = False
                        chunks = B.display_chunks(caption)
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
                chunks = B.display_chunks(caption)

            def _track_caption_patch_task(task: asyncio.Task) -> None:
                try:
                    task.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    LOG.exception("Caption patch task failed for %s", link.platform)

            async def maybe_patch_caption() -> None:
                nonlocal caption, caption_resolved, caption_pending, chunks, caption_included
                if first_media_message is None or caption_included:
                    return
                if not caption_task.done():
                    try:
                        caption = await asyncio.wait_for(asyncio.shield(caption_task), timeout=10.0)
                    except Exception:
                        caption = None
                elif not caption:
                    try:
                        caption = await caption_task
                    except Exception:
                        caption = None

                if not caption:
                    caption = B._extract_download_caption(folder)

                if not caption:
                    try:
                        await asyncio.sleep(1.5)
                        caption = await B.fetch_caption(link, self.cookie_file)
                    except Exception:
                        caption = None
                caption_resolved = True
                caption_pending = False
                chunks = B.display_chunks(caption)
                if not caption or not chunks:
                    try:
                        await first_media_message.edit(view=B.media_gallery_view(
                            link.platform, first_media_batch, None, link.url, part=1,
                            show_missing_caption=True,
                        ))
                        LOG.info("Caption remained unavailable for %s; updated first message", link.platform)
                    except discord.HTTPException as exc:
                        LOG.warning("Caption placeholder edit failed for %s: %s", link.platform, exc)
                    return
                try:
                    await first_media_message.edit(view=B.media_gallery_view(
                        link.platform, first_media_batch, chunks[0], link.url, part=1,
                        show_missing_caption=False,
                    ))
                    caption_included = True
                    LOG.info("Caption patched into first message for %s", link.platform)
                except discord.HTTPException as exc:
                    LOG.warning("Caption edit failed for %s: %s", link.platform, exc)
                    return
                for index, text_chunk in enumerate(chunks[1:], batches_sent + 1):
                    try:
                        await self._reply(message, view=B.media_gallery_view(
                            link.platform, [], text_chunk, link.url, part=index,
                            show_missing_caption=False,
                        ), as_reply=False)
                    except discord.HTTPException as exc:
                        LOG.warning("Caption continuation send failed for %s: %s", link.platform, exc)
                        break

            def schedule_caption_patch() -> None:
                nonlocal caption_patch_task
                if caption_patch_task or first_media_message is None or caption_included:
                    return
                caption_patch_task = asyncio.create_task(maybe_patch_caption())
                if hasattr(self, "_background_tasks"):
                    self._background_tasks.add(caption_patch_task)
                    caption_patch_task.add_done_callback(self._background_tasks.discard)
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
                    await set_status(i18n.t(
                        "progress_uploading_group", locale,
                        count=batches_sent, files=len(sent_files) + len(batch),
                    ))
                view = B.media_gallery_view(
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
                    first_media_batch = [Path(p.name) for p in batch]
                    caption_included = bool(caption_resolved and caption)
                    if not caption_included:
                        schedule_caption_patch()

            async def on_stream_file(paths: list[Path]) -> None:
                # Upload streamed files promptly (progressive delivery).
                for batch in attachment_batches(paths):
                    await send_paths(batch)

            try:
                try:
                    media, _ = await B.download_media(
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
                        preview = await B.fetch_preview(link)
                    if caption or preview.has_content:
                        for embed in B.preview_embeds(link, preview, caption):
                            await self._reply(message, embed=embed, view=B.source_button(link.url))
                        return "success", [], None, 0
                    else:
                        if link.platform == "threads" and re.search(r"/(?:share|t|s)/", link.url):
                            await self._reply(message, i18n.t("threads_unresolved", locale),
                                              view=B.source_button(link.url))
                        else:
                            await self._reply(
                                message,
                                user_message(str(error) if error else "no media", locale,
                                             platform=link.platform.upper()),
                            )
                        return "failure", [], None, 0

                if (media or sent_files) and not caption:
                    download_caption = B._extract_download_caption(folder)
                    if download_caption:
                        caption = download_caption
                        caption_resolved = True
                        caption_pending = False
                        chunks = B.display_chunks(caption)

                # Flush any files from the authoritative result that were not streamed/sent.
                remaining = [p for p in media if p not in uploaded]
                for batch in attachment_batches(remaining):
                    await send_paths(batch)

                if first_media_message and not caption_included:
                    if not caption_resolved:
                        await resolve_caption(wait=False)
                    if caption_resolved and chunks:
                        try:
                            await first_media_message.edit(view=B.media_gallery_view(
                                link.platform, first_media_batch, chunks[0], link.url, part=1,
                                show_missing_caption=False,
                            ))
                            caption_included = True
                            for index, text_chunk in enumerate(chunks[1:], batches_sent + 1):
                                await self._reply(message, view=B.media_gallery_view(
                                    link.platform, [], text_chunk, link.url, part=index,
                                    show_missing_caption=False,
                                ), as_reply=False)
                        except discord.HTTPException as exc:
                            LOG.warning("Caption edit failed for %s: %s", link.platform, exc)
                    else:
                        schedule_caption_patch()
                elif caption_resolved and chunks and caption_included:
                    for index, text_chunk in enumerate(chunks[1:], batches_sent + 1):
                        await self._reply(message, view=B.media_gallery_view(
                            link.platform, [], text_chunk, link.url, part=index,
                            show_missing_caption=False,
                        ), as_reply=False)

                if status_message and (sent_files or media):
                    await set_status(i18n.t("files_sent", locale,
                                            count=len(sent_files) or len(media)))
                total_bytes = sum(p.stat().st_size for p in media if p.is_file())
                return "success", media, None, total_bytes
            finally:
                if caption_patch_task is None:
                    if not caption_task.done():
                        caption_task.cancel()
                    await asyncio.gather(caption_task, return_exceptions=True)

    async def _reddit_message_preview(self, message: discord.Message, link: PostLink) -> Preview:
        B = __import__("media_bot.bot", fromlist=[""])
        preview = B.reddit_embed_preview(link, getattr(message, "embeds", []))
        if preview.has_content:
            return preview
        for _ in range(3):
            await asyncio.sleep(0.5)
            try:
                refreshed = await message.channel.fetch_message(message.id)
            except (discord.HTTPException, AttributeError):
                return Preview()
            preview = B.reddit_embed_preview(link, getattr(refreshed, "embeds", []))
            if preview.has_content:
                return preview
        return Preview()

    async def _handle_bluesky(self, message: discord.Message, link: PostLink, status_message: discord.Message | None = None) -> str | tuple[str, list[Path]]:
        B = __import__("media_bot.bot", fromlist=[""])
        locale = i18n.locale_for_message(message)

        async def set_status(text: str) -> None:
            if not status_message:
                return
            try:
                await status_message.edit(content=text)
            except (discord.HTTPException, AttributeError, TypeError):
                pass

        try:
            post = await B.fetch_bluesky_post(link)
        except httpx.HTTPError as exc:
            LOG.warning("Bluesky API unavailable: %s", exc)
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            key = ("bluesky_rate_limit" if status == 429 else
                   "bluesky_not_found" if status in {400, 401, 403, 404} else
                   "bluesky_unavailable")
            await self._reply(message, i18n.t(key, locale), view=B.source_button(link.url))
            return "failure", []
        if post is None:
            await self._reply(message, i18n.t("bluesky_not_found", locale),
                              view=B.source_button(link.url))
            return "failure", []
        if post.adult:
            if not channel_is_nsfw(message.channel):
                await self._reply(message, i18n.t("adult_channel_only", locale))
                return "failure", []
            if not await self.adult_quota.aclaim(message.author.id, istanbul_day()):
                await self._reply(message, i18n.t("adult_daily_limit", locale))
                return "failure", []
        with tempfile.TemporaryDirectory(prefix="discord-bluesky-") as tmp:
            await set_status(i18n.t("downloading", locale))
            try:
                media = await B.download_bluesky_media(post, Path(tmp), B.upload_limit(message, self.upload_bytes))
            except OSError as exc:
                LOG.warning("Bluesky media unavailable: %s", exc)
                media = []
            caption = post.caption
            if post.media and not media:
                caption += "\n\n" + i18n.t("media_not_downloaded", locale)
            chunks = B.display_chunks(caption) or [""]
            batches = list(attachment_batches(media)) or [[]]
            total = len([b for b in batches if b])
            sent = 0
            for index, batch in enumerate(batches):
                if batch and status_message and total > 1:
                    sent += 1
                    await set_status(i18n.t("progress_uploading_part", locale,
                                            part=sent, total=total))
                elif batch and status_message:
                    await set_status(i18n.t("uploading", locale))
                await self._reply(message, files=batch,
                                  view=B.media_gallery_view("bluesky", batch,
                                                          chunks[0] if index == 0 else None,
                                                          link.url, part=index + 1,
                                                          show_missing_caption=False,
                                                          descriptions=list(post.alt_texts[index * 10:(index + 1) * 10])))
            for index, chunk in enumerate(chunks[1:], len(batches) + 1):
                await self._reply(message, view=B.media_gallery_view("bluesky", [], chunk,
                                                                   link.url, part=index,
                                                                   show_missing_caption=False))
            if status_message and batch:
                await set_status(i18n.t("files_sent", locale, count=len(media)))
            return "success", media

    async def _handle_reddit(self, message: discord.Message, link: PostLink, status_message: discord.Message | None = None) -> str | tuple[str, list[Path]]:
        B = __import__("media_bot.bot", fromlist=[""])
        locale = i18n.locale_for_message(message)
        preview = (await self._reddit_message_preview(message, link)
                   if not link.post_id else Preview())
        subreddit_prefix = re.match(r"^r/[^ ]+ · ", preview.title or "")
        title_hint = (preview.title or "")[subreddit_prefix.end():] if subreddit_prefix else None
        try:
            post = await asyncio.wait_for(B.fetch_reddit_post(link, title_hint), timeout=6)
        except TimeoutError:
            post = None
        adult = await B.reddit_adult_status(post)
        if adult is None:
            await self._reply(message, i18n.t("adult_unverified", locale))
            return "failure", []
        if adult:
            if not channel_is_nsfw(message.channel):
                await self._reply(message, i18n.t("adult_channel_only", locale))
                return "failure", []
            day = istanbul_day()
            if not await self.adult_quota.aclaim(message.author.id, day):
                await self._reply(message, i18n.t("adult_daily_limit", locale))
                return "failure", []
        return await B.MediaBot._deliver_reddit(
            self, message, link, post, preview, title_hint,
            status_message=status_message,
        )

    async def _deliver_reddit(self, message: discord.Message, link: PostLink,
                              post: RedditPost | None, preview: Preview,
                              title_hint: str | None, status_message: discord.Message | None = None) -> tuple[str, list[Path]]:
        B = __import__("media_bot.bot", fromlist=[""])
        locale = i18n.locale_for_message(message)
        source_url = post.url if post else link.url
        media_files: list[Path] = []
        progress = ProgressReporter(status_message, locale)
        with tempfile.TemporaryDirectory(prefix="discord-reddit-") as tmp:
            if not post and not preview.has_content:
                preview = await self._reddit_message_preview(message, link)
                if not preview.has_content:
                    preview = await B.fetch_preview(link)
            display_post = post
            if not display_post and preview.has_content:
                subreddit = re.search(r"/r/([^/]+)", urlsplit(link.url).path, re.IGNORECASE)
                preview_title = preview.title or "Reddit"
                if " · " in preview_title and preview_title.startswith("r/"):
                    preview_title = preview_title.split(" · ", 1)[1]
                display_post = RedditPost(
                    title_hint or preview_title, preview.description or "",
                    subreddit.group(1) if subreddit else "reddit", "", link.url,
                )
            if display_post:
                bot_user = getattr(self, "user", None)
                footer = f"@{bot_user.name}" if bot_user else "Discord Video Bot"
                variant = f"v1|{'preview' if post is None else 'full'}|{footer}"
                cache_key = post.url if post else link.url
                cards: list[Path] = []
                cached = await asyncio.to_thread(B.CARD_CACHE.get, "reddit", cache_key, variant)
                if cached is not None:
                    cards = await asyncio.to_thread(B.CARD_CACHE.copy_to, cached, Path(tmp) / "cards")
                if not cards:
                    cards = await asyncio.to_thread(
                        B.render_reddit_cards, display_post, Path(tmp) / "cards",
                        footer, preview_only=post is None,
                    )
                    if cards:
                        await asyncio.to_thread(
                            B.CARD_CACHE.put, "reddit", cache_key, cards, variant
                        )
                for index, batch in enumerate(attachment_batches(cards), 1):
                    await self._reply(
                        message, files=batch,
                        view=B.media_gallery_view("reddit", batch, None, source_url,
                                                part=index, show_missing_caption=False),
                    )
                delivered_any = True
            else:
                await self._reply(message, i18n.t("reddit_no_info", locale),
                                  view=B.source_button(link.url))
                delivered_any = False
            if post and post.is_self is True:
                return ("success" if delivered_any else "failure"), media_files
            await progress.downloading(force=True)
            try:
                media_link = B.parse_link(source_url) or link
                user_id = getattr(getattr(message, "author", None), "id", None)
                estimate = await B.estimate_reddit_media_bytes(media_link)
                await progress.set_estimate(estimate)
                media, _ = await B.download_media(
                    media_link, Path(tmp), B.upload_limit(message, self.upload_bytes), self.cookie_file,
                    self.cobalt_url, self.cobalt_key, self.gallery_rs_path,
                    user_id=user_id, on_file=progress.on_file,
                )
            except DownloadError:
                return ("success" if delivered_any else "failure"), media_files
            media_files.extend(media)
            total = len([batch for batch in attachment_batches(media)])
            for index, batch in enumerate(attachment_batches(media), 1):
                await progress.uploading(part=index, total=total, force=True)
                await self._reply(message, files=batch,
                                  view=B.media_gallery_view("reddit", batch, None, source_url,
                                                          part=index, show_missing_caption=False))
                delivered_any = True
            if media_files:
                await progress.done(len(media_files))
            return ("success" if delivered_any else "failure"), media_files

    async def _handle_spotify(self, message: discord.Message, link: PostLink, status_message: discord.Message | None = None) -> str | tuple[str, list[Path]]:
        B = __import__("media_bot.bot", fromlist=[""])
        locale = i18n.locale_for_message(message)
        try:
            track = await B.fetch_track(link)
            source = await B.find_audio_source(track)
            with tempfile.TemporaryDirectory(prefix="discord-spotify-") as tmp:
                if status_message and getattr(status_message, "edit", None) is not None:
                    try:
                        await status_message.edit(content=i18n.t("downloading", locale))
                    except (discord.HTTPException, AttributeError, TypeError):
                        pass
                file, provider = await B.download_youtube(
                    source, Path(tmp), "mp3", B.upload_limit(message, self.upload_bytes),
                    self.cookie_file, self.cobalt_url, self.cobalt_key,
                )
                safe_title = re.sub(r"[^\w .-]", "", track.title, flags=re.UNICODE).strip()[:90] or "parca"
                output = Path(tmp) / f"{safe_title}.mp3"
                file.rename(output)
                await self._reply(message, files=[output])
                LOG.info("Spotify track delivered as MP3 via %s", provider)
                return "success", [output]
        except DownloadError as exc:
            await self._reply(message, i18n.t("spotify_failed", locale, error=str(exc)),
                              view=B.source_button(link.url))
            return "failure", []

    async def _handle_x(self, message: discord.Message, link: PostLink, status_message: discord.Message | None = None) -> str | tuple[str, list[Path]]:
        B = __import__("media_bot.bot", fromlist=[""])
        locale = i18n.locale_for_message(message)
        progress = ProgressReporter(status_message, locale)
        with tempfile.TemporaryDirectory(prefix="discord-x-") as tmp:
            folder = Path(tmp)
            limit = B.upload_limit(message, self.upload_bytes)
            post = await B.fetch_post(link.post_id or "")
            media_task = None
            if post is None or B._has_media(post):
                user_id = getattr(getattr(message, "author", None), "id", None)
                await progress.downloading(force=True)
                if post:
                    estimate = await B.estimate_urls_bytes(
                        [url for url, _kind in B.x_media_urls(post)]
                    )
                    await progress.set_estimate(estimate)
                media_task = asyncio.create_task(B.download_media(
                    link, folder / "media", limit, self.cookie_file,
                    self.cobalt_url, self.cobalt_key, self.gallery_rs_path,
                    user_id=user_id, on_file=progress.on_file,
                ))
            try:
                card_sent = False
                if post:
                    card_path = folder / "x_gonderisi.png"
                    try:
                        bot_user = getattr(self, "user", None)
                        footer = f"@{bot_user.name}" if bot_user else "Discord Video Bot"
                        variant = f"v1|{footer}"
                        cached = await asyncio.to_thread(
                            B.CARD_CACHE.get, "x", link.post_id or link.url, variant
                        )
                        if cached is not None:
                            copied = await asyncio.to_thread(
                                B.CARD_CACHE.copy_to, cached, folder / "card_cache"
                            )
                            if copied:
                                card_path = copied[0]
                        if not card_path.is_file():
                            await B.create_card(post, card_path, footer)
                            if card_path.is_file():
                                await asyncio.to_thread(
                                    B.CARD_CACHE.put, "x", link.post_id or link.url,
                                    [card_path], variant,
                                )
                    except Exception:
                        LOG.exception("Could not render X card")
                    else:
                        if card_path.is_file() and 0 < card_path.stat().st_size <= limit:
                            await self._reply(message, i18n.t("x_post", locale), [card_path],
                                              view=B.source_button(link.url))
                            card_sent = True

                if media_task is None:
                    if not card_sent:
                        ok = await self._send_x_fallback(
                            message, link, post, i18n.t("error_no_media", locale, platform="X"),
                            locale,
                        )
                        return ("success" if ok else "failure"), []
                    return "success", []

                try:
                    media, _ = await media_task
                except DownloadError as exc:
                    if not card_sent:
                        ok = await self._send_x_fallback(
                            message, link, post,
                            user_message(str(exc), locale, platform="X"), locale,
                        )
                        return ("success" if ok else "failure"), []
                    return "success", []
                batches = list(attachment_batches(media))
                for index, chunk in enumerate(batches, 1):
                    content = (i18n.t("x_media", locale) if index == 1
                               else i18n.t("x_media_more", locale))
                    await progress.uploading(part=index, total=len(batches), force=True)
                    await self._reply(message, content, chunk, view=B.source_button(link.url))
                if media:
                    await progress.done(len(media))
                return "success", media
            finally:
                if media_task is not None:
                    if not media_task.done():
                        media_task.cancel()
                    await asyncio.gather(media_task, return_exceptions=True)

    async def _send_x_fallback(self, message: discord.Message, link: PostLink,
                               post: dict | None, error: str,
                               locale: str | None = None) -> bool:
        B = __import__("media_bot.bot", fromlist=[""])
        locale = locale or i18n.locale_for_message(message)
        preview = (Preview(description=post.get("text")) if post else await B.fetch_preview(link))
        if preview.has_content:
            for embed in B.preview_embeds(link, preview):
                await self._reply(message, embed=embed, view=B.source_button(link.url))
            return True
        await self._reply(message, error)
        return False

