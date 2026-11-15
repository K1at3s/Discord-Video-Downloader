import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from media_bot.download import DownloadError, _yt_dlp, download_media, looks_private, provider_order
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
        expected = {"instagram": 9, "facebook": 5, "reddit": 5, "x": 6, "tiktok": 6}
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


class InstagramStoryRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_story_link_uses_indown_net_first(self):
        calls = []

        async def fake_web(name, _link, folder, _max_bytes, _cookie=None):
            calls.append(name)
            (folder / f"{name}_1.mp4").write_bytes(b"video")
            return 1, ""

        with tempfile.TemporaryDirectory() as temp:
            link = PostLink("instagram", "https://www.instagram.com/stories/natgeo/4029782063186981984/")
            with (patch("media_bot.download.download_web", new=fake_web),
                  patch("media_bot.download.download_indownio", side_effect=AssertionError("wrong route")),
                  patch("media_bot.download.first_success", side_effect=AssertionError("race ran")),
                  patch("media_bot.download.BREAKERS.allow", return_value=True),
                  patch("media_bot.download.BREAKERS.success"),
                  patch("media_bot.download.METRICS.record"),
                  patch("media_bot.download.METRICS.record_winner")):
                files, provider = await download_media(link, Path(temp), 1024)
            self.assertEqual(provider, "indown")
            self.assertEqual(len(files), 1)
            self.assertEqual(calls, ["indown"])

    async def test_highlight_link_uses_indown_net_first(self):
        calls = []

        async def fake_web(_name, _link, folder, _max_bytes, _cookie=None):
            calls.append("indown")
            (folder / "indown_1.mp4").write_bytes(b"video")
            return 1, ""

        with tempfile.TemporaryDirectory() as temp:
            link = PostLink("instagram", "https://www.instagram.com/stories/highlights/17919681140787123/")
            with (patch("media_bot.download.download_web", new=fake_web),
                  patch("media_bot.download.download_indownio", side_effect=AssertionError("wrong route")),
                  patch("media_bot.download.first_success", side_effect=AssertionError("race ran")),
                  patch("media_bot.download.BREAKERS.allow", return_value=True),
                  patch("media_bot.download.BREAKERS.success"),
                  patch("media_bot.download.METRICS.record"),
                  patch("media_bot.download.METRICS.record_winner")):
                files, provider = await download_media(link, Path(temp), 1024)
            self.assertEqual(provider, "indown")
            self.assertEqual(len(files), 1)
            self.assertEqual(calls, ["indown"])

    async def test_story_falls_back_to_other_routes_when_first_choice_fails(self):
        order: list[tuple[str, ...]] = []

        async def fake_web(_name, _link, _folder, _max_bytes, _cookie=None):
            return 0, "no usable media"

        async def fake_first_success(providers, attempt, stagger=0):
            order.append(tuple(providers))
            return None

        with tempfile.TemporaryDirectory() as temp:
            link = PostLink("instagram", "https://www.instagram.com/stories/natgeo/4029782063186981984/")
            with (patch("media_bot.download.download_web", new=fake_web),
                  patch("media_bot.download.first_success", new=fake_first_success),
                  patch("media_bot.download.BREAKERS.allow", return_value=True),
                  patch("media_bot.download.BREAKERS.neutral"),
                  patch("media_bot.download.METRICS.record"),
                  patch("media_bot.download.METRICS.record_winner"),
                  self.assertRaises(DownloadError)):
                await download_media(link, Path(temp), 1024)
            self.assertEqual(len(order), 1)
            self.assertNotIn("indown", order[0])
            self.assertIn("indownio", order[0])

    async def test_highlight_falls_back_to_other_routes_when_viewer_fails(self):
        order: list[tuple[str, ...]] = []

        async def fake_web(_name, _link, _folder, _max_bytes, _cookie=None):
            return 0, "no usable media"

        async def fake_first_success(providers, attempt, stagger=0):
            order.append(tuple(providers))
            return None

        with tempfile.TemporaryDirectory() as temp:
            link = PostLink("instagram", "https://www.instagram.com/stories/highlights/17919681140787123/")
            with (patch("media_bot.download.download_web", new=fake_web),
                  patch("media_bot.download.first_success", new=fake_first_success),
                  patch("media_bot.download.BREAKERS.allow", return_value=True),
                  patch("media_bot.download.BREAKERS.neutral"),
                  patch("media_bot.download.METRICS.record"),
                  patch("media_bot.download.METRICS.record_winner")):
                with self.assertRaises(DownloadError) as raised:
                    await download_media(link, Path(temp), 1024)
        self.assertNotIn("indown", order[0])
        self.assertIn("indownio", order[0])
        self.assertIn("medya alınamadı", str(raised.exception))


class ThreadsPluginRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_cookie_free_threadsdl_wins_even_with_cookie_configured(self):
        calls = []

        async def fake_download(name, _link, folder, _max_bytes, _cookie_file):
            calls.append(name)
            (folder / "threadsdl_1.mp4").write_bytes(b"video")
            return 1, ""

        with tempfile.TemporaryDirectory() as temp:
            link = PostLink("threads", "https://www.threads.com/@example/post/ABC123")
            with (patch("media_bot.download.download_web", new=fake_download),
                  patch("media_bot.download.first_success", side_effect=AssertionError("fallback ran")),
                  patch("media_bot.download.BREAKERS.allow", return_value=True),
                  patch("media_bot.download.BREAKERS.success"),
                  patch("media_bot.download.METRICS.record"),
                  patch("media_bot.download.METRICS.record_winner")):
                files, provider = await download_media(link, Path(temp), 1024, cookie_file=Path("cookies.txt"))
            self.assertEqual(provider, "threadsdl")
            self.assertEqual(len(files), 1)
            self.assertEqual(calls, ["threadsdl"])

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
