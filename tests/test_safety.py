import asyncio
import unittest
from unittest.mock import patch

from media_bot.safety import ensure_public_url, is_safe_http_url


class SafetyTests(unittest.TestCase):
    def test_rejects_non_http_and_credentials(self):
        self.assertFalse(is_safe_http_url("file:///etc/passwd"))
        self.assertFalse(is_safe_http_url("ftp://example.com/x"))
        self.assertFalse(is_safe_http_url("https://user:pass@example.com/x"))

    def test_rejects_local_hosts_and_private_ips(self):
        for url in (
            "http://localhost/x",
            "http://app.localhost/x",
            "http://printer.local/x",
            "http://127.0.0.1/x",
            "http://10.0.0.5/x",
            "http://192.168.1.1/x",
            "http://169.254.169.254/latest/meta-data",
            "http://[::1]/x",
        ):
            self.assertFalse(is_safe_http_url(url), url)

    def test_accepts_public_http_urls(self):
        self.assertTrue(is_safe_http_url("https://x.com/user/status/1"))
        self.assertTrue(is_safe_http_url("http://93.184.216.34/x"))

    def test_rejects_unusual_ports(self):
        self.assertFalse(is_safe_http_url("http://example.com:8080/x"))
        self.assertTrue(is_safe_http_url("https://example.com:443/x"))

    def test_dns_resolution_must_be_public(self):
        async def run(address):
            answers = [(None, None, None, None, (address, 0))]
            with patch("media_bot.safety.socket.getaddrinfo", return_value=answers):
                return await ensure_public_url("https://example.com/x")

        self.assertTrue(asyncio.run(run("93.184.216.34")))
        self.assertFalse(asyncio.run(run("127.0.0.1")))
        self.assertFalse(asyncio.run(run("10.1.2.3")))
