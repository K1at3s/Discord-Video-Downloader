import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from media_bot.download import (
    DownloadError,
    _cookies,
    looks_bot_check,
    looks_private,
    media_files,
    provider_order,
    provider_variant,
)
from media_bot.links import PostLink


class DownloadHelperTests(unittest.TestCase):
    def test_looks_private_and_bot_check(self):
        self.assertTrue(looks_private("This account is private"))
        self.assertTrue(looks_private("login required"))
        self.assertFalse(looks_private("other error"))
        self.assertTrue(looks_bot_check("Sign in to confirm you're not a bot"))
        self.assertTrue(looks_bot_check("use --cookies for authentication"))
        self.assertFalse(looks_bot_check("timeout"))

    def test_provider_variant_and_order(self):
        self.assertEqual(provider_variant(PostLink("instagram", "https://www.instagram.com/stories/a/1/", None)), "story")
        self.assertEqual(provider_variant(PostLink("threads", "https://www.threads.com/@u/post/1", "1")), "post")
        self.assertEqual(provider_variant(PostLink("reddit", "https://reddit.com/r/a/comments/b/c", "b")), "")

        order = provider_order("instagram", cobalt_enabled=True, gallery_rs_enabled=True)
        self.assertIn("cobalt", order)
        self.assertIn("gallery-dl-rs", order)
        with self.assertRaises(ValueError):
            provider_order("unknown")

    def test_media_files_and_cookies(self):
        tmp = Path(tempfile.mkdtemp(prefix="dlh-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        (tmp / "a.mp4").write_bytes(b"123")
        (tmp / "b.txt").write_bytes(b"123")
        (tmp / "c.mp4").write_bytes(b"1" * 20)
        files = media_files(tmp, 10)
        self.assertEqual([p.name for p in files], ["a.mp4"])
        self.assertEqual(_cookies(None), [])
        cookie = tmp / "cookies.txt"
        cookie.write_text("# Netscape HTTP Cookie File\n")
        self.assertEqual(_cookies(cookie), ["--cookies", str(cookie)])


class DownloadMediaBranchTests(unittest.IsolatedAsyncioTestCase):
    async def test_low_disk_raises(self):
        from media_bot.download import download_media

        tmp = Path(tempfile.mkdtemp(prefix="dlm-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        link = PostLink("reddit", "https://www.reddit.com/r/a/comments/b/c", "b")
        with patch("media_bot.download.ensure_free_disk", side_effect=__import__("media_bot.disk", fromlist=["LowDiskError"]).LowDiskError("disk")):
            with self.assertRaises(DownloadError):
                await download_media(link, tmp, 1000)

class ExecuteProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_provider_dispatch(self):
        from media_bot.download import _execute_provider
        link = PostLink("reddit", "https://www.reddit.com/r/a/comments/abc123/c", "abc123")
        tmp = Path(tempfile.mkdtemp(prefix="ep-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))

        with patch("media_bot.download._yt_dlp", AsyncMock(return_value=(0, ""))):
            self.assertEqual(await _execute_provider("yt-dlp", link, tmp, 100, None, None, None, None), (0, ""))
        with patch("media_bot.download._gallery_dl", AsyncMock(return_value=(0, ""))):
            self.assertEqual(await _execute_provider("gallery-dl", link, tmp, 100, None, None, None, None), (0, ""))
        with patch("media_bot.download._instaloader", AsyncMock(return_value=(0, ""))):
            self.assertEqual(await _execute_provider("instaloader", link, tmp, 100, None, None, None, None), (0, ""))
        with patch("media_bot.download._you_get", AsyncMock(return_value=(0, ""))):
            self.assertEqual(await _execute_provider("you-get", link, tmp, 100, None, None, None, None), (0, ""))
        with patch("media_bot.download._gallery_rs", AsyncMock(return_value=(0, ""))):
            self.assertEqual(await _execute_provider("gallery-dl-rs", link, tmp, 100, None, None, None, tmp), (0, ""))
        with patch("media_bot.download.download_direct", AsyncMock(return_value=(1, "no"))):
            self.assertEqual(await _execute_provider("direct", link, tmp, 100, None, None, None, None), (0, "no"))
        with patch("media_bot.download.download_gendownload", AsyncMock(return_value=(2, ""))):
            self.assertEqual(await _execute_provider("gendownload", link, tmp, 100, None, None, None, None), (0, ""))
        with patch("media_bot.download.download_vxtwitter", AsyncMock(return_value=(0, "x"))):
            self.assertEqual(await _execute_provider("vxtwitter", link, tmp, 100, None, None, None, None), (1, "x"))
        with patch("media_bot.download.download_indownio", AsyncMock(return_value=(1, ""))):
            self.assertEqual(await _execute_provider("indownio", link, tmp, 100, None, None, None, None), (0, ""))
        with patch("media_bot.download.download_web", AsyncMock(return_value=(0, "e"))):
            self.assertEqual(await _execute_provider("indown", link, tmp, 100, None, None, None, None), (1, "e"))
        with patch("media_bot.download.download_cobalt", AsyncMock(return_value=(1, ""))):
            self.assertEqual(await _execute_provider("cobalt", link, tmp, 100, None, "http://c", None, None), (0, ""))

        # timeout branches
        async def slow(*a, **k):
            await asyncio.sleep(10)

        async def fake_wait_for(coro, *a, **k):
            coro.close()
            raise asyncio.TimeoutError

        for name, target_name in [
            ("direct", "media_bot.download.download_direct"),
            ("gendownload", "media_bot.download.download_gendownload"),
            ("vxtwitter", "media_bot.download.download_vxtwitter"),
            ("indownio", "media_bot.download.download_indownio"),
            ("indown", "media_bot.download.download_web"),
            ("cobalt", "media_bot.download.download_cobalt"),
        ]:
            args = (link, tmp, 100, None, "http://c", None, None) if name == "cobalt" else (link, tmp, 100, None, None, None, None)
            with patch(target_name, side_effect=slow), patch("media_bot.download.asyncio.wait_for", side_effect=fake_wait_for):
                code, _ = await _execute_provider(name, *args)
                self.assertEqual(code, -1)
