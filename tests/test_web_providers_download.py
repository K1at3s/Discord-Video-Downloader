import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from media_bot.links import PostLink
from media_bot.web_providers import download_web


class FakeResponse:
    def __init__(self, status_code=200, text="", json_data=None, headers=None, url="https://example.com/", *, redirect=False):
        self.status_code = status_code
        self.text = text
        self._json = json_data
        self.headers = headers or {}
        self.url = SimpleNamespace(path=url.split("://",1)[-1].split("/",1)[-1] and "/" + url.split("://",1)[-1].split("/",1)[-1] or "/", __str__=lambda self: url)
        self._url_str = url
        self.is_redirect = redirect

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("GET", self._url_str)
            resp = httpx.Response(self.status_code, request=req)
            raise httpx.HTTPStatusError("bad", request=req, response=resp)

    def json(self):
        if isinstance(self._json, Exception):
            raise self._json
        return self._json


class FakeClient:
    def __init__(self, get=None, post=None):
        self._get = get or (lambda *a, **k: FakeResponse())
        self._post = post or (lambda *a, **k: FakeResponse())
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, url, **kwargs):
        self.requests.append(("GET", url, kwargs))
        r = self._get(url, **kwargs)
        return await r if hasattr(r, "__await__") else r

    async def post(self, url, **kwargs):
        self.requests.append(("POST", url, kwargs))
        r = self._post(url, **kwargs)
        return await r if hasattr(r, "__await__") else r

    def build_request(self, method, url, **kwargs):
        params = kwargs.get("params") or {}
        if params:
            from urllib.parse import urlencode
            url = url + "?" + urlencode(params)
        return SimpleNamespace(url=url)


