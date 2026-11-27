import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from media_bot.bot_orchestration_mixin import OrchestrationMixin
from media_bot.links import PostLink


class FakeBot(OrchestrationMixin):
    def __init__(self):
        self.channel_ids = set()
        self.max_links = 5
        self.owner_id = 1
        self.command_cooldowns = SimpleNamespace(allow=lambda _uid: True)
        self.job_slots = SimpleNamespace(slot=self._slot)
        self._reply = AsyncMock(return_value=SimpleNamespace(edit=AsyncMock(), delete=AsyncMock()))
        self._reply_metrics = AsyncMock()
        self._reply_request_stats = AsyncMock()
        self._reply_log_clear = AsyncMock()
        self._reply_log_delete_user = AsyncMock()
        self._reply_errors = AsyncMock()
        self._reply_cache = AsyncMock()
        self._reply_cache_clear = AsyncMock()
        self._reply_user_info = AsyncMock()
        self._reply_maintenance = AsyncMock()
        self._reply_settings = AsyncMock()
        self._handle_link = AsyncMock(return_value=("success", []))
        self._fit_attachments = AsyncMock(return_value=[])

    def _slot(self, _uid, on_wait):
        class CM:
            async def __aenter__(self_inner):
                return self_inner
            async def __aexit__(self_inner, *_a):
                return False
        return CM()


def make_message(content="!yardim", author_id=2, guild_id=10, channel_id=20):
    msg = SimpleNamespace()
    msg.author = SimpleNamespace(bot=False, id=author_id, name="u")
    msg.guild = SimpleNamespace(id=guild_id, name="g")
    msg.channel = SimpleNamespace(id=channel_id, send=AsyncMock())
    msg.content = content
    msg.reply = AsyncMock(return_value=SimpleNamespace(edit=AsyncMock(), delete=AsyncMock()))
    return msg


class OrchestrationMixinTests(unittest.IsolatedAsyncioTestCase):
    async def test_commands_dispatch(self):
        bot = FakeBot()
        msg = make_message("!yardim")
        with patch("media_bot.bot_orchestration_mixin.i18n.help_text", return_value="help"), \
             patch("media_bot.bot_orchestration_mixin.i18n.locale_for_message", return_value="tr"):
            await bot.on_message(msg)
        bot._reply.assert_awaited()

        for cmd, attr in [("!metrics", "_reply_metrics"), ("!stats", "_reply_request_stats"), ("!logtemizle", "_reply_log_clear"), ("!logsil", "_reply_log_delete_user"), ("!hatalar", "_reply_errors"), ("!cache", "_reply_cache"), ("!cachetemizle", "_reply_cache_clear"), ("!info", "_reply_user_info"), ("!bakim", "_reply_maintenance"), ("!ayar", "_reply_settings")]:
            bot = FakeBot()
            bot.owner_id = 1
            msg = make_message(cmd, author_id=1)
            with patch("media_bot.bot_orchestration_mixin.i18n.locale_for_message", return_value="tr"):
                await bot.on_message(msg)
            getattr(bot, attr).assert_awaited()

    async def test_owner_only_commands_block_non_owner(self):
        bot = FakeBot()
        msg = make_message("!stats", author_id=2)
        with patch("media_bot.bot_orchestration_mixin.i18n.locale_for_message", return_value="tr"):
            await bot.on_message(msg)
        bot._reply.assert_awaited()
        self.assertIn("sahibine", bot._reply.await_args.args[1].lower())

    async def test_reply_helpers(self):
        bot = FakeBot()
        # bind real methods
        bot._reply = OrchestrationMixin._reply.__get__(bot, FakeBot)
        bot._reply_once = OrchestrationMixin._reply_once.__get__(bot, FakeBot)
        msg = make_message("hi")
        msg.reply = AsyncMock(return_value=SimpleNamespace())
        with patch("media_bot.bot_orchestration_mixin.SETTINGS.get", return_value=SimpleNamespace(reply_messages=True)):
            res = await bot._reply(msg, "hello")
        self.assertIsNotNone(res)
        msg.reply.assert_awaited()

        # upload too large then fit
        msg2 = make_message("hi")
        msg2.reply = AsyncMock(side_effect=[discord.HTTPException(SimpleNamespace(status=413, reason="Payload Too Large"), "too large"), SimpleNamespace()])
        bot._fit_attachments = AsyncMock(return_value=[Path("a")])
        with patch("media_bot.bot_orchestration_mixin._upload_too_large", return_value=True), \
             patch("media_bot.bot_orchestration_mixin.SETTINGS.get", return_value=SimpleNamespace(reply_messages=True)), \
             patch("discord.File", return_value=MagicMock(close=MagicMock())):
            res = await bot._reply(msg2, "hello", files=[Path("a")])
        self.assertIsNotNone(res)

    async def test_attach_cancel_view(self):
        bot = FakeBot()
        bot._attach_cancel_view = OrchestrationMixin._attach_cancel_view.__get__(bot, FakeBot)
        msg = make_message("hi")
        status = SimpleNamespace(edit=AsyncMock())
        view = await bot._attach_cancel_view(status, msg, "tr")
        self.assertIsNotNone(view)
        status.edit.assert_awaited()

    async def test_on_message_link_flow_and_guards(self):
        bot = FakeBot()
        msg = make_message("https://x.com/u/status/1", author_id=2)
        link = PostLink("x", "https://x.com/i/status/1", "1")

        class SlotCM:
            async def __aenter__(self): 
                await on_wait_holder["fn"]()
                return self
            async def __aexit__(self, *_a): return False
        on_wait_holder = {"fn": None}
        def slot(_uid, on_wait):
            on_wait_holder["fn"] = on_wait
            return SlotCM()
        bot.job_slots.slot = slot
        bot._handle_link = AsyncMock(return_value=("success", []))

        settings = SimpleNamespace(platform_enabled=lambda p: True, reply_messages=True)
        with patch("media_bot.bot.find_links", return_value=[link]), \
             patch("media_bot.bot.i18n.locale_for_message", return_value="tr"), \
             patch("media_bot.bot.i18n.t", side_effect=lambda k, *a, **kw: k), \
             patch("media_bot.bot_orchestration_mixin.SETTINGS.get", return_value=settings), \
             patch("media_bot.bot_orchestration_mixin.REQUESTS.record", return_value=1), \
             patch("media_bot.bot_orchestration_mixin.MAINTENANCE", SimpleNamespace(active=False, message="m")):
            await bot.on_message(msg)
        bot._handle_link.assert_awaited()

        # disabled platform
        bot2 = FakeBot()
        bot2.job_slots.slot = slot
        msg2 = make_message("https://x.com/u/status/1", author_id=2)
        settings2 = SimpleNamespace(platform_enabled=lambda p: False, reply_messages=True)
        with patch("media_bot.bot.find_links", return_value=[link]), \
             patch("media_bot.bot.i18n.locale_for_message", return_value="tr"), \
             patch("media_bot.bot.i18n.t", side_effect=lambda k, *a, **kw: k), \
             patch("media_bot.bot_orchestration_mixin.SETTINGS.get", return_value=settings2), \
             patch("media_bot.bot_orchestration_mixin.REQUESTS.record", return_value=1), \
             patch("media_bot.bot_orchestration_mixin.MAINTENANCE", SimpleNamespace(active=False, message="m")):
            await bot2.on_message(msg2)
        bot2._handle_link.assert_not_awaited()