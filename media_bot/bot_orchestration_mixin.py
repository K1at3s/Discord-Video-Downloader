"""Message orchestration and reply helpers for MediaBot."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import ExitStack
from pathlib import Path

import discord

from . import i18n
from .bot_runtime import _upload_too_large
from .cancel_view import CancelView
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
        parts = content.split(maxsplit=1)
        command = parts[0] if parts else ""
        argument = raw_content.split(maxsplit=1)[1] if len(raw_content.split(maxsplit=1)) > 1 else ""
        owner_id = getattr(self, "owner_id", 0)
        cooldowns = getattr(self, "command_cooldowns", None)
        if (cooldowns is not None and raw_content.startswith("!")
                and message.author.id != owner_id
                and not cooldowns.allow(message.author.id)):
            return
        if command in {"!yardim", "!yardım", "!help", "!komutlar"}:
            await self._reply(message, i18n.help_text(i18n.locale_for_message(message)))
            return
        if content in {"!metrics", "!botmetrics", "!durum", "!botdurum"}:
            await self._reply_metrics(message)
            return
        if command in {"!stats", "!istatistik", "!log", "!logs", "!islem", "!işlem",
                       "!panel", "!admin", "!kontrol", "!kontrolpanel",
                       "!platform", "!platformdetay", "!detay"}:
            if message.author.id != owner_id:
                await self._reply(message, "Bu komut yalnızca bot sahibine açık.")
                return
            await self._reply_request_stats(message, argument)
            return
        if command in {"!logtemizle", "!logclear"}:
            if message.author.id != owner_id:
                await self._reply(message, "Bu komut yalnızca bot sahibine açık.")
                return
            await self._reply_log_clear(message, argument)
            return
        if command in {"!logsil", "!logdelete"}:
            await self._reply_log_delete_user(message, argument)
            return
        if command in {"!hatalar", "!hataları", "!errors"}:
            if message.author.id != owner_id:
                await self._reply(message, "Bu komut yalnızca bot sahibine açık.")
                return
            await self._reply_errors(message, argument)
            return
        if command in {"!cache", "!önbellek", "!onbellek"}:
            await self._reply_cache(message)
            return
        if command in {"!cachetemizle", "!önbellektemizle", "!onbellektemizle"}:
            if message.author.id != owner_id:
                await self._reply(message, "Bu komut yalnızca bot sahibine açık.")
                return
            await self._reply_cache_clear(message)
            return
        if command in {"!info", "!profil", "!me", "!kimlik"}:
            await self._reply_user_info(message, argument)
            return
        if command in {"!bakim", "!maintenance"}:
            if message.author.id != owner_id:
                await self._reply(message, "Bu komut yalnızca bot sahibine açık.")
                return
            await self._reply_maintenance(message, argument)
            return
        if command in {"!ayar", "!ayarlar", "!settings"}:
            await self._reply_settings(message, argument)
            return
        B = __import__("media_bot.bot", fromlist=[""])
        links = B.find_links(message.content, self.max_links)
        if not links:
            return
        if MAINTENANCE.active and message.author.id != owner_id:
            await self._reply(message, MAINTENANCE.message)
            return
        locale = B.i18n.locale_for_message(message)
        auto_thread = getattr(self, "_auto_thread_message", None)
        if auto_thread:
            message = await auto_thread(message, links)
        status_notice: discord.Message | None = None
        cancel_view = None
        cancelled = False

        async def on_wait() -> None:
            nonlocal status_notice
            text = B.i18n.t("queued", locale)
            try:
                if status_notice is None:
                    status_notice = await self._reply(message, text)
                else:
                    await status_notice.edit(content=text)
            except (discord.HTTPException, AttributeError, TypeError):
                pass

        try:
            async with self.job_slots.slot(message.author.id, on_wait):
                if status_notice is None:
                    try:
                        status_notice = await self._reply(message, B.i18n.t("preparing", locale))
                    except discord.HTTPException:
                        status_notice = None
                elif getattr(status_notice, "edit", None) is not None:
                    try:
                        await status_notice.edit(content=B.i18n.t("preparing", locale))
                    except (discord.HTTPException, AttributeError, TypeError):
                        pass
                cancel_view = await OrchestrationMixin._attach_cancel_view(
                    self, status_notice, message, locale
                )

                async def process_link(link: PostLink, status_message: discord.Message | None) -> None:
                    nonlocal cancelled
                    outcome = "failure"
                    detail = ""
                    files: list[Path] = []
                    pending_target = None
                    precomputed_bytes: int | None = None
                    started = time.monotonic()
                    guild_settings = SETTINGS.get(getattr(message.guild, "id", 0))
                    if not guild_settings.platform_enabled(link.platform):
                        await self._reply(
                            message,
                            i18n.t("platform_disabled", locale, platform=link.platform.upper()),
                        )
                        REQUESTS.record(
                            guild_id=getattr(message.guild, "id", None),
                            guild_name=getattr(message.guild, "name", None),
                            channel_id=getattr(message.channel, "id", None),
                            user_id=getattr(message.author, "id", None),
                            user_name=str(getattr(message.author, "name", message.author)),
                            platform=link.platform,
                            url=link.url,
                            outcome="skipped",
                            detail="platform_disabled",
                            elapsed_ms=int((time.monotonic() - started) * 1000),
                        )
                        return
                    quota = getattr(self, "user_quota", None)
                    if quota is not None and message.author.id != owner_id:
                        try:
                            decision = await quota.aclaim(message.author.id)
                        except Exception:
                            LOG.exception("User quota check failed")
                        else:
                            if not decision.allowed:
                                await self._reply(
                                    message, B.i18n.quota_message(decision, locale)
                                )
                                REQUESTS.record(
                                    guild_id=getattr(message.guild, "id", None),
                                    guild_name=getattr(message.guild, "name", None),
                                    channel_id=getattr(message.channel, "id", None),
                                    user_id=getattr(message.author, "id", None),
                                    user_name=str(getattr(message.author, "name", message.author)),
                                    platform=link.platform,
                                    url=link.url,
                                    outcome="skipped",
                                    detail="quota",
                                    elapsed_ms=int((time.monotonic() - started) * 1000),
                                )
                                return
                    guild_quota = getattr(self, "guild_quota", None)
                    guild_id = getattr(message.guild, "id", None)
                    if guild_quota is not None and guild_id is not None and message.author.id != owner_id:
                        try:
                            decision = await guild_quota.aclaim(guild_id)
                        except Exception:
                            LOG.exception("Guild quota check failed")
                        else:
                            if not decision.allowed:
                                await self._reply(
                                    message, i18n.quota_message(decision, locale, scope="guild")
                                )
                                REQUESTS.record(
                                    guild_id=guild_id,
                                    guild_name=getattr(message.guild, "name", None),
                                    channel_id=getattr(message.channel, "id", None),
                                    user_id=getattr(message.author, "id", None),
                                    user_name=str(getattr(message.author, "name", message.author)),
                                    platform=link.platform,
                                    url=link.url,
                                    outcome="skipped",
                                    detail="guild_quota",
                                    elapsed_ms=int((time.monotonic() - started) * 1000),
                                )
                                return
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
                    except asyncio.CancelledError:
                        outcome = "skipped"
                        detail = "cancelled"
                        cancelled = True
                        raise
                    except discord.Forbidden:
                        LOG.warning("Kanal için mesaj veya dosya gönderme izni yok: %s", message.channel.id)
                        outcome = "failure"
                        detail = "forbidden"
                        await self._reply(message, B.i18n.t("error_forbidden", locale))
                        return
                    except discord.HTTPException as exc:
                        LOG.warning("Discord upload error: %s", exc)
                        text = str(exc).lower()
                        if exc.status == 413 or "entity too large" in text or "larger than" in text or "40005" in text:
                            await self._reply(message, B.i18n.t("discord_413", locale))
                        else:
                            await self._reply(message, B.i18n.t("discord_upload_failed", locale))
                        outcome = "failure"
                        detail = f"http:{exc.status}"
                    except Exception:
                        LOG.exception("Unexpected error processing %s", link.platform)
                        await self._reply(message, B.i18n.t("unexpected_error", locale))
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
                    if outcome == "failure" and detail not in {"forbidden", "cancelled"}:
                        try:
                            await self._reply(
                                message,
                                B.i18n.t("retry_hint", locale),
                                view=RetryView(bot=self, source_message=message, link=link),
                            )
                        except discord.HTTPException:
                            pass

                if len(links) == 1:
                    await process_link(links[0], status_notice)
                else:
                    # Multiple links used to run one after another. Download two
                    # at a time and let each upload as soon as it is ready; keep
                    # the shared status message static to avoid edit races.
                    if getattr(status_notice, "edit", None) is not None:
                        try:
                            await status_notice.edit(content=B.i18n.t(
                                "parallel_processing", locale, count=len(links)
                            ))
                        except (discord.HTTPException, AttributeError, TypeError):
                            pass
                    link_slots = asyncio.Semaphore(2)

                    async def run_link(link: PostLink) -> None:
                        async with link_slots:
                            await process_link(link, None)

                    await asyncio.gather(*(run_link(link) for link in links))
        finally:
            if status_notice is not None and not (cancelled or (cancel_view and cancel_view.cancelled)):
                delete = getattr(status_notice, "delete", None)
                if delete is not None:
                    try:
                        await delete()
                    except (discord.HTTPException, AttributeError, TypeError):
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

    async def _attach_cancel_view(self, status_message, message, locale: str = "tr"):
        """Attach a cancel button that stops this message's processing task."""
        if status_message is None:
            return None
        edit = getattr(status_message, "edit", None)
        task = asyncio.current_task()
        if edit is None or task is None:
            return None
        view = CancelView(
            task=task,
            owner_id=getattr(getattr(message, "author", None), "id", None),
            bot_owner_id=getattr(self, "owner_id", None),
            locale=locale,
        )
        try:
            await edit(view=view)
        except (discord.HTTPException, AttributeError, TypeError):
            return None
        return view
