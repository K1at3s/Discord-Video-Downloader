import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import discord

from media_bot.bot import UPLOAD_CEILING, InteractionMessage, MediaBot, upload_limit
from media_bot.cooldowns import Cooldowns

MB = 1024 * 1024


class UploadLimitTests(unittest.TestCase):
    def test_uses_guild_limit_when_no_cap_is_configured(self):
        message = SimpleNamespace(guild=SimpleNamespace(filesize_limit=10 * MB))
        self.assertEqual(upload_limit(message, 0), 10 * MB)

    def test_configured_value_caps_a_larger_guild_limit(self):
        message = SimpleNamespace(guild=SimpleNamespace(filesize_limit=50 * MB))
        self.assertEqual(upload_limit(message, 20 * MB), 20 * MB)
        self.assertEqual(upload_limit(message, 0), 50 * MB)

    def test_guild_limit_wins_over_a_larger_cap(self):
        message = SimpleNamespace(guild=SimpleNamespace(filesize_limit=10 * MB))
        self.assertEqual(upload_limit(message, 25 * MB), 10 * MB)

    def test_missing_limit_falls_back_to_configuration(self):
        self.assertEqual(upload_limit(None, 20 * MB), 20 * MB)
        self.assertEqual(upload_limit(SimpleNamespace(guild=None), 12 * MB), 12 * MB)
        self.assertEqual(upload_limit(SimpleNamespace(guild=SimpleNamespace(filesize_limit=0)), 0),
                         UPLOAD_CEILING)

    def test_ceiling_and_broken_values(self):
        message = SimpleNamespace(guild=SimpleNamespace(filesize_limit=2 * 1024 * MB))
        self.assertEqual(upload_limit(message, 0), UPLOAD_CEILING)
        broken = SimpleNamespace(guild=SimpleNamespace(filesize_limit="unknown"))
        self.assertEqual(upload_limit(broken, 8 * MB), 8 * MB)
        negative = SimpleNamespace(guild=SimpleNamespace(filesize_limit=-1))
        self.assertEqual(upload_limit(negative, 0), UPLOAD_CEILING)


class CooldownTests(unittest.TestCase):
    def test_blocks_repeated_commands(self):
        cooldowns = Cooldowns(2.0)
        self.assertTrue(cooldowns.allow(1, now=100.0))
        self.assertFalse(cooldowns.allow(1, now=101.0))
        self.assertTrue(cooldowns.allow(1, now=102.0))
        self.assertTrue(cooldowns.allow(2, now=102.0))

    def test_zero_seconds_disables_the_cooldown(self):
        cooldowns = Cooldowns(0)
        self.assertTrue(cooldowns.allow(1, now=1.0))
        self.assertTrue(cooldowns.allow(1, now=1.0))

    def test_entries_are_pruned(self):
        cooldowns = Cooldowns(2.0, max_entries=3)
        for user in range(10):
            cooldowns.allow(user, now=100.0 + user)
        self.assertLessEqual(len(cooldowns._seen), 3)


class InteractionMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_reply_uses_response_then_followup(self):
        class Response:
            def __init__(self):
                self.done = False
                self.sent = []

            def is_done(self):
                return self.done

            async def send_message(self, content=None, **kwargs):
                self.done = True
                self.sent.append(("response", content))
                return SimpleNamespace(id=1)

        class Followup:
            def __init__(self):
                self.sent = []

            async def send(self, content=None, **kwargs):
                self.sent.append(("followup", content))
                return SimpleNamespace(id=2)

        interaction = SimpleNamespace(
            user=SimpleNamespace(id=7), guild=None, channel=None, id=99,
            response=Response(), followup=Followup(),
        )
        adapter = InteractionMessage(interaction)
        await adapter.reply("hello", mention_author=False, suppress_embeds=True)
        await adapter.reply("again")
        self.assertEqual(interaction.response.sent, [("response", "hello")])
        self.assertEqual(interaction.followup.sent, [("followup", "again")])


class SlashCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_presence_is_playing_idle(self):
        with tempfile.TemporaryDirectory() as temp:
            bot = MediaBot(
                channel_ids=set(), upload_bytes=0, max_links=3, concurrency=2,
                cookie_file=None, cobalt_url=None, cobalt_key=None,
                gallery_rs_path=None, database_path=Path(temp) / "bot.sqlite3",
            )
            try:
                self.assertIs(bot.status, discord.Status.idle)
                self.assertEqual(bot.activity.name, "made by hakixld")
                self.assertIs(bot.activity.type, discord.ActivityType.playing)
                self.assertEqual(bot.activity.to_dict()["type"], 0)
            finally:
                await bot.close()

    async def test_commands_are_registered(self):
        with tempfile.TemporaryDirectory() as temp:
            bot = MediaBot(
                channel_ids=set(), upload_bytes=0, max_links=3, concurrency=2,
                cookie_file=None, cobalt_url=None, cobalt_key=None,
                gallery_rs_path=None, database_path=Path(temp) / "bot.sqlite3",
            )
            try:
                names = {command.name for command in bot.tree.get_commands()}
                self.assertTrue({"yardim", "metrics", "stats", "info", "cache",
                                 "cachetemizle", "hatalar", "bakim", "platform",
                                 "logsil", "logtemizle"} <= names)
            finally:
                await bot.close()


if __name__ == "__main__":
    unittest.main()
