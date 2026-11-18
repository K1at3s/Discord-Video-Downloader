"""Message orchestration and reply helpers for MediaBot."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import ExitStack
from pathlib import Path

import discord

from .bot_runtime import _upload_too_large
from .guild_settings import SETTINGS
from .links import PostLink
from .maintenance import MAINTENANCE
from .request_log import REQUESTS
from .retry_view import RetryView

LOG = logging.getLogger("media_bot.bot")


class OrchestrationMixin:
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild or (self.channel_ids and message.channel.id not in self.channel_ids):
            return
        raw_content = (message.content or "").strip()
        content = raw_content.lower()
        owner_id = getattr(self, "owner_id", 0)
        cooldowns = getattr(self, "command_cooldowns", None)
        if (cooldowns is not None and raw_content.startswith("!")
                and message.author.id != owner_id
                and not cooldowns.allow(message.author.id)):
            return
        if content.split(maxsplit=1)[0] in {"!yardim", "!yardım", "!help", "!komutlar"}:
            help_text = __import__("media_bot.bot", fromlist=[""]).HELP_TEXT
            await self._reply(message, help_text)
            return
        if content in {"!metrics", "!botmetrics", "!durum", "!botdurum"}:
            await self._reply_metrics(message)
            return
        if content.split(maxsplit=1)[0] in {"!stats", "!istatistik", "!log", "!logs", "!islem", "!işlem",
                                            "!panel", "!admin", "!kontrol", "!kontrolpanel",
                                            "!platform", "!platformdetay", "!detay"}:
            if message.author.id != owner_id:
                await self._reply(message, "Bu komut yalnızca bot sahibine açık.")
                return
            argument = raw_content.split(maxsplit=1)[1] if len(raw_content.split(maxsplit=1)) > 1 else ""
            await self._reply_request_stats(message, argument)
            return
        if content.split(maxsplit=1)[0] in {"!logtemizle", "!logclear"}:
            if message.author.id != owner_id:
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
            if message.author.id != owner_id:
                await self._reply(message, "Bu komut yalnızca bot sahibine açık.")
                return
            argument = raw_content.split(maxsplit=1)[1] if len(raw_content.split(maxsplit=1)) > 1 else ""
            await self._reply_errors(message, argument)
            return
        if content.split(maxsplit=1)[0] in {"!cache", "!önbellek", "!onbellek"}:
            await self._reply_cache(message)
            return
        if content.split(maxsplit=1)[0] in {"!cachetemizle", "!önbellektemizle", "!onbellektemizle"}:
            if message.author.id != owner_id:
                await self._reply(message, "Bu komut yalnızca bot sahibine açık.")
                return
            await self._reply_cache_clear(message)
            return
        if content.split(maxsplit=1)[0] in {"!info", "!profil", "!me", "!kimlik"}:
            argument = raw_content.split(maxsplit=1)[1] if len(raw_content.split(maxsplit=1)) > 1 else ""
            await self._reply_user_info(message, argument)
            return
        if content.split(maxsplit=1)[0] in {"!bakim", "!maintenance"}:
            if message.author.id != owner_id:
                await self._reply(message, "Bu komut yalnızca bot sahibine açık.")
                return
            argument = raw_content.split(maxsplit=1)[1] if len(raw_content.split(maxsplit=1)) > 1 else ""
            await self._reply_maintenance(message, argument)
            return
        if content.split(maxsplit=1)[0] in {"!ayar", "!ayarlar", "!settings"}:
            argument = raw_content.split(maxsplit=1)[1] if len(raw_content.split(maxsplit=1)) > 1 else ""
            await self._reply_settings(message, argument)
            return
        B = __import__("media_bot.bot", fromlist=[""])
        links = B.find_links(message.content, self.max_links)
        if not links:
            return
        if MAINTENANCE.active and message.author.id != owner_id:
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
                     *, embed: discord.Embed | None = None, view: discord.ui.View | None = None,
                     as_reply: bool | None = None) -> discord.Message:
        try:
            return await self._reply_once(message, content, files, embed=embed, view=view, as_reply=as_reply)
        except discord.HTTPException as exc:
            if not files or not _upload_too_large(exc):
                raise
            shrunk = await self._fit_attachments(files, message)
            if not shrunk:
                raise
            LOG.info("Discord rejected an upload as too large; retrying %d file(s) smaller", len(shrunk))
            return await self._reply_once(message, content, shrunk, embed=embed, view=view, as_reply=as_reply)

    async def _reply_once(self, message: discord.Message, content: str = "", files: list[Path] | None = None,
                          *, embed: discord.Embed | None = None,
                          view: discord.ui.View | None = None,
                          as_reply: bool | None = None) -> discord.Message:
        with ExitStack() as stack:
            attachments = []
            for path in files or []:
                attachment = discord.File(path, filename=path.name)
                stack.callback(attachment.close)
                attachments.append(attachment)
            use_reply = True
            if as_reply is not None:
                use_reply = as_reply
            elif isinstance(message, discord.Message):
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
