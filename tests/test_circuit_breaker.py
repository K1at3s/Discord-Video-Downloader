import asyncio
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from media_bot.circuit_breaker import CircuitBreaker, provider_fault
from media_bot.download import DownloadError, download_media
from media_bot.links import PostLink


class CircuitBreakerTests(unittest.TestCase):
    def test_faults_open_only_the_matching_provider_and_platform(self):
        now = [10.0]
        breaker = CircuitBreaker(max_failures=3, cooldown=120, clock=lambda: now[0])
        for _ in range(2):
            self.assertTrue(breaker.allow("indown", "instagram"))
            self.assertFalse(breaker.failure("indown", "instagram"))
        self.assertTrue(breaker.allow("indown", "instagram"))
        self.assertTrue(breaker.failure("indown", "instagram"))
        self.assertFalse(breaker.allow("indown", "instagram"))
        self.assertTrue(breaker.allow("indown", "tiktok"))
        self.assertTrue(breaker.allow("tiktokio", "instagram"))

    def test_half_open_allows_one_probe_and_reopens_after_failure(self):
        now = [10.0]
        breaker = CircuitBreaker(max_failures=1, cooldown=120, clock=lambda: now[0])
        self.assertTrue(breaker.allow("indown", "instagram"))
        breaker.failure("indown", "instagram")
        now[0] = 130.0
        self.assertTrue(breaker.allow("indown", "instagram"))
        self.assertFalse(breaker.allow("indown", "instagram"))
        breaker.failure("indown", "instagram")
        self.assertFalse(breaker.allow("indown", "instagram"))
        now[0] = 250.0
        self.assertTrue(breaker.allow("indown", "instagram"))
        breaker.success("indown", "instagram")
        self.assertTrue(breaker.allow("indown", "instagram"))

    def test_cancelled_probe_releases_half_open_slot(self):
        now = [10.0]
        breaker = CircuitBreaker(max_failures=1, cooldown=120, clock=lambda: now[0])
        breaker.failure("indown", "instagram")
        now[0] = 130.0
        self.assertTrue(breaker.allow("indown", "instagram"))
        breaker.abandon("indown", "instagram")
        self.assertTrue(breaker.allow("indown", "instagram"))

    def test_neutral_result_breaks_a_consecutive_fault_streak(self):
        breaker = CircuitBreaker(max_failures=2)
        breaker.failure("indown", "instagram")
        breaker.neutral("indown", "instagram")
        self.assertFalse(breaker.failure("indown", "instagram"))
        self.assertTrue(breaker.allow("indown", "instagram"))

    def test_provider_fault_classification(self):
        for error in (asyncio.TimeoutError(), ssl.SSLError("TLS"),
                      httpx.ConnectError("connection reset"), "HTTP Error 503",
                      "[SSL: INVALID_SESSION_ID] invalid session id", "zaman aşımı"):
            with self.subTest(error=repr(error)):
                self.assertTrue(provider_fault(error))
        for error in ("HTTP Error 403: Forbidden", "HTTP 404", "login required",
                      "This post is private", "HTTP 429", "no usable media", None):
            with self.subTest(error=repr(error)):
                self.assertFalse(provider_fault(error))


class CircuitBreakerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_download_skips_unhealthy_provider(self):
        breaker = CircuitBreaker(max_failures=2, cooldown=120)
        link = PostLink("tiktok", "https://www.tiktok.com/@example/video/123")
        with tempfile.TemporaryDirectory() as temporary, \
                patch("media_bot.download.BREAKERS", breaker), \
                patch("media_bot.download.provider_order", return_value=("direct",)), \
                patch("media_bot.download.download_direct", new_callable=AsyncMock, return_value=(0, "timed out")) as direct:
            for _ in range(3):
                with self.assertRaises(DownloadError):
                    await download_media(link, Path(temporary), 1024)
            self.assertEqual(direct.await_count, 2)

    async def test_threads_share_link_gets_actionable_error(self):
        link = PostLink("threads", "https://www.threads.com/share/BAaTVei4V4")
        with tempfile.TemporaryDirectory() as temporary, \
                patch("media_bot.download.provider_order", return_value=("direct",)), \
                patch("media_bot.download.download_direct", new_callable=AsyncMock,
                      return_value=(0, "no usable media")):
            with self.assertRaisesRegex(DownloadError, "paylaşım bağlantısı"):
                await download_media(link, Path(temporary), 1024)


if __name__ == "__main__":
    unittest.main()
