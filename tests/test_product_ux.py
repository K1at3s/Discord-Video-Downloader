import tempfile
import unittest
from pathlib import Path

from media_bot.guild_settings import GuildSettingsStore
from media_bot.request_log import RequestLog


class GuildPlatformToggleTests(unittest.TestCase):
    def test_disable_and_enable_platform(self):
        with tempfile.TemporaryDirectory() as folder:
            store = GuildSettingsStore(Path(folder) / "bot.sqlite3")
            settings = store.update(1, disabled_platforms=("instagram", "tiktok"))
            self.assertEqual(settings.disabled_platforms, ("instagram", "tiktok"))
            self.assertFalse(settings.platform_enabled("instagram"))
            self.assertTrue(settings.platform_enabled("youtube"))
            settings = store.update(1, disabled_platforms=())
            self.assertTrue(settings.platform_enabled("instagram"))


class RequestLogFilterTests(unittest.TestCase):
    def test_format_filtered_by_platform_and_outcome(self):
        with tempfile.TemporaryDirectory() as folder:
            log = RequestLog(Path(folder) / "req.sqlite3")
            log.record(guild_id=1, guild_name="g", channel_id=1, user_id=10, user_name="u",
                       platform="instagram", url="https://instagram.com/p/a", outcome="success")
            log.record(guild_id=1, guild_name="g", channel_id=1, user_id=10, user_name="u",
                       platform="instagram", url="https://instagram.com/p/b", outcome="failure", detail="boom")
            text = log.format_filtered(24, platform="instagram", outcome="failure")
            self.assertIn("instagram", text.lower())
            self.assertIn("failure", text.lower())
            self.assertIn("boom", text)


if __name__ == "__main__":
    unittest.main()
