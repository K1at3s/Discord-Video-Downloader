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


    async def test_attempt_success_and_cache_store(self):
        link = PostLink("reddit", "https://www.reddit.com/r/a/comments/abc123/c", "abc123")
        tmp = make_tmp(self)
        prov_dir = tmp / "yt-dlp"
        prov_dir.mkdir()
        (prov_dir / "a.mp4").write_bytes(b"video")

        stored = {}
        fake_cache = SimpleNamespace(
            enabled=True,
            key_for=lambda p,u,c:"k",
            get=lambda *a,**k: None,
            copy_to=lambda *a,**k: None,
            singleflight_start=AsyncMock(return_value=(True, None)),
            singleflight_finish=AsyncMock(),
            put=lambda platform,url,cookies,provider,files: stored.update({"provider":provider,"files":list(files)}),
        )
        with patch("media_bot.download.ensure_free_disk"), \
             patch("media_bot.download.MEDIA_CACHE", fake_cache), \
             patch("media_bot.download.HIGHLIGHTS", SimpleNamespace(enabled=False, cache=None)), \
             patch("media_bot.download.highlight_id", return_value=None), \
             patch("media_bot.download.highlight_post_link", return_value=None), \
             patch("media_bot.download.METRICS.record_winner"), \
             patch("media_bot.download.METRICS.record"), \
             patch("media_bot.download.BREAKERS.allow", return_value=True), \
             patch("media_bot.download.BREAKERS.success"), \
             patch("media_bot.download.BREAKERS.neutral"), \
             patch("media_bot.download.BREAKERS.failure", return_value=None), \
             patch("media_bot.download.BREAKERS.abandon"), \
             patch("media_bot.download.provider_memory.preferred", AsyncMock(return_value=None)), \
             patch("media_bot.download.provider_memory.ranked", AsyncMock(side_effect=lambda _p,names:list(names[:1]))), \
             patch("media_bot.download.provider_memory.hedge_seconds", return_value=0), \
             patch("media_bot.download.provider_memory.remember"), \
             patch("media_bot.download.provider_memory.forget"), \
             patch("media_bot.download._execute_provider", AsyncMock(return_value=(0, ""))), \
             patch("media_bot.download.media_files", return_value=[prov_dir / "a.mp4"]):
            files, provider = await download_media(link, tmp, 1000)
        self.assertTrue(files)
        self.assertEqual(stored.get("provider"), provider)
        fake_cache.singleflight_finish.assert_awaited()


    async def test_download_media_error_endings(self):
        link = PostLink("instagram", "https://www.instagram.com/p/abc/", "abc")
        base = dict(
            ensure_free_disk=patch("media_bot.download.ensure_free_disk"),
            media_cache=patch("media_bot.download.MEDIA_CACHE", SimpleNamespace(enabled=False)),
            highlight_id=patch("media_bot.download.highlight_id", return_value=None),
            highlight_post_link=patch("media_bot.download.highlight_post_link", return_value=None),
            highlights=patch("media_bot.download.HIGHLIGHTS", SimpleNamespace(enabled=False, cache=None, bucket=SimpleNamespace(wait_seconds=lambda: 60))),
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

        async def run_case(tmp, exec_side_effect, expected):
            for p in base.values():
                p.start()
            try:
                with patch("media_bot.download._execute_provider", AsyncMock(side_effect=exec_side_effect)), \
                     patch("media_bot.download.media_files", return_value=[]):
                    with self.assertRaises(DownloadError) as ctx:
                        await download_media(link, tmp, 10)
                self.assertIn(expected.lower(), str(ctx.exception).lower())
            finally:
                for p in base.values():
                    p.stop()

        # oversize: create big file in target dir
        tmp = make_tmp(self)
        async def oversize_exec(name, _link, target, _max, *a):
            target.mkdir(parents=True, exist_ok=True)
            (target / "big.mp4").write_bytes(b"x" * 100)
            return 1, "too big"
        await run_case(tmp, oversize_exec, "yükleme")

        # bot check
        tmp = make_tmp(self)
        await run_case(tmp, AsyncMock(return_value=(1, "Sign in to confirm you're not a bot")), "bot")

        # rate limited instagram
        tmp = make_tmp(self)
        await run_case(tmp, AsyncMock(return_value=(1, "hourly request limit")), "sınır")


    async def test_threads_highlight_handoff(self):
        link = PostLink("threads", "https://www.threads.com/s/highlight:1", None)
        highlight = PostLink("instagram", "https://www.instagram.com/stories/highlights/1/", None)
        tmp = make_tmp(self)
        prov_dir = tmp / "embed"
        prov_dir.mkdir()
        (prov_dir / "a.mp4").write_bytes(b"v")
        with patch("media_bot.download.ensure_free_disk"), \
             patch("media_bot.download.MEDIA_CACHE", SimpleNamespace(enabled=False)), \
             patch("media_bot.download.HIGHLIGHTS", SimpleNamespace(enabled=False, cache=None)), \
             patch("media_bot.download.highlight_id", return_value=None), \
             patch("media_bot.download.highlight_post_link", return_value=highlight), \
             patch("media_bot.download.METRICS.record_winner"), \
             patch("media_bot.download.METRICS.record"), \
             patch("media_bot.download.BREAKERS.allow", return_value=True), \
             patch("media_bot.download.BREAKERS.success"), \
             patch("media_bot.download.BREAKERS.neutral"), \
             patch("media_bot.download.BREAKERS.failure", return_value=None), \
             patch("media_bot.download.BREAKERS.abandon"), \
             patch("media_bot.download.provider_memory.preferred", AsyncMock(return_value=None)), \
             patch("media_bot.download.provider_memory.ranked", AsyncMock(side_effect=lambda _p, names: list(names[:1]))), \
             patch("media_bot.download.provider_memory.hedge_seconds", return_value=0), \
             patch("media_bot.download.provider_memory.remember"), \
             patch("media_bot.download.provider_memory.forget"), \
             patch("media_bot.download._execute_provider", AsyncMock(return_value=(0, ""))), \
             patch("media_bot.download.media_files", return_value=[prov_dir / "a.mp4"]):
            files, provider = await download_media(link, tmp, 1000)
        self.assertTrue(files)


    async def test_cache_singleflight_follower_falls_through(self):
        link = PostLink("reddit", "https://www.reddit.com/r/a/comments/abc123/c", "abc123")
        tmp = make_tmp(self)
        prov_dir = tmp / "yt-dlp"
        prov_dir.mkdir()
        (prov_dir / "a.mp4").write_bytes(b"v")
        fut = asyncio.get_running_loop().create_future()
        fut.set_result(None)
        fake_cache = SimpleNamespace(
            enabled=True, key_for=lambda p,u,c:"k", get=lambda *a,**k: None, copy_to=lambda *a,**k: None,
            singleflight_start=AsyncMock(return_value=(False, fut)),
            singleflight_finish=AsyncMock(),
            put=lambda *a,**k: None,
        )
        with patch("media_bot.download.ensure_free_disk"), \
             patch("media_bot.download.MEDIA_CACHE", fake_cache), \
             patch("media_bot.download.HIGHLIGHTS", SimpleNamespace(enabled=False, cache=None)), \
             patch("media_bot.download.highlight_id", return_value=None), \
             patch("media_bot.download.highlight_post_link", return_value=None), \
             patch("media_bot.download.METRICS.record_winner"), \
             patch("media_bot.download.METRICS.record"), \
             patch("media_bot.download.BREAKERS.allow", return_value=True), \
             patch("media_bot.download.BREAKERS.success"), \
             patch("media_bot.download.BREAKERS.neutral"), \
             patch("media_bot.download.BREAKERS.failure", return_value=None), \
             patch("media_bot.download.BREAKERS.abandon"), \
             patch("media_bot.download.provider_memory.preferred", AsyncMock(return_value=None)), \
             patch("media_bot.download.provider_memory.ranked", AsyncMock(side_effect=lambda _p,names:list(names[:1]))), \
             patch("media_bot.download.provider_memory.hedge_seconds", return_value=0), \
             patch("media_bot.download.provider_memory.remember"), \
             patch("media_bot.download.provider_memory.forget"), \
             patch("media_bot.download._execute_provider", AsyncMock(return_value=(0, ""))), \
             patch("media_bot.download.media_files", return_value=[prov_dir / "a.mp4"]):
            files, provider = await download_media(link, tmp, 1000)
        self.assertTrue(files)


    async def test_streaming_attempt_emits_files(self):
        link = PostLink("reddit", "https://www.reddit.com/r/a/comments/abc123/c", "abc123")
        tmp = make_tmp(self)
        emitted = []

        async def on_file(files):
            emitted.extend(files)

        async def exec_provider(name, _link, target, _max, *a):
            target.mkdir(parents=True, exist_ok=True)
            f = target / "a.mp4"
            f.write_bytes(b"video")
            await asyncio.sleep(0.1)
            return 0, ""

        with patch("media_bot.download.ensure_free_disk"), \
             patch("media_bot.download.MEDIA_CACHE", SimpleNamespace(enabled=False)), \
             patch("media_bot.download.HIGHLIGHTS", SimpleNamespace(enabled=False, cache=None)), \
             patch("media_bot.download.highlight_id", return_value=None), \
             patch("media_bot.download.highlight_post_link", return_value=None), \
             patch("media_bot.download.METRICS.record_winner"), \
             patch("media_bot.download.METRICS.record"), \
             patch("media_bot.download.BREAKERS.allow", return_value=True), \
             patch("media_bot.download.BREAKERS.success"), \
             patch("media_bot.download.BREAKERS.neutral"), \
             patch("media_bot.download.BREAKERS.failure", return_value=None), \
             patch("media_bot.download.BREAKERS.abandon"), \
             patch("media_bot.download.provider_memory.preferred", AsyncMock(return_value=None)), \
             patch("media_bot.download.provider_memory.ranked", AsyncMock(side_effect=lambda _p, names: list(names[:1]))), \
             patch("media_bot.download.provider_memory.hedge_seconds", return_value=0), \
             patch("media_bot.download.provider_memory.remember"), \
             patch("media_bot.download.provider_memory.forget"), \
             patch("media_bot.download._execute_provider", side_effect=exec_provider), \
             patch("media_bot.download.media_files", side_effect=lambda folder, _m: [p for p in folder.rglob("*.mp4") if p.stat().st_size > 0]):
            files, provider = await download_media(link, tmp, 1000, on_file=on_file)
        self.assertTrue(files)


    async def test_attempt_oserror_and_exception(self):
        link = PostLink("reddit", "https://www.reddit.com/r/a/comments/abc123/c", "abc123")
        for exc in [OSError("disk"), RuntimeError("boom")]:
            tmp = make_tmp(self)
            patches = [
                patch("media_bot.download.ensure_free_disk"),
                patch("media_bot.download.MEDIA_CACHE", SimpleNamespace(enabled=False)),
                patch("media_bot.download.HIGHLIGHTS", SimpleNamespace(enabled=False, cache=None)),
                patch("media_bot.download.highlight_id", return_value=None),
                patch("media_bot.download.highlight_post_link", return_value=None),
                patch("media_bot.download.METRICS.record_winner"),
                patch("media_bot.download.METRICS.record"),
                patch("media_bot.download.BREAKERS.allow", return_value=True),
                patch("media_bot.download.BREAKERS.success"),
                patch("media_bot.download.BREAKERS.neutral"),
                patch("media_bot.download.BREAKERS.failure", return_value=None),
                patch("media_bot.download.BREAKERS.abandon"),
                patch("media_bot.download.provider_memory.preferred", AsyncMock(return_value=None)),
                patch("media_bot.download.provider_memory.ranked", AsyncMock(side_effect=lambda _p, names: list(names[:1]))),
                patch("media_bot.download.provider_memory.hedge_seconds", return_value=0),
                patch("media_bot.download.provider_memory.remember"),
                patch("media_bot.download.provider_memory.forget"),
                patch("media_bot.download._execute_provider", AsyncMock(side_effect=exc)),
                patch("media_bot.download.media_files", return_value=[]),
            ]
            [p.start() for p in patches]
            try:
                with self.assertRaises(DownloadError):
                    await download_media(link, tmp, 1000)
            finally:
                for p in patches:
                    p.stop()


    async def test_instagram_story_and_highlight_cache(self):
        from media_bot.download import download_media
        # simple story path: no highlight cache/quota
        link = PostLink("instagram", "https://www.instagram.com/stories/user/123/", None)
        tmp = make_tmp(self)
        prov_dir = tmp / "indown"
        prov_dir.mkdir()
        (prov_dir / "a.mp4").write_bytes(b"v")
        highlights = SimpleNamespace(enabled=False, cache=None, bucket=SimpleNamespace(wait_seconds=lambda: 0))
        with patch("media_bot.download.ensure_free_disk"), \
             patch("media_bot.download.MEDIA_CACHE", SimpleNamespace(enabled=False)), \
             patch("media_bot.download.HIGHLIGHTS", highlights), \
             patch("media_bot.download.highlight_id", return_value=None), \
             patch("media_bot.download.highlight_post_link", return_value=None), \
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
             patch("media_bot.download.provider_memory.forget"), \
             patch("media_bot.download._execute_provider", AsyncMock(return_value=(0, ""))), \
             patch("media_bot.download.media_files", return_value=[prov_dir / "a.mp4"]):
            files, provider = await download_media(link, tmp, 1000)
        self.assertTrue(files)

        # highlight cache hit
        link2 = PostLink("instagram", "https://www.instagram.com/stories/highlights/1/", None)
        tmp2 = make_tmp(self)
        cached = SimpleNamespace(provider="indownio")
        def copy_to(_e, folder): (folder / "h.mp4").write_bytes(b"v")
        hl_cache = SimpleNamespace(get=lambda *a,**k: cached, copy_to=copy_to, singleflight_start=AsyncMock(), singleflight_finish=AsyncMock(), put=lambda *a,**k: None)
        highlights2 = SimpleNamespace(enabled=True, cache=hl_cache, user_quota=None, bucket=SimpleNamespace(wait_seconds=lambda: 0, try_consume=lambda: (True,0), mark_exhausted=lambda: 0), probe_lock=asyncio.Lock())
        with patch("media_bot.download.ensure_free_disk"), \
             patch("media_bot.download.MEDIA_CACHE", SimpleNamespace(enabled=False)), \
             patch("media_bot.download.HIGHLIGHTS", highlights2), \
             patch("media_bot.download.highlight_id", return_value="1"), \
             patch("media_bot.download.highlight_post_link", return_value=None), \
             patch("media_bot.download.METRICS.record_winner"), \
             patch("media_bot.download.METRICS.record"):
            files, provider = await download_media(link2, tmp2, 1000)
        self.assertEqual(provider, "indownio")


    async def test_highlight_leader_download_and_quota(self):
        from media_bot.download import download_media
        link = PostLink("instagram", "https://www.instagram.com/stories/highlights/1/", None)
        tmp = make_tmp(self)
        src = tmp / "indown"
        src.mkdir()
        (src / "a.mp4").write_bytes(b"v")
        hl_cache = SimpleNamespace(get=lambda *a,**k: None, copy_to=lambda *a,**k: None, singleflight_start=AsyncMock(return_value=(True, None)), singleflight_finish=AsyncMock(), put=AsyncMock())
        bucket = SimpleNamespace(try_consume=lambda: (True, 0), wait_seconds=lambda: 60, mark_exhausted=lambda: 60)
        highlights = SimpleNamespace(enabled=True, cache=hl_cache, user_quota=SimpleNamespace(aclaim=AsyncMock(return_value=(True, "")), arelease=AsyncMock()), bucket=bucket, probe_lock=asyncio.Lock())
        with patch("media_bot.download.ensure_free_disk"), \
             patch("media_bot.download.MEDIA_CACHE", SimpleNamespace(enabled=False)), \
             patch("media_bot.download.HIGHLIGHTS", highlights), \
             patch("media_bot.download.highlight_id", return_value="1"), \
             patch("media_bot.download.highlight_post_link", return_value=None), \
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
             patch("media_bot.download.provider_memory.forget"), \
             patch("media_bot.download._execute_provider", AsyncMock(return_value=(0, ""))), \
             patch("media_bot.download.media_files", return_value=[src / "a.mp4"]):
            files, provider = await download_media(link, tmp, 1000, user_id=123)
        self.assertTrue(files)
        hl_cache.singleflight_finish.assert_awaited()


    async def test_highlight_follower_and_quota_denied(self):
        from media_bot.download import download_media
        link = PostLink("instagram", "https://www.instagram.com/stories/highlights/1/", None)

        # follower gets cache after leader finishes
        tmp = make_tmp(self)
        cached = SimpleNamespace(provider="indownio")
        fut = asyncio.get_running_loop().create_future()
        fut.set_result(None)
        def copy_to(_e, folder): (folder / "h.mp4").write_bytes(b"v")
        hl_cache = SimpleNamespace(get=lambda *a,**k: cached, copy_to=copy_to, singleflight_start=AsyncMock(return_value=(False, fut)), singleflight_finish=AsyncMock(), put=AsyncMock())
        highlights = SimpleNamespace(enabled=True, cache=hl_cache, user_quota=None, bucket=SimpleNamespace(wait_seconds=lambda: 0, try_consume=lambda:(True,0), mark_exhausted=lambda:0), probe_lock=asyncio.Lock())
        with patch("media_bot.download.ensure_free_disk"), patch("media_bot.download.MEDIA_CACHE", SimpleNamespace(enabled=False)), patch("media_bot.download.HIGHLIGHTS", highlights), patch("media_bot.download.highlight_id", return_value="1"), patch("media_bot.download.highlight_post_link", return_value=None), patch("media_bot.download.METRICS.record_winner"), patch("media_bot.download.METRICS.record"):
            files, provider = await download_media(link, tmp, 1000)
        self.assertEqual(provider, "indownio")

        # leader quota denied
        tmp2 = make_tmp(self)
        hl_cache2 = SimpleNamespace(get=lambda *a,**k: None, copy_to=lambda *a,**k: None, singleflight_start=AsyncMock(return_value=(True, None)), singleflight_finish=AsyncMock(), put=AsyncMock())
        highlights2 = SimpleNamespace(enabled=True, cache=hl_cache2, user_quota=SimpleNamespace(aclaim=AsyncMock(return_value=(False, "limit doldu")), arelease=AsyncMock()), bucket=SimpleNamespace(wait_seconds=lambda:60, try_consume=lambda:(True,0), mark_exhausted=lambda:60), probe_lock=asyncio.Lock())
        with patch("media_bot.download.ensure_free_disk"), patch("media_bot.download.MEDIA_CACHE", SimpleNamespace(enabled=False)), patch("media_bot.download.HIGHLIGHTS", highlights2), patch("media_bot.download.highlight_id", return_value="1"), patch("media_bot.download.highlight_post_link", return_value=None), patch("media_bot.download.METRICS.record_winner"), patch("media_bot.download.METRICS.record"):
            with self.assertRaises(DownloadError) as ctx:
                await download_media(link, tmp2, 1000, user_id=5)
        self.assertIn("limit", str(ctx.exception).lower())



    async def test_highlight_indown_fallback_indownio(self):
        from media_bot.download import download_media
        link = PostLink("instagram", "https://www.instagram.com/stories/highlights/1/", None)
        tmp = make_tmp(self)
        src = tmp / "indownio"
        src.mkdir()
        (src / "a.mp4").write_bytes(b"v")
        hl_cache = SimpleNamespace(get=lambda *a,**k: None, copy_to=lambda *a,**k: None, singleflight_start=AsyncMock(return_value=(True, None)), singleflight_finish=AsyncMock(), put=AsyncMock())
        bucket = SimpleNamespace(try_consume=lambda: (True, 0), wait_seconds=lambda: 60, mark_exhausted=lambda: 60)
        highlights = SimpleNamespace(enabled=True, cache=hl_cache, user_quota=None, bucket=bucket, probe_lock=asyncio.Lock())

        async def exec_provider(name, _link, target, _max, *a):
            target.mkdir(parents=True, exist_ok=True)
            if name == "indown":
                return 1, "no media"
            if name == "indownio":
                (target / "a.mp4").write_bytes(b"v")
                return 0, ""
            return 1, "no"

        with patch("media_bot.download.ensure_free_disk"), patch("media_bot.download.MEDIA_CACHE", SimpleNamespace(enabled=False)), patch("media_bot.download.HIGHLIGHTS", highlights), patch("media_bot.download.highlight_id", return_value="1"), patch("media_bot.download.highlight_post_link", return_value=None), patch("media_bot.download.METRICS.record_winner"), patch("media_bot.download.METRICS.record"), patch("media_bot.download.BREAKERS.allow", return_value=True), patch("media_bot.download.BREAKERS.success"), patch("media_bot.download.BREAKERS.neutral"), patch("media_bot.download.BREAKERS.failure", return_value=None), patch("media_bot.download.BREAKERS.abandon"), patch("media_bot.download.provider_memory.preferred", AsyncMock(return_value=None)), patch("media_bot.download.provider_memory.ranked", AsyncMock(side_effect=lambda _p, names: list(names))), patch("media_bot.download.provider_memory.hedge_seconds", return_value=0), patch("media_bot.download.provider_memory.remember"), patch("media_bot.download.provider_memory.forget"), patch("media_bot.download._execute_provider", side_effect=exec_provider), patch("media_bot.download.media_files", side_effect=lambda folder,_m:[p for p in folder.rglob("*.mp4") if p.is_file()]):
            files, provider = await download_media(link, tmp, 1000)
        self.assertEqual(provider, "indownio")


    async def test_highlight_exhausted_and_follower_miss(self):
        from media_bot.download import download_media
        link = PostLink("instagram", "https://www.instagram.com/stories/highlights/1/", None)

        # bucket exhausted, wait > 300
        tmp = make_tmp(self)
        hl_cache = SimpleNamespace(get=lambda *a,**k: None, copy_to=lambda *a,**k: None, singleflight_start=AsyncMock(return_value=(True, None)), singleflight_finish=AsyncMock(), put=AsyncMock())
        bucket = SimpleNamespace(try_consume=lambda: (False, 600), wait_seconds=lambda: 600, mark_exhausted=lambda: 600)
        highlights = SimpleNamespace(enabled=True, cache=hl_cache, user_quota=None, bucket=bucket, probe_lock=asyncio.Lock())
        with patch("media_bot.download.ensure_free_disk"), patch("media_bot.download.MEDIA_CACHE", SimpleNamespace(enabled=False)), patch("media_bot.download.HIGHLIGHTS", highlights), patch("media_bot.download.highlight_id", return_value="1"), patch("media_bot.download.highlight_post_link", return_value=None), patch("media_bot.download.METRICS.record"), patch("media_bot.download.METRICS.record_winner"), patch("media_bot.download.BREAKERS.allow", return_value=True), patch("media_bot.download.BREAKERS.success"), patch("media_bot.download.BREAKERS.neutral"), patch("media_bot.download.BREAKERS.failure", return_value=None), patch("media_bot.download.BREAKERS.abandon"), patch("media_bot.download.provider_memory.preferred", AsyncMock(return_value=None)), patch("media_bot.download.provider_memory.ranked", AsyncMock(side_effect=lambda _p,names:list(names))), patch("media_bot.download.provider_memory.hedge_seconds", return_value=0), patch("media_bot.download.provider_memory.remember"), patch("media_bot.download.provider_memory.forget"), patch("media_bot.download._execute_provider", AsyncMock(return_value=(1,"no"))), patch("media_bot.download.media_files", return_value=[]):
            with self.assertRaises(DownloadError):
                await download_media(link, tmp, 1000)

        # follower miss falls through to normal download
        tmp2 = make_tmp(self)
        prov_dir = tmp2 / "yt-dlp"
        prov_dir.mkdir()
        (prov_dir / "a.mp4").write_bytes(b"v")
        fut = asyncio.get_running_loop().create_future()
        fut.set_result(None)
        hl_cache2 = SimpleNamespace(get=lambda *a,**k: None, copy_to=lambda *a,**k: None, singleflight_start=AsyncMock(return_value=(False, fut)), singleflight_finish=AsyncMock(), put=AsyncMock())
        highlights2 = SimpleNamespace(enabled=True, cache=hl_cache2, user_quota=None, bucket=SimpleNamespace(wait_seconds=lambda:0, try_consume=lambda:(True,0), mark_exhausted=lambda:0), probe_lock=asyncio.Lock())
        with patch("media_bot.download.ensure_free_disk"), patch("media_bot.download.MEDIA_CACHE", SimpleNamespace(enabled=False)), patch("media_bot.download.HIGHLIGHTS", highlights2), patch("media_bot.download.highlight_id", return_value="1"), patch("media_bot.download.highlight_post_link", return_value=None), patch("media_bot.download.METRICS.record_winner"), patch("media_bot.download.METRICS.record"), patch("media_bot.download.BREAKERS.allow", return_value=True), patch("media_bot.download.BREAKERS.success"), patch("media_bot.download.BREAKERS.neutral"), patch("media_bot.download.BREAKERS.failure", return_value=None), patch("media_bot.download.BREAKERS.abandon"), patch("media_bot.download.provider_memory.preferred", AsyncMock(return_value=None)), patch("media_bot.download.provider_memory.ranked", AsyncMock(side_effect=lambda _p,names:list(names[:1]))), patch("media_bot.download.provider_memory.hedge_seconds", return_value=0), patch("media_bot.download.provider_memory.remember"), patch("media_bot.download.provider_memory.forget"), patch("media_bot.download._execute_provider", AsyncMock(return_value=(0,""))), patch("media_bot.download.media_files", return_value=[prov_dir/"a.mp4"]):
            files, provider = await download_media(link, tmp2, 1000)
        self.assertTrue(files)


    async def test_streaming_race_two_providers(self):
        from media_bot.download import download_media
        link = PostLink("reddit", "https://www.reddit.com/r/a/comments/abc123/c", "abc123")
        tmp = make_tmp(self)
        emitted = []
        async def on_file(files):
            emitted.extend(files)

        async def exec_provider(name, _link, target, _max, *a):
            target.mkdir(parents=True, exist_ok=True)
            if name == "slow":
                await asyncio.sleep(0.5)
                (target / "a.mp4").write_bytes(b"slow")
                return 0, ""
            (target / "b.mp4").write_bytes(b"fast")
            await asyncio.sleep(0.05)
            return 0, ""

        with patch("media_bot.download.ensure_free_disk"), patch("media_bot.download.MEDIA_CACHE", SimpleNamespace(enabled=False)), patch("media_bot.download.HIGHLIGHTS", SimpleNamespace(enabled=False, cache=None)), patch("media_bot.download.highlight_id", return_value=None), patch("media_bot.download.highlight_post_link", return_value=None), patch("media_bot.download.METRICS.record_winner"), patch("media_bot.download.METRICS.record"), patch("media_bot.download.BREAKERS.allow", return_value=True), patch("media_bot.download.BREAKERS.success"), patch("media_bot.download.BREAKERS.neutral"), patch("media_bot.download.BREAKERS.failure", return_value=None), patch("media_bot.download.BREAKERS.abandon"), patch("media_bot.download.provider_memory.preferred", AsyncMock(return_value=None)), patch("media_bot.download.provider_memory.ranked", AsyncMock(return_value=["slow", "fast"])), patch("media_bot.download.provider_memory.hedge_seconds", return_value=0), patch("media_bot.download.provider_memory.remember"), patch("media_bot.download.provider_memory.forget"), patch("media_bot.download._execute_provider", side_effect=exec_provider), patch("media_bot.download.media_files", side_effect=lambda folder,_m:[p for p in folder.rglob("*.mp4") if p.is_file()]):
            files, provider = await download_media(link, tmp, 1000, on_file=on_file)
        self.assertTrue(files)
