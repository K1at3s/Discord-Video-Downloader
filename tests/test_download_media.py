import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from media_bot.download import DownloadError, download_media
from media_bot.links import PostLink


def make_tmp(self):
    d = Path(tempfile.mkdtemp(prefix="dlm-"))
    self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
    return d


class DownloadMediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_hit_and_singleflight_follower(self):
        tmp = make_tmp(self)
        link = PostLink("reddit", "https://www.reddit.com/r/a/comments/abc123/c", "abc123")
        cached = SimpleNamespace(provider="yt-dlp")
        cache_file = tmp / "cached.mp4"
        cache_file.write_bytes(b"video")

        def copy_to(_entry, folder):
            (folder / "cached.mp4").write_bytes(b"video")

        done_fut = asyncio.get_running_loop().create_future()
        done_fut.set_result(None)
        fake_cache = SimpleNamespace(
            enabled=True,
            key_for=lambda p, u, c: "k",
            get=lambda *a, **k: cached,
            copy_to=copy_to,
            singleflight_start=AsyncMock(return_value=(False, done_fut)),
            singleflight_finish=AsyncMock(),
            put=lambda *a, **k: None,
        )
        with patch("media_bot.download.ensure_free_disk"), \
             patch("media_bot.download.MEDIA_CACHE", fake_cache), \
             patch("media_bot.download.METRICS.record_winner"), \
             patch("media_bot.download.highlight_id", return_value=None), \
             patch("media_bot.download.HIGHLIGHTS", SimpleNamespace(enabled=False, cache=None)):
            files, provider = await download_media(link, tmp, 1000)
        self.assertEqual(provider, "yt-dlp")
        self.assertTrue(files)

    async def test_canonical_facebook_and_threads_short(self):
        tmp = make_tmp(self)
        link = PostLink("facebook", "https://www.facebook.com/share/p/abc123/", None)
        canonical = PostLink("facebook", "https://www.facebook.com/reel/1/", "1")
        with patch("media_bot.download.ensure_free_disk"), \
             patch("media_bot.download.canonical_facebook_post", AsyncMock(return_value=canonical)), \
             patch("media_bot.download.MEDIA_CACHE", SimpleNamespace(enabled=False)), \
             patch("media_bot.download.highlight_id", return_value=None), \
             patch("media_bot.download.highlight_post_link", return_value=None), \
             patch("media_bot.download._execute_provider", AsyncMock(return_value=(0, ""))), \
             patch("media_bot.download.media_files", return_value=[tmp / "a.mp4"]), \
             patch("media_bot.download.METRICS.record_winner"), \
             patch("media_bot.download.METRICS.record"), \
             patch("media_bot.download.BREAKERS.allow", return_value=True), \
             patch("media_bot.download.BREAKERS.success"), \
             patch("media_bot.download.BREAKERS.neutral"), \
             patch("media_bot.download.BREAKERS.failure", return_value=None), \
             patch("media_bot.download.BREAKERS.abandon"), \
             patch("media_bot.download.provider_memory.preferred", AsyncMock(return_value=None)), \
             patch("media_bot.download.provider_memory.ranked", AsyncMock(side_effect=lambda _p, names: list(names))), \
             patch("media_bot.download.provider_memory.hedge_seconds", return_value=0), \
             patch("media_bot.download.provider_memory.remember"), \
             patch("media_bot.download.provider_memory.forget"):
            (tmp / "a.mp4").write_bytes(b"x")
            files, provider = await download_media(link, tmp, 1000)
        self.assertTrue(files)

        # threads short link without session should raise specific message at end
        tmp2 = make_tmp(self)
        tlink = PostLink("threads", "https://www.threads.com/t/ABC", None)
        with patch("media_bot.download.ensure_free_disk"), \
             patch("media_bot.download.MEDIA_CACHE", SimpleNamespace(enabled=False)), \
             patch("media_bot.download.highlight_id", return_value=None), \
             patch("media_bot.download.highlight_post_link", return_value=None), \
             patch("media_bot.download._execute_provider", AsyncMock(return_value=(1, "no media"))), \
             patch("media_bot.download.media_files", return_value=[]), \
             patch("media_bot.download.METRICS.record_winner"), \
             patch("media_bot.download.METRICS.record"), \
             patch("media_bot.download.BREAKERS.allow", return_value=True), \
             patch("media_bot.download.BREAKERS.success"), \
             patch("media_bot.download.BREAKERS.neutral"), \
             patch("media_bot.download.BREAKERS.failure", return_value=None), \
             patch("media_bot.download.BREAKERS.abandon"), \
             patch("media_bot.download.provider_memory.preferred", AsyncMock(return_value=None)), \
             patch("media_bot.download.provider_memory.ranked", AsyncMock(side_effect=lambda _p, names: list(names))), \
             patch("media_bot.download.provider_memory.hedge_seconds", return_value=0), \
             patch("media_bot.download.provider_memory.remember"), \
             patch("media_bot.download.provider_memory.forget"):
            with self.assertRaises(DownloadError) as ctx:
                await download_media(tlink, tmp2, 1000)
        self.assertIn("Threads", str(ctx.exception))

    async def test_private_oversize_and_botcheck_errors(self):
        from media_bot.download import download_media
        link = PostLink("instagram", "https://www.instagram.com/p/abc/", "abc")
        common = dict(
            ensure_free_disk=patch("media_bot.download.ensure_free_disk"),
            media_cache=patch("media_bot.download.MEDIA_CACHE", SimpleNamespace(enabled=False)),
            highlight_id=patch("media_bot.download.highlight_id", return_value=None),
            highlight_post_link=patch("media_bot.download.highlight_post_link", return_value=None),
            highlights=patch("media_bot.download.HIGHLIGHTS", SimpleNamespace(enabled=False, cache=None, bucket=SimpleNamespace(wait_seconds=lambda: 0))),
            metrics_w=patch("media_bot.download.METRICS.record_winner"),
            metrics_r=patch("media_bot.download.METRICS.record"),
            ba=patch("media_bot.download.BREAKERS.allow", return_value=True),
            bs=patch("media_bot.download.BREAKERS.success"),
            bn=patch("media_bot.download.BREAKERS.neutral"),
            bf=patch("media_bot.download.BREAKERS.failure", return_value=None),
            bab=patch("media_bot.download.BREAKERS.abandon"),
            pp=patch("media_bot.download.provider_memory.preferred", AsyncMock(return_value=None)),
            pr=patch("media_bot.download.provider_memory.ranked", AsyncMock(side_effect=lambda _p, names: list(names[:1]))),
            ph=patch("media_bot.download.provider_memory.hedge_seconds", return_value=0),
            pm=patch("media_bot.download.provider_memory.remember"),
            pf=patch("media_bot.download.provider_memory.forget"),
        )
        # private
        tmp = make_tmp(self)
        for p in common.values():
            p.start()
        try:
            with patch("media_bot.download._execute_provider", AsyncMock(return_value=(1, "login required"))), \
                 patch("media_bot.download.media_files", return_value=[]):
                with self.assertRaises(DownloadError) as ctx:
                    await download_media(link, tmp, 1000)
            self.assertIn("gizli", str(ctx.exception).lower())
        finally:
            for p in common.values():
                p.stop()
