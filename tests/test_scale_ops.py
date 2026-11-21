import tempfile
import unittest
from pathlib import Path

from media_bot.sqlite_util import integrity_check, maintain_databases, open_db, vacuum
from media_bot.user_quota import GuildQuota


class SqliteMaintenanceTests(unittest.TestCase):
    def test_integrity_check_ok(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.sqlite3"
            db = open_db(path)
            try:
                db.execute("CREATE TABLE t(x INTEGER)")
                db.execute("INSERT INTO t(x) VALUES (1)")
                db.commit()
            finally:
                db.close()
            ok, detail = integrity_check(path)
            self.assertTrue(ok)
            self.assertEqual(detail, "ok")

    def test_vacuum_and_maintain(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.sqlite3"
            db = open_db(path)
            try:
                db.execute("CREATE TABLE t(x INTEGER)")
                db.execute("INSERT INTO t(x) VALUES (1)")
                db.commit()
            finally:
                db.close()
            self.assertTrue(vacuum(path))
            results = maintain_databases([path], vacuum_enabled=True)
            self.assertIn(str(path), results)
            self.assertTrue(results[str(path)]["integrity_ok"])
            self.assertTrue(results[str(path)]["vacuumed"])

    def test_maintain_skips_missing(self):
        results = maintain_databases([Path("/tmp/does-not-exist.sqlite3")])
        self.assertEqual(results, {})


class GuildQuotaTests(unittest.TestCase):
    def test_guild_quota_blocks_after_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bot.sqlite3"
            quota = GuildQuota(path, hourly_limit=2, daily_limit=0)
            self.assertTrue(quota.claim(123).allowed)
            self.assertTrue(quota.claim(123).allowed)
            decision = quota.claim(123)
            self.assertFalse(decision.allowed)
            self.assertEqual(decision.kind, "hourly")

    def test_guild_quota_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bot.sqlite3"
            quota = GuildQuota(path, hourly_limit=10, daily_limit=10)
            quota.claim(1)
            quota.claim(2)
            summary = quota.summary()
            self.assertEqual(summary["guilds"], 2)
            self.assertEqual(summary["hourly"], 2)
            self.assertEqual(summary["daily"], 2)


if __name__ == "__main__":
    unittest.main()
