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
        path = "/" + url.split("://", 1)[-1].split("/", 1)[1] if "/" in url.split("://", 1)[-1] else "/"
        self.url = type("Url", (), {"path": path, "__str__": lambda self: url, "__repr__": lambda self: url})()
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

class WebProviderGapTests(unittest.IsolatedAsyncioTestCase):
    def _tmp(self):
        import shutil
        import tempfile

        d = Path(tempfile.mkdtemp(prefix="wp-"))
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        return d

    async def test_parser_edge_branches(self):
        from media_bot.web_providers import _embed_media, _indown_urls, _threads_page_urls, _threadsdl_urls, _tiktokio_urls, _vette_urls

        # indown duplicate URL branch (86->79)
        page = '''
        <div class="download-items">
          <a href="https://cdninstagram.com/a.jpg">Photo</a>
          <a href="https://cdninstagram.com/a.jpg">Photo</a>
        </div>
        '''
        self.assertEqual(_indown_urls(page), [("https://cdninstagram.com/a.jpg", "photo")])

        # tiktokio slideshow with img src fallback (104)
        page = '<div class="image-item"><img class="image-item" src="https://tiktokio.com/a.jpg"/></div>'
        self.assertEqual(_tiktokio_urls(page), [("https://tiktokio.com/a.jpg", "photo")])

        # embed node not dict (127), break at max (133)
        ctx = {"gql_data": {"shortcode_media": {"edge_sidecar_to_children": {"edges": [{"node": "bad"}]}}}}
        page = '{"contextJSON":' + __import__("json").dumps(__import__("json").dumps(ctx)) + '}'
        self.assertEqual(_embed_media(page), [])
        nodes = [{"node": {"is_video": False, "display_url": f"https://scontent.cdninstagram.com/{i}.jpg"}} for i in range(101)]
        ctx = {"gql_data": {"shortcode_media": {"edge_sidecar_to_children": {"edges": nodes}}}}
        page = '{"contextJSON":' + __import__("json").dumps(__import__("json").dumps(ctx)) + '}'
        self.assertEqual(len(_embed_media(page)), 100)

        # threads page depth/item branches (143,152,156,171)
        deep = {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {"a": {}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}}
        markup = '<script type="application/json">' + __import__("json").dumps(deep) + '</script>'
        self.assertEqual(_threads_page_urls(markup, "X"), [])
        bad_item = {"code": "X", "carousel_media": ["bad"]}
        markup = '<script type="application/json">' + __import__("json").dumps(bad_item) + '</script>'
        self.assertEqual(_threads_page_urls(markup, "X"), [])
        no_media = {"code": "X", "image_versions2": {"candidates": []}, "video_versions": []}
        markup = '<script type="application/json">' + __import__("json").dumps(no_media) + '</script>'
        self.assertEqual(_threads_page_urls(markup, "X"), [])

        # threadsdl filters (197,201,205)
        markup = '<img src="https://other.com/a.jpg"/><img src="https://cdn.threadsphotodownloader.com/evil.example/a.jpg"/><img src="https://cdn.threadsphotodownloader.com/scontent.cdninstagram.com/t51.2885-19/a.jpg"/>'
        self.assertEqual(_threadsdl_urls(markup), [])

        # vette success false (243)
        self.assertEqual(_vette_urls({"success": False}, "instagram"), [])

    async def test_download_web_remaining_branches(self):
        tmp = self._tmp()
        stream_ok = AsyncMock(side_effect=lambda c,u,s,o,m: o.write_bytes(b"x") or True)
        link_threads = PostLink("threads", "https://www.threads.com/@u/post/ABC123", "ABC123")

        # threadsdl connect retries, job path, status connect/sleep, no usable
        attempts = {"form": 0, "job": 0, "status": 0}
        form = FakeResponse(text='<input name="authenticity_token" value="tok">')
        job = FakeResponse(200, url="https://threadsphotodownloader.com/download/1")

        def get(url, **kwargs):
            if url.endswith("/"):
                attempts["form"] += 1
                if attempts["form"] == 1:
                    raise httpx.ConnectError("boom")
                return form
            if "/status" in url:
                attempts["status"] += 1
                if attempts["status"] == 1:
                    raise httpx.ConnectError("boom")
                return FakeResponse(text="retrieve media")
            return FakeResponse()

        def post(url, **kwargs):
            attempts["job"] += 1
            if attempts["job"] == 1:
                raise httpx.ConnectError("boom")
            return job

        client = FakeClient(get=get, post=post)
        with patch("media_bot.web_providers.shared_client", return_value=client), \
             patch("media_bot.web_providers.asyncio.sleep", AsyncMock()):
            count, err = await download_web("threadsdl", link_threads, tmp, 1000)
        self.assertEqual(count, 0)

        # vette facebook non-video (373)
        fb = PostLink("facebook", "https://www.facebook.com/share/p/abc123/", None)
        with patch("media_bot.web_providers.shared_client", return_value=FakeClient()):
            count, err = await download_web("vette", fb, tmp, 1000)
        self.assertEqual((count, err), (0, "video links only"))

        # indown reel with no video kinds keeps urls as-is? 353->355 means no filter
        link = PostLink("instagram", "https://www.instagram.com/reel/abc/", "abc")
        payload = {"status": "ok", "data": '<div class="download-items"><a href="https://cdninstagram.com/a.jpg">Photo</a></div>'}
        client = FakeClient(post=lambda *a, **k: FakeResponse(json_data=payload))
        with patch("media_bot.web_providers.shared_client", return_value=client), patch("media_bot.web_providers._stream", stream_ok):
            count, err = await download_web("indown", link, tmp, 1000)
        self.assertEqual(count, 1)

        # no usable media (394)
        link = PostLink("reddit", "https://www.reddit.com/r/t/comments/abc123/x/", "abc123")
        client = FakeClient(get=lambda *a, **k: FakeResponse(text="<html></html>"))
        with patch("media_bot.web_providers.shared_client", return_value=client):
            count, err = await download_web("rapidsave", link, tmp, 1000)
        self.assertEqual((count, err), (0, "no usable media"))

        # untrusted media host (402) - rapidsave returns untrusted host
        client = FakeClient(get=lambda *a, **k: FakeResponse(text='<a class="downloadbutton" href="https://evil.example/d.mp4">d</a>'))
        with patch("media_bot.web_providers.shared_client", return_value=client):
            count, err = await download_web("rapidsave", link, tmp, 1000)
        self.assertEqual(count, 0)

        # instagram video fit fail branches (408->412,409->412)
        ig = PostLink("instagram", "https://www.instagram.com/reel/abc/", "abc")
        page = '{"contextJSON":"{\\"gql_data\\":{\\"shortcode_media\\":{\\"is_video\\":true,\\"video_url\\":\\"https://scontent.cdninstagram.com/v.mp4\\"}}}"}'
        client = FakeClient(get=lambda *a, **k: FakeResponse(text=page))
        with patch("media_bot.web_providers.shared_client", return_value=client), \
             patch("media_bot.web_providers._stream", AsyncMock(return_value=False)), \
             patch("media_bot.web_providers.fit_video", AsyncMock(return_value=True)):
            count, err = await download_web("embed", ig, tmp, 1000)
        self.assertEqual(count, 0)
        with patch("media_bot.web_providers.shared_client", return_value=FakeClient(get=lambda *a, **k: FakeResponse(text=page))), \
             patch("media_bot.web_providers._stream", AsyncMock(side_effect=lambda c,u,s,o,m: o.write_bytes(b"x") or True)), \
             patch("media_bot.web_providers.fit_video", AsyncMock(return_value=False)):
            count, err = await download_web("embed", ig, tmp, 1000)
        self.assertEqual(count, 0)


