import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from media_bot import provider_memory
from media_bot.bot import MediaBot
from media_bot.http import close_shared_clients, shared_client
from media_bot.media_cache import MediaCache
from media_bot.provider_metrics import ProviderMetrics, _percentile
from media_bot.sqlite_util import open_db


class SharedClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await close_shared_clients()

    async def test_same_signature_reuses_one_client(self):
        async with shared_client(timeout=5, trust_env=False) as first:
            async with shared_client(timeout=5, trust_env=False) as second:
                self.assertIs(first, second)
                self.assertFalse(first.is_closed)

    async def test_different_signature_gets_its_own_client(self):
        async with shared_client(timeout=5) as first:
            async with shared_client(timeout=9) as second:
                self.assertIsNot(first, second)

    async def test_close_marks_clients_closed(self):
        async with shared_client(timeout=3) as client:
            pass
        await close_shared_clients()
        self.assertTrue(client.is_closed)


class ProviderSpeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.metrics = ProviderMetrics(Path(self.temp.name) / "metrics.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_p95_is_only_reported_with_enough_samples(self):
        for elapsed in (100, 110, 120, 130):
            self.metrics.record("instagram", "fast", "success", elapsed)
        self.assertGreater(self.metrics.p95_ms("instagram", "fast"), 0)
        self.assertEqual(self.metrics.p95_ms("instagram", "fast", min_samples=10), 0)
        self.assertEqual(self.metrics.p95_ms("tiktok", "fast"), 0)
        self.assertEqual(self.metrics.p95_ms("instagram", "missing"), 0)

    def test_ranking_puts_measured_fast_provider_first(self):
        for elapsed in (500, 520, 540, 560):
            self.metrics.record("instagram", "slow", "success", elapsed)
        for elapsed in (90, 95, 100, 105):
            self.metrics.record("instagram", "fast", "success", elapsed)
        with patch.object(provider_memory.METRICS, "successful_latencies",
                          return_value=self.metrics.successful_latencies("instagram")):
            ordered = asyncio.run(provider_memory.ranked(
                "instagram", ("slow", "unknown", "fast")))
        self.assertEqual(ordered, ["fast", "slow", "unknown"])

    def test_unknown_providers_keep_their_order(self):
        with patch.object(provider_memory.METRICS, "successful_latencies", return_value={}):
            ordered = asyncio.run(provider_memory.ranked("x", ("a", "b", "c")))
        self.assertEqual(ordered, ["a", "b", "c"])

    def test_hedge_uses_measured_p95(self):
        with patch.object(provider_memory.METRICS, "p95_ms", return_value=5000), \
                patch.dict(os.environ, {"PROVIDER_HEDGE_SECONDS": ""}):
            self.assertEqual(provider_memory.hedge_seconds(platform="instagram", provider="fast"), 6.0)

    def test_explicit_hedge_setting_wins(self):
        with patch.object(provider_memory.METRICS, "p95_ms", return_value=5000), \
                patch.dict(os.environ, {"PROVIDER_HEDGE_SECONDS": "2.5"}):
            self.assertEqual(provider_memory.hedge_seconds(platform="instagram", provider="fast"), 2.5)


class SqliteModeTests(unittest.TestCase):
    def test_open_db_enables_wal_and_busy_timeout(self):
        with tempfile.TemporaryDirectory() as temp:
            db = open_db(Path(temp) / "test.sqlite3")
            try:
                self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
                self.assertEqual(db.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
            finally:
                db.close()


class PayloadRetryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.bot = MediaBot(
            channel_ids=set(), upload_bytes=0, max_links=3, concurrency=2,
            cookie_file=None, cobalt_url=None, cobalt_key=None,
            gallery_rs_path=None, database_path=self.folder / "bot.sqlite3",
        )

    async def asyncTearDown(self):
        await self.bot.close()
        self.temp.cleanup()

    def _message(self):
        return SimpleNamespace(
            guild=SimpleNamespace(filesize_limit=10 * 1024 * 1024),
            reply=AsyncMock(),
            author=SimpleNamespace(id=1),
            channel=None,
        )

    async def test_413_retries_with_a_smaller_file(self):
        source = self.folder / "clip.mp4"
        source.write_bytes(b"x" * (3 * 1024 * 1024))

        async def fake_fit(source_path, target, budget, **kwargs):
            target.write_bytes(b"y" * 1024)
            return True

        response = SimpleNamespace(status=413, reason="Payload Too Large")
        error = discord.HTTPException(response, "Payload Too Large")
        message = self._message()
        message.reply.side_effect = [error, SimpleNamespace(id=1)]

        with patch("media_bot.bot.fit_video", side_effect=fake_fit):
            result = await self.bot._reply(message, files=[source])

        self.assertEqual(result.id, 1)
        self.assertEqual(message.reply.await_count, 2)
        retried = message.reply.await_args.kwargs["files"]
        self.assertEqual(len(retried), 1)
        self.assertTrue(retried[0].filename.endswith(".fit.mp4"))

    async def test_other_http_errors_are_not_retried(self):
        source = self.folder / "clip.mp4"
        source.write_bytes(b"x" * 1024)
        response = SimpleNamespace(status=500, reason="Server Error")
        error = discord.HTTPException(response, "Server Error")
        message = self._message()
        message.reply.side_effect = [error, SimpleNamespace(id=1)]
        with self.assertRaises(discord.HTTPException):
            await self.bot._reply(message, files=[source])
        self.assertEqual(message.reply.await_count, 1)

    async def test_small_non_video_files_are_not_reencoded(self):
        text = self.folder / "note.txt"
        text.write_bytes(b"x" * 10)
        self.assertIsNone(await self.bot._fit_attachments([text], self._message()))


class MediaCacheDefaultTests(unittest.TestCase):
    def test_default_ttl_is_six_hours(self):
        cache = MediaCache()
        self.assertEqual(cache.ttl, 6 * 3600)
        self.assertEqual(_percentile([1, 2, 3, 4, 5], 95), 5)


if __name__ == "__main__":
    unittest.main()
