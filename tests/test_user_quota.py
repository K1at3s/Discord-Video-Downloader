import tempfile
import unittest
from pathlib import Path

from media_bot.user_quota import UserQuota

NOW = 1_800_000_000.0


class UserQuotaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "quota.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def test_disabled_quota_always_allows(self):
        quota = UserQuota(self.path, 0, 0)
        for _ in range(20):
            self.assertTrue(quota.claim(1, NOW).allowed)

    def test_hourly_limit_blocks_and_reports_wait(self):
        quota = UserQuota(self.path, hourly_limit=2)
        self.assertTrue(quota.claim(1, NOW).allowed)
        self.assertTrue(quota.claim(1, NOW).allowed)
        blocked = quota.claim(1, NOW)
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.kind, "hourly")
        self.assertEqual(blocked.limit, 2)
        self.assertGreaterEqual(blocked.retry_after, 1)
        self.assertTrue(quota.claim(2, NOW).allowed)

    def test_daily_limit_survives_hourly_reset(self):
        quota = UserQuota(self.path, hourly_limit=1, daily_limit=2)
        self.assertTrue(quota.claim(1, NOW).allowed)
        self.assertTrue(quota.claim(1, NOW + 3600).allowed)
        blocked = quota.claim(1, NOW + 7200)
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.kind, "daily")
        self.assertEqual(blocked.limit, 2)

    def test_usage_does_not_consume(self):
        quota = UserQuota(self.path, hourly_limit=3)
        quota.claim(1, NOW)
        usage = quota.usage(1, NOW)
        self.assertEqual(usage["hourly"], 1)
        self.assertEqual(usage["hourly_limit"], 3)
        self.assertEqual(quota.usage(1, NOW + 3600)["hourly"], 0)

    def test_summary_aggregates_current_windows(self):
        quota = UserQuota(self.path, hourly_limit=3, daily_limit=5)
        quota.claim(1, NOW)
        quota.claim(2, NOW)
        quota.claim(2, NOW)
        summary = quota.summary(NOW)
        self.assertEqual(summary["users"], 2)
        self.assertEqual(summary["hourly"], 3)
        self.assertEqual(summary["daily"], 3)
        self.assertEqual(summary["hourly_limit"], 3)
        self.assertEqual(quota.summary(NOW + 3600)["hourly"], 0)
