import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from media_bot.direct_providers import (
    _facebook_page,
    _stream,
    _suffix,
    allowed_media_url,
    canonical_facebook_post,
    download_direct,
    download_vxtwitter,
    facebook_media_urls,
    reddit_media_urls,
    tikwm_media_urls,
    vx_media_urls,
    x_media_urls,
)
from media_bot.links import parse_link
from media_bot.x_card import normalize_vxtwitter


class DirectProviderTests(unittest.TestCase):
    def test_media_hosts_are_exactly_allowlisted(self):
        self.assertTrue(allowed_media_url("https://video.twimg.com/a.mp4", "x"))
        self.assertFalse(allowed_media_url("https://video.twimg.com.evil.test/a.mp4", "x"))
        self.assertFalse(allowed_media_url("http://127.0.0.1/a.mp4", "reddit"))
        self.assertTrue(allowed_media_url("https://p16.tiktokcdn-us.com/a.jpg", "tiktok"))

    def test_x_selects_best_mp4(self):
        post = {"media": {"videos": [{"type": "video", "formats": [
            {"container": "mp4", "height": 360, "url": "https://video.twimg.com/low.mp4"},
            {"container": "mp4", "height": 720, "url": "https://video.twimg.com/high.mp4"},
        ]}]}}
        self.assertEqual(x_media_urls(post), [("https://video.twimg.com/high.mp4", "video")])

    def test_vxtwitter_text_and_quoted_media_can_make_card_and_download(self):
        payload = {"text": "Alıntı", "user_name": "Deneme", "user_screen_name": "test",
                   "date_epoch": 1794425157, "media_extended": [], "qrt": {
                       "text": "Video", "media_extended": [{"type": "video", "url": "https://video.twimg.com/clip.mp4"}]
                   }}
        post = normalize_vxtwitter(payload)
        self.assertEqual(post["author"]["screen_name"], "test")
        self.assertEqual(post["quote"]["text"], "Video")
        self.assertEqual(x_media_urls(post), [("https://video.twimg.com/clip.mp4", "video")])

    def test_vx_media_urls(self):
        payload = {"media_extended": [{"type": "video", "url": "https://video.twimg.com/clip.mp4"}]}
        self.assertEqual(vx_media_urls(payload), [("https://video.twimg.com/clip.mp4", "video")])

    def test_reddit_gallery_keeps_order(self):
        data = {"gallery_data": {"items": [{"media_id": "b"}, {"media_id": "a"}]}, "media_metadata": {
            "a": {"s": {"u": "https://i.redd.it/a.jpg"}},
            "b": {"s": {"u": "https://i.redd.it/b.jpg"}},
        }}
        self.assertEqual(reddit_media_urls(data), [("https://i.redd.it/b.jpg", "photo"), ("https://i.redd.it/a.jpg", "photo")])

    def test_tikwm_slideshow(self):
        self.assertEqual(tikwm_media_urls({"images": ["/a.jpg", "/b.jpg"]}), [
            ("https://www.tikwm.com/a.jpg", "photo"), ("https://www.tikwm.com/b.jpg", "photo")])

    def test_facebook_open_graph_photo(self):
        page = '<meta property="og:image" content="https://scontent.xx.fbcdn.net/photo.jpg">'
        self.assertEqual(facebook_media_urls(page, "/photos/abc/"), [("https://scontent.xx.fbcdn.net/photo.jpg", "photo")])


class RedditDirectTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_json_has_short_timeout(self):
        requests = []

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, url, **kwargs):
                requests.append(kwargs)
                return httpx.Response(403, request=httpx.Request("GET", url))

        link = parse_link("https://www.reddit.com/r/test/comments/abc123/post/")
        with patch("media_bot.direct_providers.shared_client", return_value=Client()):
            count, _ = await download_direct(link, Path("unused"), 1024)
        self.assertEqual(count, 0)
        self.assertEqual(requests[0]["timeout"].connect, 1)
        self.assertEqual(requests[0]["timeout"].read, 2)


class StreamResponse:
    def __init__(self, status_code=200, headers=None, chunks=None, *, redirect=False):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks or [b"data"]
        self.is_redirect = redirect

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("GET", "https://example.com")
            resp = httpx.Response(self.status_code, request=req)
            raise httpx.HTTPStatusError("bad", request=req, response=resp)

    async def aiter_bytes(self, _size):
        for chunk in self._chunks:
            yield chunk


class StreamCM:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *_args):
        return False


