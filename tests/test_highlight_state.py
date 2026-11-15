import json
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from media_bot.highlight_state import (
    HighlightCache,
    HighlightUserQuota,
    TokenBucket,
    format_wait,
    highlight_id,
    istanbul_day,
    istanbul_hour,
)


class HighlightHelpersTests(unittest.TestCase):
    def test_highlight_id(self):
        self.assertEqual(
            highlight_id("https://www.instagram.com/stories/highlights/18023864549864045/"),
            "18023864549864045",
        )
        self.assertIsNone(highlight_id("https://www.instagram.com/stories/natgeo/123/"))
        self.assertIsNone(highlight_id("https://www.instagram.com/p/abc/"))

    def test_format_wait(self):
        self.assertEqual(format_wait(30), "1 dakika")
        self.assertEqual(format_wait(90), "2 dakika")
        self.assertEqual(format_wait(3600), "1 saat")
        self.assertEqual(format_wait(3660), "1 saat 1 dakika")

    def test_istanbul_keys(self):
        tz = timezone(timedelta(hours=3))
        now = datetime(2026, 11, 15, 14, 30, tzinfo=tz)
        self.assertEqual(istanbul_day(now), "2026-11-15")
        self.assertEqual(istanbul_hour(now), "2026-11-15T14")


class TokenBucketTests(unittest.TestCase):
    def test_consume_and_wait(self):
        b = TokenBucket(capacity=2, period=60.0)
        ok, wait = b.try_consume(now=100.0)
        self.assertTrue(ok)
        self.assertEqual(wait, 0.0)
        ok, _ = b.try_consume(now=101.0)
        self.assertTrue(ok)
        ok, wait = b.try_consume(now=102.0)
        self.assertFalse(ok)
        self.assertAlmostEqual(wait, 58.0)
        self.assertEqual(b.remaining(now=102.0), 0)
        ok, _ = b.try_consume(now=161.0)
        self.assertTrue(ok)

    def test_mark_exhausted(self):
        b = TokenBucket(capacity=3, period=60.0)
        b.try_consume(now=100.0)
        wait = b.mark_exhausted(now=110.0)
        self.assertGreater(wait, 0)
        self.assertEqual(b.remaining(now=110.0), 0)
        self.assertFalse(b.try_consume(now=110.0)[0])

    def test_persistence_across_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "state.sqlite3"
            b = TokenBucket(capacity=2, period=3600.0, db_path=db)
            now = time.time()
            self.assertTrue(b.try_consume(now=now)[0])
            self.assertTrue(b.try_consume(now=now + 1)[0])
            # restart loads events
            b2 = TokenBucket(capacity=2, period=3600.0, db_path=db)
            ok, _ = b2.try_consume(now=now + 2)
            self.assertFalse(ok)


class HighlightUserQuotaTests(unittest.TestCase):
    def test_hourly_and_daily_limits(self):
        tz = timezone(timedelta(hours=3))
        with tempfile.TemporaryDirectory() as temp:
            q = HighlightUserQuota(Path(temp) / "q.sqlite3", daily_limit=3, hourly_limit=2)
            t1 = datetime(2026, 11, 15, 10, 0, tzinfo=tz)
            ok, _ = q.claim(1, t1)
            self.assertTrue(ok)
            ok, _ = q.claim(1, t1)
            self.assertTrue(ok)
            ok, msg = q.claim(1, t1)
            self.assertFalse(ok)
            self.assertIn("saat", msg or "")
            # next hour allows (under daily)
            t2 = datetime(2026, 11, 15, 11, 0, tzinfo=tz)
            ok, _ = q.claim(1, t2)
            self.assertTrue(ok)
            ok, msg = q.claim(1, t2)  # daily now 3
            self.assertFalse(ok)
            self.assertIn("Günlük", msg or "")
            # other user unaffected
            ok, _ = q.claim(2, t1)
            self.assertTrue(ok)

    def test_release_and_retention(self):
        tz = timezone(timedelta(hours=3))
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "q.sqlite3"
            q = HighlightUserQuota(db, daily_limit=2, hourly_limit=2)
            t = datetime(2026, 11, 15, 10, 0, tzinfo=tz)
            self.assertTrue(q.claim(1, t)[0])
            self.assertTrue(q.claim(1, t)[0])
            self.assertFalse(q.claim(1, t)[0])
            q.release(1, t)
            self.assertTrue(q.claim(1, t)[0])
            # old rows cleaned (>2 days)
            old = datetime(2026, 11, 10, 10, 0, tzinfo=tz)
            q.claim(1, old)
            q2 = HighlightUserQuota(db, daily_limit=5, hourly_limit=5)
            # cleanup ran on init/claim; ensure old period doesn't block
            self.assertTrue(q2.claim(1, t)[0])


