import tempfile
import unittest
from pathlib import Path

from media_bot.highlight_state import HighlightState
from media_bot.provider_metrics import ProviderMetrics


class HighlightConfigTests(unittest.TestCase):
    def test_configure_respects_custom_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "bot.sqlite3"
            st = HighlightState()
            st.configure(
                db,
                daily_limit=7,
                hourly_limit=2,
                cache_ttl=3600,
                cache_max_bytes=1024 * 1024,
                health_check_enabled=False,
                health_check_interval=7200,
                health_check_reserve=5,
            )
            self.assertTrue(st.enabled)
            self.assertEqual(st.user_quota.daily_limit, 7)
            self.assertEqual(st.user_quota.hourly_limit, 2)
            self.assertEqual(st.cache.ttl, 3600)
            self.assertFalse(st.health_check_enabled)
            self.assertEqual(st.health_check_interval, 7200)
            self.assertEqual(st.health_check_reserve, 5)


class MetricsFormatTests(unittest.TestCase):
    def test_last_winner_tracks_most_recent_provider(self):
        with tempfile.TemporaryDirectory() as temp:
            m = ProviderMetrics(Path(temp) / "m.sqlite3")
            self.assertIsNone(m.last_winner("youtube"))
            m.record_winner("youtube", "cobalt")
            m.record_winner("youtube", "yt-dlp")
            m.record_winner("instagram", "indown")
            self.assertEqual(m.last_winner("youtube"), "yt-dlp")
            self.assertEqual(m.last_winner("instagram"), "indown")

    def test_last_winner_separates_variants_and_strips_cache_suffix(self):
        with tempfile.TemporaryDirectory() as temp:
            m = ProviderMetrics(Path(temp) / "m.sqlite3")
            m.record_winner("youtube", "cobalt", "mp3")
            m.record_winner("youtube", "yt-dlp", "720")
            m.record_winner("instagram", "indownio-cache", "highlight")
            self.assertEqual(m.last_winner("youtube", variant="mp3"), "cobalt")
            self.assertEqual(m.last_winner("youtube", variant="720"), "yt-dlp")
            self.assertIsNone(m.last_winner("youtube", variant="360"))
            self.assertEqual(m.last_winner("instagram", variant="highlight"), "indownio")

    def test_format_text_includes_quota_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            m = ProviderMetrics(Path(temp) / "m.sqlite3")
            m.record("instagram", "indownio", "quota", 100)
            m.record("instagram", "indown", "success", 200)
            text = m.format_text(24)
            self.assertIn("instagram/indownio", text)
            self.assertIn("kota=1", text)
            self.assertIn("Kota aşımı", text)


if __name__ == "__main__":
    unittest.main()
