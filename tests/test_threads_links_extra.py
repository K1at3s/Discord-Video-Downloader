import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from httpx import MockTransport

from media_bot.links import PostLink
from media_bot.threads_links import (
    _post_link,
    _threads_cookies,
    _threads_scoped,
    highlight_post_link,
    resolve_threads_link,
)


POST = "https://www.threads.com/@user/post/ABC123"
SHARE = "https://www.threads.com/share/XYZ789"
CANONICAL = '<html><head><meta property="og:url" content="https://www.threads.com/@user/post/CANON1"></head></html>'


class PostLinkTests(unittest.TestCase):
    def test_post_link_is_valid(self):
        link = _post_link(POST)
        self.assertIsNotNone(link)
        self.assertEqual(link.url, POST)

    def test_highlight_token_is_not_a_post_link(self):
        self.assertIsNone(_post_link("https://www.threads.com/s/highlight:18129769924466391"))

    def test_share_link_is_not_a_post_link(self):
        self.assertIsNone(_post_link(SHARE))

    def test_non_threads_link_returns_none(self):
        self.assertIsNone(_post_link("https://instagram.com/p/abc"))


class HighlightTests(unittest.TestCase):
    def test_highlight_short_link_maps_to_instagram(self):
        result = highlight_post_link("https://www.threads.com/s/highlight:18129769924466391")
        self.assertIsNotNone(result)
        self.assertEqual(result.platform, "instagram")
        self.assertEqual(result.url, "https://www.instagram.com/stories/highlights/18129769924466391/")

    def test_empty_url_returns_none(self):
        self.assertIsNone(highlight_post_link(""))


class ResolveTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_post_link_is_returned(self):
        link = PostLink("threads", POST, "ABC123")
        result = await resolve_threads_link(link, cookie_file=None)
        self.assertEqual(result.url, POST)

    async def test_share_without_cookie_returns_same_link(self):
        link = PostLink("threads", SHARE, "XYZ789")
        result = await resolve_threads_link(link, cookie_file=None)
        self.assertEqual(result.url, SHARE)

    async def test_share_without_cookie_returns_same_link_using_mocktransport(self):
        async def respond(request):
            return httpx.Response(302, headers={"Location": POST})

        link = PostLink("threads", SHARE, "XYZ789")
        async with httpx.AsyncClient(transport=MockTransport(respond), follow_redirects=False) as client:
            result = await resolve_threads_link(link, cookie_file=None, client=client)
            self.assertEqual(result.url, SHARE)

    async def test_share_with_cookie_redirects_to_post(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "cookies.txt"
            path.write_text("# Netscape HTTP Cookie File\n.threads.com\tTRUE\t/\tTRUE\t2147483647\tsessionid\ttestvalue\n")

            async def respond(request):
                return httpx.Response(200, text=CANONICAL)

            link = PostLink("threads", SHARE, "XYZ789")
            async with httpx.AsyncClient(
                transport=MockTransport(respond),
                follow_redirects=False,
            ) as client:
                result = await resolve_threads_link(link, cookie_file=path, client=client)
                self.assertIsNotNone(result)
                self.assertIn("CANON1", result.url)

    async def test_share_with_cookie_canonical_meta(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "cookies.txt"
            path.write_text("# Netscape HTTP Cookie File\n.threads.com\tTRUE\t/\tTRUE\t2147483647\tsessionid\ttestvalue\n")

            async def respond(request):
                return httpx.Response(200, text=CANONICAL)

            link = PostLink("threads", SHARE, "XYZ789")
            async with httpx.AsyncClient(
                transport=MockTransport(respond),
                follow_redirects=False,
            ) as client:
                result = await resolve_threads_link(link, cookie_file=path, client=client)
                self.assertIsNotNone(result)
                self.assertIn("CANON1", result.url)

    async def test_share_with_cookie_foreign_redirect_is_rejected(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "cookies.txt"
            path.write_text("# Netscape HTTP Cookie File\n.threads.com\tTRUE\t/\tTRUE\t2147483647\tsessionid\ttestvalue\n")

            async def respond(request):
                return httpx.Response(
                    302,
                    headers={"Location": "https://example.org/@user/post/ABC123"},
                )

            link = PostLink("threads", SHARE, "XYZ789")
            async with httpx.AsyncClient(
                transport=MockTransport(respond),
                follow_redirects=False,
            ) as client:
                result = await resolve_threads_link(link, cookie_file=path, client=client)
                self.assertIsNone(result)


class CookieTests(unittest.TestCase):
    def test_raw_cookie_header_parsed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cookies.txt"
            path.write_text("Cookie: sessionid=abc123; csrftoken=xyz")
            cookies = _threads_cookies(path)
            self.assertIsNotNone(cookies)
            # Check via jar iteration (httpx.Cookies.__contains__ has quirks)
            names = {c.name for c in cookies.jar}
            self.assertIn("sessionid", names)
            self.assertIn("csrftoken", names)

    def test_raw_cookie_without_prefix(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cookies.txt"
            path.write_text("sessionid=abc123; csrftoken=xyz")
            cookies = _threads_cookies(path)
            self.assertIsNotNone(cookies)
            names = {c.name for c in cookies.jar}
            self.assertIn("sessionid", names)

    def test_empty_file_returns_none(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cookies.txt"
            path.write_text("")
            self.assertIsNone(_threads_cookies(path))

    def test_no_pairs_returns_none(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cookies.txt"
            path.write_text("sessionid=abc123; csrftoken=xyz")
            cookies = _threads_cookies(path)
            self.assertIsNotNone(cookies)

    def test_none_path_returns_none(self):
        self.assertIsNone(_threads_cookies(None))

    def test_instagram_session_is_copied_to_threads(self):
        cookies = httpx.Cookies()
        cookies.set("sessionid", "igvalue", domain=".instagram.com")
        scoped = _threads_scoped(cookies)
        names = {(cookie.domain, cookie.name): cookie.value for cookie in scoped.jar}
        self.assertIn((".threads.com", "sessionid"), names)
        self.assertIn((".threads.net", "sessionid"), names)


if __name__ == "__main__":
    unittest.main()