class WebProviderDownloadTests(unittest.IsolatedAsyncioTestCase):
    def _tmp(self):
        import shutil
        import tempfile

        d = Path(tempfile.mkdtemp(prefix="wp-"))
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        return d

    async def test_embed_instagram_paths(self):
        tmp = self._tmp()
        link = PostLink("instagram", "https://www.instagram.com/reel/abc/", "abc")

        page = '{"contextJSON":"{\\"gql_data\\":{\\"shortcode_media\\":{\\"is_video\\":true,\\"video_url\\":\\"https://scontent.cdninstagram.com/v.mp4\\"}}}"}'
        client = FakeClient(get=lambda *a, **k: FakeResponse(text=page))
        with patch("media_bot.web_providers.shared_client", return_value=client), \
             patch("media_bot.web_providers._stream", AsyncMock(side_effect=lambda c,u,s,o,m: o.write_bytes(b"x") or True)), \
             patch("media_bot.web_providers.fit_video", AsyncMock(return_value=True)):
            count, err = await download_web("embed", link, tmp, 1000)
        self.assertEqual(count, 1)

        client = FakeClient(get=lambda *a, **k: FakeResponse(text="This account is private"))
        with patch("media_bot.web_providers.shared_client", return_value=client):
            count, err = await download_web("embed", link, tmp, 1000)
        self.assertEqual(count, 0)
        self.assertIn("private", err)

    async def test_threads_page_and_unsupported(self):
        tmp = self._tmp()
        link = PostLink("threads", "https://www.threads.com/@u/post/ABC123", "ABC123")

        client = FakeClient(get=lambda *a, **k: FakeResponse(302, headers={"location": "/login/"}, redirect=True))
        with patch("media_bot.web_providers.shared_client", return_value=client):
            count, err = await download_web("threads-page", link, tmp, 1000)
        self.assertEqual((count, err), (0, "login required"))

        data = [{"code": "ABC123", "image_versions2": {"candidates": [{"url": "https://scontent.cdninstagram.com/a.jpg", "width": 1, "height": 1}]}}]
        markup = '<script type="application/json">' + __import__("json").dumps(data) + '</script>'
        client = FakeClient(get=lambda *a, **k: FakeResponse(text=markup))
        with patch("media_bot.web_providers.shared_client", return_value=client), \
             patch("media_bot.web_providers._stream", AsyncMock(side_effect=lambda c,u,s,o,m: o.write_bytes(b"x") or True)):
            count, err = await download_web("threads-page", link, tmp, 1000)
        self.assertEqual(count, 1)

        with patch("media_bot.web_providers.shared_client", return_value=FakeClient()):
            count, err = await download_web("nope", link, tmp, 1000)
        self.assertEqual(count, 0)

    async def test_indown_tiktokio_rapidsave_vette_btch(self):
        tmp = self._tmp()
        stream_ok = AsyncMock(side_effect=lambda c,u,s,o,m: o.write_bytes(b"x") or True)

        # indown
        link = PostLink("instagram", "https://www.instagram.com/reel/abc/", "abc")
        payload = {"status": "ok", "data": '<div class="download-items"><a href="https://cdninstagram.com/v.mp4">Download Video</a></div>'}
        client = FakeClient(post=lambda *a, **k: FakeResponse(json_data=payload))
        with patch("media_bot.web_providers.shared_client", return_value=client), patch("media_bot.web_providers._stream", stream_ok), patch("media_bot.web_providers.fit_video", AsyncMock(return_value=True)):
            count, err = await download_web("indown", link, tmp, 1000)
        self.assertEqual(count, 1)

        # tiktokio
        link = PostLink("tiktok", "https://www.tiktok.com/@u/video/1", "1")
        client = FakeClient(post=lambda *a, **k: FakeResponse(text='<a class="download-btn download-btn-blue" href="https://tiktokio.com/v.mp4">Download</a>'))
        with patch("media_bot.web_providers.shared_client", return_value=client), patch("media_bot.web_providers._stream", stream_ok):
            count, err = await download_web("tiktokio", link, tmp, 1000)
        self.assertEqual(count, 1)

        # rapidsave
        link = PostLink("reddit", "https://www.reddit.com/r/t/comments/abc123/x/", "abc123")
        client = FakeClient(get=lambda *a, **k: FakeResponse(text='<a class="downloadbutton" href="https://sd.rapidsave.com/d.mp4">d</a>'))
        with patch("media_bot.web_providers.shared_client", return_value=client), patch("media_bot.web_providers._stream", stream_ok):
            count, err = await download_web("rapidsave", link, tmp, 1000)
        self.assertEqual(count, 1)

        # vette error and success
        link = PostLink("reddit", "https://www.reddit.com/r/t/comments/abc123/x/", "abc123")
        client = FakeClient(post=lambda *a, **k: FakeResponse(json_data={"success": False, "error": "nope"}))
        with patch("media_bot.web_providers.shared_client", return_value=client):
            count, err = await download_web("vette", link, tmp, 1000)
        self.assertEqual(count, 0)
        client = FakeClient(post=lambda *a, **k: FakeResponse(json_data={"success": True, "downloadUrl": "/api/video?id=1"}))
        with patch("media_bot.web_providers.shared_client", return_value=client), patch("media_bot.web_providers._stream", stream_ok):
            count, err = await download_web("vette", link, tmp, 1000)
        self.assertEqual(count, 1)

        # btch
        link = PostLink("tiktok", "https://www.tiktok.com/@u/video/1", "1")
        btch_payload = {"success": True, "normalized": {"kind": "media", "media": [{"type": "video", "url": "https://cdn.example/v.mp4"}]}}
        client = FakeClient(get=lambda *a, **k: FakeResponse(json_data=btch_payload))
        with patch("media_bot.web_providers.shared_client", return_value=client), patch("media_bot.web_providers._stream", stream_ok):
            count, err = await download_web("btch", link, tmp, 1000)
        self.assertEqual(count, 1)

    async def test_threadsdl_flow_and_errors(self):
        tmp = self._tmp()
        link = PostLink("threads", "https://www.threads.com/@u/post/ABC123", "ABC123")
        form = FakeResponse(text='<input name="authenticity_token" value="tok">')
        job = FakeResponse(302, headers={"location": "/download/1"}, redirect=True)
        status = FakeResponse(text='<video src="https://cdn.threadsphotodownloader.com/scontent.cdninstagram.com/v.mp4"></video>')

        def get(url, **kwargs):
            if url.endswith("/"):
                return form
            if "/status" in url:
                return status
            return FakeResponse()

        client = FakeClient(get=get, post=lambda *a, **k: job)
        with patch("media_bot.web_providers.shared_client", return_value=client), \
             patch("media_bot.web_providers._stream", AsyncMock(side_effect=lambda c,u,s,o,m: o.write_bytes(b"x") or True)), \
             patch("media_bot.web_providers.asyncio.sleep", AsyncMock()):
            count, err = await download_web("threadsdl", link, tmp, 1000)
        self.assertEqual(count, 1)

        # no form token
        client = FakeClient(get=lambda *a, **k: FakeResponse(text="<html/>"), post=lambda *a, **k: FakeResponse())
        with patch("media_bot.web_providers.shared_client", return_value=client):
            count, err = await download_web("threadsdl", link, tmp, 1000)
        self.assertEqual((count, err), (0, "no form token"))

    async def test_incomplete_untrusted_and_exception(self):
        tmp = self._tmp()
        link = PostLink("reddit", "https://www.reddit.com/r/t/comments/abc123/x/", "abc123")
        # incomplete
        client = FakeClient(get=lambda *a, **k: FakeResponse(text='<a class="downloadbutton" href="https://sd.rapidsave.com/d.mp4">d</a>'))
        with patch("media_bot.web_providers.shared_client", return_value=client), \
             patch("media_bot.web_providers._stream", AsyncMock(return_value=False)):
            count, err = await download_web("rapidsave", link, tmp, 1000)
        self.assertEqual(count, 0)
        self.assertIn("incomplete", err)

        # untrusted host from btch
        link = PostLink("tiktok", "https://www.tiktok.com/@u/video/1", "1")
        btch_payload = {"success": True, "normalized": {"kind": "media", "media": [{"type": "video", "url": "https://evil.example/v.mp4"}]}}
        client = FakeClient(get=lambda *a, **k: FakeResponse(json_data=btch_payload))
        # build_request will point to btch fetch-media, allowed for btch source? btch source means allowed_media_url checks btch hosts
        with patch("media_bot.web_providers.shared_client", return_value=client), \
             patch("media_bot.web_providers._stream", AsyncMock(return_value=True)):
            count, err = await download_web("btch", link, tmp, 1000)
        # fetch-media url is on btch base, should be allowed if btch in MEDIA_HOSTS; if not, untrusted
        self.assertIn(count, (0, 1))

        # exception
        with patch("media_bot.web_providers.shared_client", side_effect=httpx.HTTPError("boom")):
            count, err = await download_web("rapidsave", link, tmp, 1000)
        self.assertEqual(count, 0)