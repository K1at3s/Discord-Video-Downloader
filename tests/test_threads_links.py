import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from media_bot.links import parse_link
from media_bot.threads_links import (
    _threads_cookies,
    _threads_scoped,
    highlight_post_link,
    resolve_threads_link,
)


SHORT = "https://www.threads.com/share/BAaTVei4V4"
POST = "https://www.threads.com/@zehrayahsii0/post/DfotDeOCJ1t"
HIGHLIGHT_SHORT = "https://www.threads.com/s/highlight:18129769924466391"
HIGHLIGHT_GROUPS = "https://www.threads.com/share/highlight:18129769924466391"


class ThreadsHighlightTests(unittest.TestCase):
    def test_highlight_short_link_maps_to_instagram_highlight(self):
        link = highlight_post_link(HIGHLIGHT_SHORT)
        self.assertIsNotNone(link)
        self.assertEqual(link.platform, "instagram")
        self.assertEqual(
            link.url,
            "https://www.instagram.com/stories/highlights/18129769924466391/",
        )

    def test_highlight_token_with_trailing_slash(self):
        link = highlight_post_link("https://www.threads.com/s/highlight:18129769924466391/")
        self.assertIsNotNone(link)
        self.assertEqual(link.url, "https://www.instagram.com/stories/highlights/18129769924466391/")

    def test_share_variant_is_supported(self):
        link = highlight_post_link(HIGHLIGHT_GROUPS)
        self.assertIsNotNone(link)
        self.assertEqual(link.platform, "instagram")

    def test_post_link_is_not_rewritten(self):
        link = highlight_post_link(POST)
        self.assertIsNone(link)

    def test_empty_url(self):
        self.assertIsNone(highlight_post_link(""))


class ThreadsShareTests(unittest.IsolatedAsyncioTestCase):
    async def _resolve(self, response, cookie_file=None):
        async with httpx.AsyncClient(transport=httpx.MockTransport(response),
                                     follow_redirects=False) as client:
            return await resolve_threads_link(parse_link(SHORT), cookie_file=cookie_file,
                                              client=client)

    async def test_redirect_to_post(self):
        result = await self._resolve(lambda request: httpx.Response(302, headers={"Location": POST}),
                                     cookie_file=Path("/tmp/whatever.txt"))
        self.assertEqual(result.url, POST)

    async def test_canonical_meta_on_share_page(self):
        page = f'<html><meta property="og:url" content="{POST}?xmt=tracking"></html>'
        result = await self._resolve(lambda request: httpx.Response(200, text=page),
                                     cookie_file=Path("/tmp/whatever.txt"))
        self.assertEqual(result.url, POST)

    async def test_login_redirect_is_not_a_post(self):
        result = await self._resolve(lambda request: httpx.Response(
            302, headers={"Location": "https://www.threads.com/login/"}),
            cookie_file=Path("/tmp/whatever.txt"))
        self.assertIsNone(result)

    async def test_share_request_looks_like_a_browser_navigation(self):
        seen = {}

        def respond(request):
            seen["mode"] = request.headers.get("sec-fetch-mode")
            seen["accept"] = request.headers.get("accept", "")
            return httpx.Response(302, headers={"Location": POST})

        result = await self._resolve(respond, cookie_file=Path("/tmp/whatever.txt"))
        self.assertEqual(result.url, POST)
        self.assertEqual(seen["mode"], "navigate")
        self.assertIn("text/html", seen["accept"])

    async def test_foreign_redirect_is_rejected(self):
        result = await self._resolve(lambda request: httpx.Response(
            302, headers={"Location": "https://example.org/@user/post/ABC123"}),
            cookie_file=Path("/tmp/whatever.txt"))
        self.assertIsNone(result)

    async def test_share_passthrough_without_cookie_file(self):
        result = await self._resolve(lambda request: httpx.Response(302, headers={"Location": POST}))
        self.assertEqual(result.url, SHORT)

    async def test_configured_cookie_is_scoped_to_threads(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "cookies.txt"
            path.write_text("# Netscape HTTP Cookie File\n.threads.com\tTRUE\t/\tTRUE\t2147483647\tsessionid\ttestvalue\n")
            cookies = _threads_cookies(path)
            headers = []

            def respond(request):
                headers.append(request.headers.get("cookie", ""))
                return httpx.Response(200)

            async with httpx.AsyncClient(transport=httpx.MockTransport(respond), cookies=cookies) as client:
                await client.get("https://www.threads.com/share/example")
                await client.get("https://example.org/")
            self.assertEqual(headers, ["sessionid=testvalue", ""])


class ThreadsCookieScopeTests(unittest.TestCase):
    def test_instagram_session_is_copied_to_threads(self):
        cookies = httpx.Cookies()
        cookies.set("sessionid", "igvalue", domain=".instagram.com")
        scoped = _threads_scoped(cookies)
        names = {(cookie.domain, cookie.name): cookie.value for cookie in scoped.jar}
        self.assertEqual(names[(".threads.com", "sessionid")], "igvalue")
        self.assertEqual(names[(".threads.net", "sessionid")], "igvalue")

    def test_existing_threads_cookie_is_not_overwritten(self):
        cookies = httpx.Cookies()
        cookies.set("sessionid", "threadsvalue", domain=".threads.com")
        cookies.set("sessionid", "igvalue", domain=".instagram.com")
        scoped = _threads_scoped(cookies)
        names = {(cookie.domain, cookie.name): cookie.value for cookie in scoped.jar}
        self.assertEqual(names[(".threads.com", "sessionid")], "threadsvalue")
        self.assertEqual(len(scoped.jar), 2)


if __name__ == "__main__":
    unittest.main()
