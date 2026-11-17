import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from media_bot.bot import MediaBot, ThreadMessage
from media_bot.guild_settings import SETTINGS, GuildSettings, GuildSettingsStore
from media_bot.links import PostLink


class GuildSettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GuildSettingsStore(Path(self.temp.name) / "settings.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_unknown_guild_uses_defaults(self):
        settings = self.store.get(123)
        self.assertFalse(settings.auto_thread)
        self.assertEqual(settings.youtube_quality, "sor")

    def test_update_persists_and_is_per_guild(self):
        self.store.update(1, auto_thread=True, youtube_quality="720")
        self.assertEqual(self.store.get(1), GuildSettings(auto_thread=True, youtube_quality="720"))
        self.assertEqual(self.store.get(2), GuildSettings())

    def test_partial_update_keeps_other_field(self):
        self.store.update(1, youtube_quality="mp3")
        self.store.update(1, auto_thread=True)
        settings = self.store.get(1)
        self.assertTrue(settings.auto_thread)
        self.assertEqual(settings.youtube_quality, "mp3")

    def test_invalid_quality_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.update(1, youtube_quality="4k")


class SettingsCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        SETTINGS.configure(Path(self.temp.name) / "settings.sqlite3")
        self.bot = MediaBot(
            channel_ids=set(), upload_bytes=0, max_links=3, concurrency=1,
            cookie_file=None, cobalt_url=None, cobalt_key=None,
            gallery_rs_path=None, database_path=Path(self.temp.name) / "bot.sqlite3",
        )

    async def asyncTearDown(self):
        await self.bot.close()
        SETTINGS.configure(None)
        self.temp.cleanup()

    def _message(self, *, admin=True, content=""):
        return SimpleNamespace(
            guild=SimpleNamespace(id=55),
            author=SimpleNamespace(id=9, guild_permissions=SimpleNamespace(manage_guild=admin)),
            content=content,
            reply=AsyncMock(),
        )

    async def test_admin_can_enable_thread_and_set_quality(self):
        message = self._message()
        await self.bot._reply_settings(message, "thread ac youtube 720")
        settings = SETTINGS.get(55)
        self.assertTrue(settings.auto_thread)
        self.assertEqual(settings.youtube_quality, "720")
        self.assertIn("açık", message.reply.await_args.kwargs["content"])

    async def test_non_admin_is_refused(self):
        message = self._message(admin=False)
        await self.bot._reply_settings(message, "thread ac")
        self.assertFalse(SETTINGS.get(55).auto_thread)
        self.assertIn("Sunucuyu yönet", message.reply.await_args.kwargs["content"])

    async def test_invalid_quality_shows_usage(self):
        message = self._message()
        await self.bot._reply_settings(message, "youtube 4k")
        self.assertIn("Geçersiz", message.reply.await_args.kwargs["content"])

    async def test_reset_restores_defaults(self):
        SETTINGS.update(55, auto_thread=True, youtube_quality="1080")
        message = self._message()
        await self.bot._reply_settings(message, "sifirla")
        self.assertEqual(SETTINGS.get(55), GuildSettings())

    async def test_slash_command_is_registered(self):
        self.assertIsNotNone(self.bot.tree.get_command("ayar"))


class AutoThreadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bot = MediaBot(
            channel_ids=set(), upload_bytes=0, max_links=3, concurrency=1,
            cookie_file=None, cobalt_url=None, cobalt_key=None,
            gallery_rs_path=None, database_path=Path(self.temp.name) / "bot.sqlite3",
        )
        self.thread = SimpleNamespace(id=77, send=AsyncMock())

    async def asyncTearDown(self):
        await self.bot.close()
        self.temp.cleanup()

    def _message(self, channel=None):
        return SimpleNamespace(
            guild=SimpleNamespace(id=5),
            channel=channel or SimpleNamespace(id=10),
            content="şu videoya bak https://www.tiktok.com/@a/video/1",
            author=SimpleNamespace(id=3),
            create_thread=AsyncMock(return_value=self.thread),
        )

    async def test_thread_is_created_when_enabled(self):
        message = self._message()
        with patch.object(SETTINGS, "get", return_value=GuildSettings(auto_thread=True)):
            wrapped = await self.bot._auto_thread_message(message, [])
        self.assertIsInstance(wrapped, ThreadMessage)
        self.assertIs(wrapped.channel, self.thread)
        self.assertIn("şu videoya bak", message.create_thread.await_args.kwargs["name"])

    async def test_no_thread_when_disabled(self):
        message = self._message()
        with patch.object(SETTINGS, "get", return_value=GuildSettings(auto_thread=False)):
            wrapped = await self.bot._auto_thread_message(message, [])
        self.assertIs(wrapped, message)
        message.create_thread.assert_not_awaited()

    async def test_existing_thread_is_left_alone(self):
        class FakeThread:
            pass

        message = self._message(channel=FakeThread())
        with patch.object(SETTINGS, "get", return_value=GuildSettings(auto_thread=True)), \
                patch("media_bot.bot.discord.Thread", FakeThread):
            wrapped = await self.bot._auto_thread_message(message, [])
        self.assertIs(wrapped, message)
        message.create_thread.assert_not_awaited()

    async def test_thread_message_replies_inside_thread(self):
        wrapped = ThreadMessage(SimpleNamespace(author=SimpleNamespace(id=1)), self.thread)
        await wrapped.reply("merhaba", files=[], view=None)
        self.thread.send.assert_awaited_once()
        self.assertEqual(self.thread.send.await_args.kwargs["content"], "merhaba")


class YoutubeDefaultQualityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bot = MediaBot(
            channel_ids=set(), upload_bytes=0, max_links=3, concurrency=1,
            cookie_file=None, cobalt_url=None, cobalt_key=None,
            gallery_rs_path=None, database_path=Path(self.temp.name) / "bot.sqlite3",
        )
        self.link = PostLink("youtube", "https://www.youtube.com/watch?v=abc")

    async def asyncTearDown(self):
        await self.bot.close()
        self.temp.cleanup()

    def _message(self):
        return SimpleNamespace(
            guild=SimpleNamespace(id=6, filesize_limit=10 * 1024 * 1024),
            author=SimpleNamespace(id=2),
            reply=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())),
        )

    async def test_prompt_is_skipped_when_a_default_exists(self):
        message = self._message()
        with patch.object(SETTINGS, "get", return_value=GuildSettings(youtube_quality="720")), \
                patch("media_bot.bot.deliver_youtube", new=AsyncMock(return_value=(True, [], "720p"))) as deliver:
            result = await self.bot._handle_link(message, self.link)
        self.assertEqual(result[0], "success")
        self.assertEqual(deliver.await_args.args[3], "720")

    async def test_prompt_is_shown_when_quality_is_ask(self):
        message = self._message()
        with patch.object(SETTINGS, "get", return_value=GuildSettings(youtube_quality="sor")), \
                patch("media_bot.bot.YoutubeChoiceView") as view_type:
            view_type.return_value = SimpleNamespace(prompt_message=None)
            result = await self.bot._handle_link(message, self.link)
        self.assertEqual(result[0], "pending")


if __name__ == "__main__":
    unittest.main()
