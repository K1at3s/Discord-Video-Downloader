import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from media_bot import provider_memory
from media_bot.links import PostLink
from media_bot.provider_metrics import METRICS, ProviderMetrics


class ProviderMemoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        provider_memory.reset()

    def tearDown(self):
        provider_memory.reset()

    async def test_remembers_and_forgets(self):
        self.assertIsNone(await provider_memory.preferred("instagram:reel", "instagram", "reel"))
        provider_memory.remember("instagram:reel", "indown")
        self.assertEqual(await provider_memory.preferred("instagram:reel", "instagram", "reel"), "indown")
        provider_memory.forget("instagram:reel")
        self.assertIsNone(await provider_memory.preferred("instagram:reel", "instagram", "reel"))

    async def test_seeds_from_metrics_variant(self):
        with tempfile.TemporaryDirectory() as temp:
            metrics = ProviderMetrics(Path(temp) / "m.sqlite3")
            metrics.record_winner("instagram", "indownio", "highlight")
            metrics.record_winner("instagram", "embed", "post")
            with patch.object(METRICS, "path", metrics.path):
                self.assertEqual(
                    await provider_memory.preferred("instagram:highlight", "instagram", "highlight"),
                    "indownio",
                )
                self.assertEqual(
                    await provider_memory.preferred("instagram:post", "instagram", "post"),
                    "embed",
                )

    def test_hedge_seconds_env(self):
        with patch.dict(os.environ, {"PROVIDER_HEDGE_SECONDS": "3.5"}):
            self.assertEqual(provider_memory.hedge_seconds(), 3.5)
        with patch.dict(os.environ, {"PROVIDER_HEDGE_SECONDS": "0"}):
            self.assertEqual(provider_memory.hedge_seconds(), 0.0)
        with patch.dict(os.environ, {"PROVIDER_HEDGE_SECONDS": "nonsense"}):
            self.assertEqual(provider_memory.hedge_seconds(), 8.0)
        with patch.dict(os.environ, {"PROVIDER_HEDGE_SECONDS": ""}):
            self.assertEqual(provider_memory.hedge_seconds(5.0), 5.0)


class ProviderVariantTests(unittest.TestCase):
    def test_variants(self):
        from media_bot.download import provider_variant

        self.assertEqual(provider_variant(
            PostLink("instagram", "https://www.instagram.com/reel/abc/", "abc")), "reel")
        self.assertEqual(provider_variant(
            PostLink("instagram", "https://www.instagram.com/stories/user/123/", "123")), "story")
        self.assertEqual(provider_variant(
            PostLink("instagram", "https://www.instagram.com/stories/highlights/1802/", "1802")), "highlight")
        self.assertEqual(provider_variant(
            PostLink("instagram", "https://www.instagram.com/p/abc/", "abc")), "post")
        self.assertEqual(provider_variant(
            PostLink("threads", "https://www.threads.com/share/abc", "abc")), "short")
        self.assertEqual(provider_variant(
            PostLink("threads", "https://www.threads.com/@a/post/abc", "abc")), "post")
        self.assertEqual(provider_variant(PostLink("tiktok", "https://www.tiktok.com/@a/video/1", "1")), "")


if __name__ == "__main__":
    unittest.main()
