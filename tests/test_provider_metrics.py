import tempfile
import sqlite3
import time
from contextlib import closing
import unittest
from pathlib import Path
from unittest.mock import patch

from media_bot.download import download_media
from media_bot.links import PostLink
from media_bot.provider_metrics import ProviderMetrics


class ProviderMetricsTests(unittest.TestCase):
    def test_last_day_summary_keeps_provider_outcomes_without_urls(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "metrics.sqlite3"
            metrics = ProviderMetrics(path)
            metrics.record("instagram", "indown", "success", 1200)
            metrics.record("instagram", "indown", "fault", 9000)
            metrics.record("instagram", "indown", "skipped", 0)
            metrics.record_winner("instagram", "indown")
            self.assertEqual(metrics.summary(), [
                ("instagram", "indown", "fault", 1, 9000),
                ("instagram", "indown", "skipped", 1, 0),
                ("instagram", "indown", "success", 1, 1200),
            ])
            self.assertEqual(ProviderMetrics(path, initialize=False).summary(), metrics.summary())
            self.assertEqual(metrics.report(), [
                ("instagram", "indown", {"success": 1, "fault": 1, "skipped": 1,
                                          "wins": 1, "p50_ms": 1200, "p95_ms": 1200}),
            ])
            with self.assertRaises(ValueError):
                metrics.record("instagram", "indown", "unexpected", 1)

    def test_report_percentiles_and_retention(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "metrics.sqlite3"
            metrics = ProviderMetrics(path)
            for duration in (100, 200, 300, 400):
                metrics.record("tiktok", "direct", "success", duration)
            self.assertEqual(metrics.report()[0][2]["p50_ms"], 200)
            self.assertEqual(metrics.report()[0][2]["p95_ms"], 400)
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("UPDATE provider_attempts SET at = ?", (time.time() - 15 * 86400,))
                db.execute("INSERT INTO provider_winners VALUES (?, ?, ?)",
                           (time.time() - 15 * 86400, "tiktok", "direct"))
            ProviderMetrics(path)
            self.assertEqual(metrics.report(), [])

    def test_old_database_can_be_read_and_upgraded(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "metrics.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE provider_attempts (at REAL, platform TEXT, provider TEXT, "
                           "outcome TEXT, elapsed_ms INTEGER)")
                db.execute("INSERT INTO provider_attempts VALUES (?, ?, ?, ?, ?)",
                           (time.time(), "instagram", "indown", "success", 150))
            self.assertEqual(ProviderMetrics(path, initialize=False).report()[0][2]["wins"], 0)
            upgraded = ProviderMetrics(path)
            upgraded.record_winner("instagram", "indown")
            self.assertEqual(upgraded.report()[0][2]["wins"], 1)


class ProviderMetricsFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_download_records_completed_provider_attempt(self):
        link = PostLink("tiktok", "https://www.tiktok.com/@example/video/123")

        async def direct(_link, folder, _max_bytes):
            (folder / "video.mp4").write_bytes(b"video")
            return 1, ""

        with tempfile.TemporaryDirectory() as temporary:
            metrics = ProviderMetrics(Path(temporary) / "metrics.sqlite3")
            with patch("media_bot.download.METRICS", metrics), \
                    patch("media_bot.download.provider_order", return_value=("direct",)), \
                    patch("media_bot.download.download_direct", side_effect=direct):
                files, provider = await download_media(link, Path(temporary) / "media", 1024)
            self.assertEqual(provider, "direct")
            self.assertEqual(len(files), 1)
            self.assertEqual(metrics.summary()[0][:4], ("tiktok", "direct", "success", 1))
            self.assertEqual(metrics.report()[0][2]["wins"], 1)


if __name__ == "__main__":
    unittest.main()
