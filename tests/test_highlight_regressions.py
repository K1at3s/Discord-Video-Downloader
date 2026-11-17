import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import discord

from media_bot.bot import MediaBot
from media_bot.download import DownloadError, download_media
from media_bot.highlight_state import HIGHLIGHTS, TokenBucket
from media_bot.links import PostLink


class BucketCleanupTests(unittest.TestCase):
    def test_old_events_cleaned_from_db_on_load(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "s.sqlite3"
            TokenBucket(capacity=20, period=60.0, db_path=db)
            old = time.time() - 7200
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.execute("INSERT INTO highlight_bucket_events(ts) VALUES (?)", (old,))
            b2 = TokenBucket(capacity=20, period=60.0, db_path=db)
            self.assertEqual(b2.remaining(now=time.time()), 20)
            with closing(sqlite3.connect(db)) as conn:
                rows = conn.execute("SELECT COUNT(*) FROM highlight_bucket_events").fetchone()[0]
            self.assertEqual(rows, 0)

    def test_old_events_cleaned_on_persist(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "s.sqlite3"
            b = TokenBucket(capacity=20, period=60.0, db_path=db)
            old = time.time() - 7200
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.execute("INSERT INTO highlight_bucket_events(ts) VALUES (?)", (old,))
            b.try_consume(now=time.time())
            with closing(sqlite3.connect(db)) as conn:
                old_rows = conn.execute(
                    "SELECT COUNT(*) FROM highlight_bucket_events WHERE ts <= ?",
                    (time.time() - 60.0,),
                ).fetchone()[0]
            self.assertEqual(old_rows, 0)


class CacheServeSizeRecheckTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._orig = (HIGHLIGHTS._configured, HIGHLIGHTS.bucket, HIGHLIGHTS.user_quota, HIGHLIGHTS.cache, HIGHLIGHTS.probe_lock)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db = Path(self.tmp.name) / "bot.sqlite3"
        cache_root = Path(self.tmp.name) / "cache"
        HIGHLIGHTS.configure(db, cache_root)

    async def asyncTearDown(self):
        (HIGHLIGHTS._configured, HIGHLIGHTS.bucket, HIGHLIGHTS.user_quota,
         HIGHLIGHTS.cache, HIGHLIGHTS.probe_lock) = self._orig

    async def test_cached_oversize_file_not_served_for_smaller_limit(self):
        hid = "17919681140787123"
        src = Path(self.tmp.name) / "src"
        src.mkdir()
        (src / "indownio_1.mp4").write_bytes(b"x" * 2048)
        self.assertIsNotNone(HIGHLIGHTS.cache.put(hid, src))
        link = PostLink("instagram", f"https://www.instagram.com/stories/highlights/{hid}/")

        async def fake_web(*_a, **_k):
            return 0, "no usable media"

        async def fake_io(*_a, **_k):
            return 0, "no usable media"

        async def fake_first(*_a, **_k):
            return None

        out = Path(self.tmp.name) / "out"
        out.mkdir()
        with (patch("media_bot.download.download_web", new=fake_web),
              patch("media_bot.download.download_indownio", new=fake_io),
              patch("media_bot.download.first_success", new=fake_first),
              patch("media_bot.download.BREAKERS.allow", return_value=True),
              patch("media_bot.download.BREAKERS.neutral"),
              patch("media_bot.download.BREAKERS.abandon"),
              patch("media_bot.download.METRICS.record"),
              patch("media_bot.download.METRICS.record_winner")):
            with self.assertRaises(DownloadError):
                await download_media(link, out, 100, user_id=1)


class DiscordTooLargeTests(unittest.IsolatedAsyncioTestCase):
    async def test_file_too_large_message(self):
        sent = []

        async def reply(_m, content="", **_kw):
            sent.append(content)
            return SimpleNamespace()

        class Slot:
            def slot(self, *_a, **_k):
                class Ctx:
                    async def __aenter__(self_): return self_
                    async def __aexit__(self_, exc_type, exc, tb): return False
                return Ctx()

        bot = SimpleNamespace(channel_ids=set(), max_links=3, _reply=reply, job_slots=Slot())

        msg = SimpleNamespace(author=SimpleNamespace(id=1, bot=False), guild=object(),
                              channel=SimpleNamespace(id=1), content="https://x.com/a/status/1", id=1)
        link = PostLink("x", "https://x.com/i/status/1", "1")

        err = discord.HTTPException(SimpleNamespace(status=413, reason="Payload Too Large"), "Payload Too Large")

        async def boom(_m, _l, **_kw):
            raise err

        bot._handle_link = boom
        with patch("media_bot.bot.find_links", return_value=[link]):
            await MediaBot.on_message(bot, msg)
        self.assertTrue(any("yükleme sınırını aşıyor" in s for s in sent))


if __name__ == "__main__":
    unittest.main()