class HighlightCacheTests(unittest.IsolatedAsyncioTestCase):
    def test_put_get_copy_and_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "cache"
            cache = HighlightCache(root, ttl=3600, max_bytes=1024 * 1024)
            src = Path(temp) / "src"
            src.mkdir()
            (src / "a.mp4").write_bytes(b"video")
            (src / "b.jpg").write_bytes(b"img")
            (src / "note.txt").write_text("ignore")
            entry = cache.put("123", src)
            self.assertIsNotNone(entry)
            self.assertEqual(sorted(entry.files), ["a.mp4", "b.jpg"])
            got = cache.get("123")
            self.assertIsNotNone(got)
            out = Path(temp) / "out"
            copied = cache.copy_to(got, out)
            self.assertEqual(sorted(p.name for p in copied), ["a.mp4", "b.jpg"])

    def test_expired_cache_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = HighlightCache(Path(temp) / "cache", ttl=10)
            src = Path(temp) / "src"
            src.mkdir()
            (src / "a.mp4").write_bytes(b"v")
            cache.put("1", src)
            meta = cache._meta_path("1")
            data = json.loads(meta.read_text())
            data["created_at"] = time.time() - 100
            meta.write_text(json.dumps(data))
            self.assertIsNone(cache.get("1"))
            self.assertFalse(cache._dir("1").exists())

    def test_size_limit_eviction(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = HighlightCache(Path(temp) / "cache", ttl=3600, max_bytes=10)
            for i, payload in enumerate([b"12345", b"67890", b"abcde"]):
                src = Path(temp) / f"src{i}"
                src.mkdir()
                (src / "a.bin.mp4").write_bytes(payload)  # suffix .mp4 to be included
                cache.put(str(i), src)
            # max_bytes small, only newest/some should remain
            cache.cleanup()
            remaining = [p.name for p in (Path(temp) / "cache").iterdir() if p.is_dir()]
            self.assertLessEqual(len(remaining), 2)

    def test_stats_and_clear(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = HighlightCache(Path(temp) / "cache", ttl=3600, max_bytes=1024 * 1024)
            src = Path(temp) / "src"
            src.mkdir()
            (src / "a.mp4").write_bytes(b"video")
            cache.put("123", src)
            stats = cache.stats()
            self.assertEqual(stats["entries"], 1)
            self.assertEqual(stats["files"], 1)
            self.assertEqual(stats["bytes"], 5)
            self.assertEqual(cache.clear(), 1)
            self.assertEqual(cache.stats()["entries"], 0)

    async def test_singleflight(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = HighlightCache(Path(temp) / "cache")
            leader, fut = await cache.singleflight_start("x")
            self.assertTrue(leader)
            leader2, fut2 = await cache.singleflight_start("x")
            self.assertFalse(leader2)
            self.assertIs(fut, fut2)
            await cache.singleflight_finish("x", "ok")
            self.assertEqual(await fut2, "ok")


if __name__ == "__main__":
    unittest.main()
