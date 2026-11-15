import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from media_bot.request_log import RequestLog


def record(log, *, user_id=1, guild_id=10, platform="instagram", outcome="success", **kwargs):
    log.record(
        guild_id=guild_id,
        guild_name="Test Sunucu",
        channel_id=5,
        user_id=user_id,
        user_name=f"user{user_id}",
        platform=platform,
        url="https://example.com/p/1",
        outcome=outcome,
        **kwargs,
    )


class RequestLogTests(unittest.TestCase):
    def test_user_and_global_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            log = RequestLog(Path(temp) / "requests.sqlite3")
            record(log, outcome="success", elapsed_ms=1200, file_count=2, bytes=2048)
            record(log, outcome="failure")
            record(log, user_id=2, guild_id=20, platform="tiktok", outcome="success")

            user = log.user_summary(1, hours=24)
            self.assertEqual(user["total"], 2)
            self.assertEqual(user["success"], 1)
            self.assertEqual(user["failure"], 1)
            self.assertEqual(user["files"], 2)
            self.assertEqual(user["bytes"], 2048)
            self.assertIn("instagram", user["by_platform"])

            global_summary = log.global_summary(hours=24)
            self.assertEqual(global_summary["total"], 3)
            self.assertEqual(global_summary["success"], 2)
            self.assertEqual(global_summary["failure"], 1)
            self.assertEqual(global_summary["users"], 2)
            self.assertEqual(global_summary["guilds"], 2)

    def test_format_user_and_panel_include_percentages(self):
        with tempfile.TemporaryDirectory() as temp:
            log = RequestLog(Path(temp) / "requests.sqlite3")
            record(log, outcome="success", elapsed_ms=3000, file_count=1, bytes=1024)
            record(log, outcome="failure")
            user_text = log.format_user(1, 24, display_name="Test Kullanıcı")
            self.assertIn("Test Kullanıcı", user_text)
            self.assertIn("Toplam istek: 2", user_text)
            self.assertIn("Başarı oranı: %50.0", user_text)

            panel = log.format_panel(24, role="Yönetici")
            self.assertIn("Yönetim Paneli", panel)
            self.assertIn("Rol: Yönetici", panel)
            self.assertIn("Bakım: Kapalı", panel)
            self.assertIn("Başarı oranı: %50.0", panel)

    def test_pending_record_can_be_updated(self):
        with tempfile.TemporaryDirectory() as temp:
            log = RequestLog(Path(temp) / "requests.sqlite3")
            record_id = log.record(
                guild_id=1, guild_name="Sunucu", channel_id=2, user_id=7,
                user_name="user7", platform="youtube", url="https://youtu.be/x",
                outcome="pending",
            )
            self.assertIsNotNone(record_id)
            self.assertEqual(log.user_summary(7, 24)["pending"], 1)

            updated = log.update(record_id, outcome="success", detail="youtube:720",
                                 elapsed_ms=4200, file_count=1, bytes=9000)
            self.assertTrue(updated)
            summary = log.user_summary(7, 24)
            self.assertEqual(summary["pending"], 0)
            self.assertEqual(summary["success"], 1)
            self.assertEqual(summary["files"], 1)
            self.assertEqual(summary["bytes"], 9000)
            self.assertEqual(summary["avg_ms"], 4200)

    def test_update_missing_row_reports_false(self):
        with tempfile.TemporaryDirectory() as temp:
            log = RequestLog(Path(temp) / "requests.sqlite3")
            self.assertFalse(log.update(None, outcome="success"))
            self.assertFalse(log.update(9999, outcome="success"))

    def test_existing_database_is_migrated(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "requests.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute(
                    "CREATE TABLE request_log (at REAL NOT NULL, guild_id INTEGER, guild_name TEXT, "
                    "channel_id INTEGER, user_id INTEGER, user_name TEXT, platform TEXT NOT NULL, "
                    "url TEXT NOT NULL, outcome TEXT NOT NULL, detail TEXT)"
                )
            log = RequestLog(path)
            record(log, outcome="success", elapsed_ms=500, file_count=1, bytes=100)
            summary = log.user_summary(1, 24)
            self.assertEqual(summary["files"], 1)
            self.assertEqual(summary["bytes"], 100)
            self.assertEqual(summary["avg_ms"], 500)


if __name__ == "__main__":
    unittest.main()
