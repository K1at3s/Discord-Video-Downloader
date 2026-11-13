import unittest
from pathlib import Path
from unittest.mock import patch

from media_bot.download import _yt_dlp, looks_private, provider_order
from media_bot.links import PostLink


class ProviderOrderTests(unittest.TestCase):
    def test_private_account_detection(self):
        self.assertTrue(looks_private("[instagram][error] HTTP redirect to login page (https://www.instagram.com/accounts/login/)"))
        self.assertTrue(looks_private("ERROR: [Instagram] DfnT4zOIADJ: This post is private"))
        self.assertTrue(looks_private("Login required"))
        self.assertFalse(looks_private("[SSL: INVALID_SESSION_ID] invalid session id (_ssl.c:1010)"))
        self.assertFalse(looks_private("no usable media"))
        self.assertFalse(looks_private("zaman aşımı"))

    def test_provider_routes(self):
        expected = {"instagram": 8, "facebook": 5, "reddit": 5, "x": 6, "tiktok": 6}
        for platform, base_count in expected.items():
            with self.subTest(platform=platform):
                self.assertEqual(len(provider_order(platform)), base_count)
                self.assertEqual(len(provider_order(platform, cobalt_enabled=True)), base_count + 1)
                self.assertEqual(len(provider_order(platform, cobalt_enabled=True, gallery_rs_enabled=True)), base_count + 2)
                if platform not in {"instagram", "reddit"}:
                    self.assertEqual(provider_order(platform)[0], "direct")
                if platform in {"instagram", "facebook", "tiktok", "x"}:
                    self.assertIn("gendownload", provider_order(platform))
        self.assertEqual(provider_order("reddit")[:2], ("rapidsave", "vette"))
        self.assertEqual(provider_order("reddit")[-1], "direct")

    def test_added_platforms_have_real_routes(self):
        self.assertEqual(provider_order("threads"), ("threadsdl", "threads-page", "yt-dlp", "vette"))
        self.assertEqual(provider_order("douyin"), ("yt-dlp", "direct"))
        self.assertEqual(provider_order("weibo"), ("gallery-dl", "yt-dlp"))
        self.assertEqual(provider_order("streamable", cobalt_enabled=True),
                         ("direct", "cobalt", "yt-dlp"))
        with self.assertRaises(ValueError):
            provider_order("spotify")


class ThreadsPluginRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_threads_keeps_all_video_entries(self):
        commands = []

        async def fake_run(command, **_kwargs):
            commands.append(command)
            return 0, ""

        link = PostLink("threads", "https://www.threads.com/@example/post/ABC123")
        with patch("media_bot.download._run", new=fake_run):
            await _yt_dlp(link, Path("unused"), 20 * 1024 * 1024, None)
        self.assertIn("--yes-playlist", commands[0])
        self.assertNotIn("--no-playlist", commands[0])


if __name__ == "__main__":
    unittest.main()
