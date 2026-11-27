import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from media_bot.gendownload import download_gendownload, media_options
from media_bot.links import PostLink


class GenDownloadTests(unittest.TestCase):
    def test_only_hosted_media_streams_and_uploadable_formats(self):
        response = {"formats": [
            {"type": "video", "ext": "mp4", "filesize": 500, "url": "https://dl.gendownload.com/api/stream?t=a&i=0"},
            {"type": "video", "ext": "mp4", "filesize": 5, "url": "https://dl.gendownload.com/api/stream?t=a&i=1"},
            {"type": "audio", "ext": "mp3", "url": "https://dl.gendownload.com/api/stream?t=a&i=2"},
            {"type": "video", "ext": "mp4", "url": "https://dl.gendownload.com.evil.test/api/stream?t=a&i=3"},
        ]}
        self.assertEqual(media_options(response, 100), ([], ["https://dl.gendownload.com/api/stream?t=a&i=1"]))

    def test_carousel_keeps_more_than_ten_images(self):
        formats = [{"type": "image", "ext": "jpg",
                    "url": f"https://dl.gendownload.com/api/stream?t=a&i={index}"}
                   for index in range(23)]
        images, videos = media_options({"formats": formats}, 1024)
        self.assertEqual(len(images), 23)
        self.assertEqual(videos, [])


class GenDownloadDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_download_gendownload(self):
        tmp = Path(tempfile.mkdtemp(prefix="gd-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        link = PostLink("tiktok", "https://www.tiktok.com/@u/video/1", "1")

        class CM:
            def __init__(self, post):
                self.post = post
            async def __aenter__(self): return self
            async def __aexit__(self, *_a): return False

        payload = {"formats": [
            {"type": "image", "ext": "jpg", "url": "https://dl.gendownload.com/api/stream?t=a&i=0"},
            {"type": "video", "ext": "mp4", "url": "https://dl.gendownload.com/api/stream?t=a&i=1"},
        ]}
        resp = SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)
        client = CM(post=AsyncMock(return_value=resp))
        with patch("media_bot.gendownload.shared_client", return_value=client), \
             patch("media_bot.gendownload._stream", AsyncMock(side_effect=lambda c,u,s,o,m: o.write_bytes(b"x") or True)):
            count, err = await download_gendownload(link, tmp, 10_000)
        self.assertEqual(count, 2)

        with patch("media_bot.gendownload.shared_client", return_value=client), \
             patch("media_bot.gendownload._stream", AsyncMock(return_value=False)):
            count, err = await download_gendownload(link, tmp, 10_000)
        self.assertEqual(count, 0)

        calls = {"n": 0}
        async def post(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("boom")
            return resp
        client2 = CM(post=post)
        with patch("media_bot.gendownload.shared_client", return_value=client2), \
             patch("media_bot.gendownload._stream", AsyncMock(side_effect=lambda c,u,s,o,m: o.write_bytes(b"x") or True)), \
             patch("media_bot.gendownload.asyncio.sleep", AsyncMock()):
            count, err = await download_gendownload(link, tmp, 10_000)
        self.assertEqual(count, 2)

        with patch("media_bot.gendownload.shared_client", side_effect=httpx.HTTPError("boom")):
            count, err = await download_gendownload(link, tmp, 10_000)
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
