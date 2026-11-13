import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from media_bot.links import parse_link
from media_bot.threads_links import _threads_cookies, _threads_scoped, resolve_threads_link


SHORT = "https://www.threads.com/share/BAaTVei4V4"
POST = "https://www.threads.com/@zehrayahsii0/post/DfotDeOCJ1t"


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