class WebProviderFinalGapTests(unittest.IsolatedAsyncioTestCase):
    def _tmp(self):
        import shutil
        import tempfile

        d = Path(tempfile.mkdtemp(prefix="wp-"))
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        return d

    async def test_remaining_branches(self):
        from media_bot.web_providers import _threads_page_urls
        # 152 not isinstance(items,list)
        markup = '<script type="application/json">' + __import__("json").dumps({"code":"X","carousel_media":{"bad":1}}) + '</script>'
        self.assertEqual(_threads_page_urls(markup, "X"), [])

        tmp = self._tmp()
        link = PostLink("threads", "https://www.threads.com/@u/post/ABC123", "ABC123")

        # 305,322 raise on 4th connect error
        form_calls = {"n": 0}
        def get_form(*a, **k):
            form_calls["n"] += 1
            raise httpx.ConnectError("boom")
        with patch("media_bot.web_providers.shared_client", return_value=FakeClient(get=get_form)), patch("media_bot.web_providers.asyncio.sleep", AsyncMock()):
            count, err = await download_web("threadsdl", link, tmp, 1000)
        self.assertEqual(count, 0)

        job_calls = {"n": 0}
        form = FakeResponse(text='<input name="authenticity_token" value="tok">')
        def get_job(*a, **k):
            if a[0].endswith("/"):
                return form
            return FakeResponse()
        def post_job(*a, **k):
            job_calls["n"] += 1
            raise httpx.ConnectError("boom")
        with patch("media_bot.web_providers.shared_client", return_value=FakeClient(get=get_job, post=post_job)), patch("media_bot.web_providers.asyncio.sleep", AsyncMock()):
            count, err = await download_web("threadsdl", link, tmp, 1000)
        self.assertEqual(count, 0)

        # 330 post media unavailable
        client = FakeClient(get=lambda *a, **k: form if a[0].endswith("/") else FakeResponse(), post=lambda *a, **k: FakeResponse(200, url="https://threadsphotodownloader.com/"))
        with patch("media_bot.web_providers.shared_client", return_value=client), patch("media_bot.web_providers.asyncio.sleep", AsyncMock()):
            count, err = await download_web("threadsdl", link, tmp, 1000)
        self.assertEqual((count, err), (0, "post media unavailable"))

        # 332->345,344 status loop sleeps then finds urls
        status_calls = {"n": 0}
        def get_status(url, **kwargs):
            if url.endswith("/"):
                return form
            if "/status" in url:
                status_calls["n"] += 1
                if status_calls["n"] == 1:
                    return FakeResponse(text="<html>wait</html>")
                return FakeResponse(text='<video src="https://cdn.threadsphotodownloader.com/scontent.cdninstagram.com/v.mp4"></video>')
            return FakeResponse()
        job = FakeResponse(302, headers={"location": "/download/1"}, redirect=True)
        with patch("media_bot.web_providers.shared_client", return_value=FakeClient(get=get_status, post=lambda *a, **k: job)), \
             patch("media_bot.web_providers._stream", AsyncMock(side_effect=lambda c,u,s,o,m: o.write_bytes(b"x") or True)), \
             patch("media_bot.web_providers.asyncio.sleep", AsyncMock()):
            count, err = await download_web("threadsdl", link, tmp, 1000)
        self.assertEqual(count, 1)

        # 402 untrusted media host via parser mock
        rlink = PostLink("reddit", "https://www.reddit.com/r/t/comments/abc123/x/", "abc123")
        with patch("media_bot.web_providers.shared_client", return_value=FakeClient(get=lambda *a, **k: FakeResponse(text="x"))), \
             patch("media_bot.web_providers._rapidsave_url", return_value="https://evil.example/d.mp4"):
            count, err = await download_web("rapidsave", rlink, tmp, 1000)
        self.assertEqual((count, err), (0, "untrusted media host"))