class DirectProviderExtraTests(unittest.IsolatedAsyncioTestCase):
    def test_suffix_and_allowed_url_edge(self):
        self.assertEqual(_suffix("https://x/a.png?x=1", "video"), ".png")
        self.assertEqual(_suffix("https://x/a", "video"), ".mp4")
        self.assertEqual(_suffix("https://x/a", "photo"), ".jpg")
        self.assertFalse(allowed_media_url("https://[bad", "x"))

    async def test_stream_success_redirect_and_failures(self):
        tmp = Path(self._tmp())
        out = tmp / "a.mp4"
        responses = [
            StreamResponse(302, {"location": "https://video.twimg.com/final.mp4"}, redirect=True),
            StreamResponse(200, {"content-type": "video/mp4", "content-length": "4"}, [b"abcd"]),
        ]

        class Client:
            def stream(self, *_args, **_kwargs):
                return StreamCM(responses.pop(0))

        ok = await _stream(Client(), "https://video.twimg.com/a.mp4", "x", out, 10)
        self.assertTrue(ok)
        self.assertEqual(out.read_bytes(), b"abcd")

        out2 = tmp / "b.mp4"
        bad_type = StreamResponse(200, {"content-type": "text/html"}, [b"x"])
        self.assertFalse(await _stream(type("C", (), {"stream": lambda *_a, **_k: StreamCM(bad_type)})(), "https://video.twimg.com/a.mp4", "x", out2, 10))

        out3 = tmp / "c.mp4"
        too_big = StreamResponse(200, {"content-length": "999"}, [b"x"])
        self.assertFalse(await _stream(type("C", (), {"stream": lambda *_a, **_k: StreamCM(too_big)})(), "https://video.twimg.com/a.mp4", "x", out3, 10))

        out4 = tmp / "d.mp4"
        chunk_big = StreamResponse(200, {}, [b"12345", b"67890"])
        self.assertFalse(await _stream(type("C", (), {"stream": lambda *_a, **_k: StreamCM(chunk_big)})(), "https://video.twimg.com/a.mp4", "x", out4, 5))
        self.assertFalse(out4.exists())

    def _tmp(self):
        import tempfile
        d = tempfile.mkdtemp(prefix="dp-")
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        return d

    async def test_facebook_page_and_canonical(self):
        class Client:
            def __init__(self, responses):
                self.responses = list(responses)
            async def __aenter__(self): return self
            async def __aexit__(self, *_a): return False
            async def get(self, url, **_kwargs):
                r = self.responses.pop(0)
                if isinstance(r, Exception):
                    raise r
                return r

        redir = httpx.Response(302, headers={"location": "https://www.facebook.com/p/1"}, request=httpx.Request("GET", "https://fb.watch/x"))
        ok = httpx.Response(200, text='<meta property="og:url" content="https://www.facebook.com/reel/1/">', request=httpx.Request("GET", "https://www.facebook.com/p/1"))
        page = await _facebook_page(Client([redir, ok]), "https://fb.watch/x")
        self.assertIn("og:url", page)

        link = parse_link("https://www.facebook.com/share/p/abc123/")
        with patch("media_bot.direct_providers.shared_client", return_value=Client([ok])):
            canonical = await canonical_facebook_post(link)
        self.assertIsNotNone(canonical)
        self.assertEqual(canonical.platform, "facebook")

        with patch("media_bot.direct_providers.shared_client", side_effect=httpx.HTTPError("boom")):
            self.assertIsNone(await canonical_facebook_post(link))

    def test_facebook_and_vx_parsers(self):
        page = '<meta property="og:video" content="https://video.xx.fbcdn.net/v/t42.mp4">'
        self.assertEqual(facebook_media_urls(page, "/reel/1/"), [("https://video.xx.fbcdn.net/v/t42.mp4", "video")])
        page2 = r'<script>{"browser_native_hd_url":"https:\/\/video.xx.fbcdn.net\/v\/hd.mp4"}</script>'
        self.assertEqual(facebook_media_urls(page2, "/reel/1/")[0][1], "video")
        self.assertEqual(vx_media_urls({"mediaURLs": ["https://video.twimg.com/a.mp4", "https://pbs.twimg.com/a.jpg"]}),
                         [("https://video.twimg.com/a.mp4", "video"), ("https://pbs.twimg.com/a.jpg", "photo")])

    async def test_download_direct_platforms_and_vxtwitter(self):
        tmp = Path(self._tmp())

        async def fake_stream(_client, url, _platform, output, _max):
            output.write_bytes(b"x")
            return True

        class CM:
            def __init__(self, **attrs):
                for k, v in attrs.items():
                    setattr(self, k, v)
            async def __aenter__(self): return self
            async def __aexit__(self, *_a): return False

        # x
        link = parse_link("https://x.com/u/status/1")
        with patch("media_bot.direct_providers.shared_client", return_value=CM()), \
             patch("media_bot.direct_providers.fetch_post", AsyncMock(return_value={"media": {"all": [{"type": "video", "url": "https://video.twimg.com/a.mp4"}]}})), \
             patch("media_bot.direct_providers._stream", side_effect=fake_stream):
            count, err = await download_direct(link, tmp, 100)
        self.assertEqual((count, err), (1, ""))

        # tiktok
        link = parse_link("https://www.tiktok.com/@u/video/1")
        resp = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"code": 0, "data": {"play": "/v.mp4"}})
        with patch("media_bot.direct_providers.shared_client", return_value=CM(post=AsyncMock(return_value=resp))), \
             patch("media_bot.direct_providers._stream", side_effect=fake_stream):
            count, err = await download_direct(link, tmp, 100)
        self.assertEqual(count, 1)

        # streamable
        link = parse_link("https://streamable.com/abc123")
        resp = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"files": {"mp4": {"url": "//cdn.streamable.com/video/mp4/abc.mp4"}}})
        with patch("media_bot.direct_providers.shared_client", return_value=CM(get=AsyncMock(return_value=resp))), \
             patch("media_bot.direct_providers._stream", side_effect=fake_stream):
            count, err = await download_direct(link, tmp, 100)
        self.assertEqual(count, 1)

        # unsupported
        link = parse_link("https://www.youtube.com/watch?v=abc123def45")
        with patch("media_bot.direct_providers.shared_client", return_value=CM()):
            count, err = await download_direct(link, tmp, 100)
        self.assertEqual(count, 0)

        # vxtwitter
        payload = {"media_extended": [{"type": "video", "url": "https://video.twimg.com/a.mp4"}]}
        resp = SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)
        with patch("media_bot.direct_providers.shared_client", return_value=CM(get=AsyncMock(return_value=resp))), \
             patch("media_bot.direct_providers._stream", side_effect=fake_stream):
            count, err = await download_vxtwitter("1", tmp, 100)
        self.assertEqual((count, err), (1, ""))


if __name__ == "__main__":
    unittest.main()
