import unittest
from unittest.mock import AsyncMock, patch

import httpx

from media_bot.links import PostLink
from media_bot.sizes import _head_size, _length_from_headers, estimate_reddit_media_bytes, estimate_urls_bytes


class FakeResponse:
    def __init__(self, status_code=200, headers=None, *, redirect=False):
        self.status_code = status_code
        self.headers = headers or {}
        self.is_redirect = redirect

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=None, response=None)

    def json(self):
        return self._json

    @property
    def text(self):
        return ""


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def head(self, url):
        self.calls.append(("HEAD", url))
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


class SizesTests(unittest.IsolatedAsyncioTestCase):
    def test_length_from_headers(self):
        self.assertEqual(_length_from_headers({"content-length": "123"}), 123)
        self.assertEqual(_length_from_headers({"content-range": "bytes 0-0/456"}), 456)
        self.assertIsNone(_length_from_headers({"content-range": "bytes */*"}))

    async def test_head_size_branches(self):
        client = FakeClient([FakeResponse(200, {"content-length": "10"})])
        self.assertEqual(await _head_size(client, "https://example.com/a.mp4"), 10)

        client = FakeClient([FakeResponse(405), FakeResponse(206, {"content-range": "bytes 0-0/99"})])
        self.assertEqual(await _head_size(client, "https://example.com/a.mp4"), 99)

        redirect = FakeResponse(302, {"location": "https://example.com/b.mp4"}, redirect=True)
        client = FakeClient([redirect, FakeResponse(200, {"content-length": "5"})])
        self.assertEqual(await _head_size(client, "https://example.com/a.mp4"), 5)

        redirect_no_loc = FakeResponse(302, {}, redirect=True)
        client = FakeClient([redirect_no_loc])
        self.assertIsNone(await _head_size(client, "https://example.com/a.mp4"))

        client = FakeClient([FakeResponse(404)])
        self.assertIsNone(await _head_size(client, "https://example.com/a.mp4"))

        client = FakeClient([httpx.HTTPError("boom")])
        self.assertIsNone(await _head_size(client, "https://example.com/a.mp4"))

        self.assertIsNone(await _head_size(FakeClient([]), "ftp://example.com/a.mp4"))

    async def test_estimate_urls_bytes(self):
        fake = FakeClient([FakeResponse(200, {"content-length": "10"}), FakeResponse(200, {"content-length": "20"})])
        with patch("media_bot.sizes.shared_client", return_value=fake):
            total = await estimate_urls_bytes(["https://a/1", "https://a/1", "", "https://a/2"], max_urls=10)
        self.assertEqual(total, 30)

        self.assertIsNone(await estimate_urls_bytes([], max_urls=10))

        with patch("media_bot.sizes.shared_client", side_effect=OSError("boom")):
            self.assertIsNone(await estimate_urls_bytes(["https://a/1"]))

        bad = FakeClient([FakeResponse(404)])
        with patch("media_bot.sizes.shared_client", return_value=bad):
            self.assertIsNone(await estimate_urls_bytes(["https://a/1"]))

    async def test_estimate_reddit_media_bytes(self):
        link = PostLink("reddit", "https://reddit.com/r/x/comments/abc123/t/", "abc123")
        self.assertIsNone(await estimate_reddit_media_bytes(PostLink("x", "u")))

        payload = [{"data": {"children": [{"data": {"id": "1"}}]}}]

        class RedditClient(FakeClient):
            async def get(self, url, **kwargs):
                self.calls.append(("GET", url, kwargs))
                resp = FakeResponse(200)
                resp._json = payload
                return resp

        with patch("media_bot.sizes.shared_client", return_value=RedditClient([])), \
             patch("media_bot.direct_providers.reddit_media_urls", return_value=[("https://v.mp4", "video")]), \
             patch("media_bot.sizes.estimate_urls_bytes", AsyncMock(return_value=123)):
            self.assertEqual(await estimate_reddit_media_bytes(link), 123)

        with patch("media_bot.sizes.shared_client", side_effect=httpx.HTTPError("boom")):
            self.assertIsNone(await estimate_reddit_media_bytes(link))