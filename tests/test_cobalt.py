import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from media_bot.cobalt import _extension, _same_instance, _stream_file, download_cobalt


class CobaltSafetyTests(unittest.TestCase):
    def test_tunnel_must_stay_on_configured_instance(self):
        base = "http://127.0.0.1:9000/"
        self.assertTrue(_same_instance("http://127.0.0.1:9000/tunnel?id=123", base))
        self.assertFalse(_same_instance("http://127.0.0.1:9999/tunnel", base))
        self.assertFalse(_same_instance("http://127.0.0.1.evil.test/tunnel", base))
        self.assertFalse(_same_instance("https://127.0.0.1:9000/tunnel", base))

    def test_output_extension_is_constrained(self):
        self.assertEqual(_extension("../../foo.exe", "photo"), ".jpg")
        self.assertEqual(_extension("clip.mp4", "video"), ".mp4")
        self.assertEqual(_extension("audio.mp3", "video"), ".mp3")


class CobaltDownloadTests(unittest.IsolatedAsyncioTestCase):
    def _tmp(self):
        d = Path(tempfile.mkdtemp(prefix="cobalt-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        return d

    async def test_stream_file(self):
        tmp = self._tmp()
        out = tmp / "a.mp4"

        class Resp:
            def raise_for_status(self): pass
            async def aiter_bytes(self, _):
                for c in [b"ab", b"cd"]:
                    yield c
        class CM:
            async def __aenter__(self): return Resp()
            async def __aexit__(self, *_a): return False
        client = SimpleNamespace(stream=lambda *_a, **_k: CM())
        ok = await _stream_file(client, "http://127.0.0.1:9000/tunnel?id=1", out, "http://127.0.0.1:9000/", 10)
        self.assertTrue(ok)
        self.assertEqual(out.read_bytes(), b"abcd")

        out2 = tmp / "b.mp4"
        ok = await _stream_file(client, "http://evil.test/x", out2, "http://127.0.0.1:9000/", 10)
        self.assertFalse(ok)

        class BigResp(Resp):
            async def aiter_bytes(self, _):
                yield b"123456"
        class BigCM:
            async def __aenter__(self): return BigResp()
            async def __aexit__(self, *_a): return False
        client2 = SimpleNamespace(stream=lambda *_a, **_k: BigCM())
        out3 = tmp / "c.mp4"
        self.assertFalse(await _stream_file(client2, "http://127.0.0.1:9000/t", out3, "http://127.0.0.1:9000/", 3))
        self.assertFalse(out3.exists())

    async def test_download_cobalt_paths(self):
        tmp = self._tmp()
        base = "http://127.0.0.1:9000/"

        class Client:
            def __init__(self, resp):
                self.resp = resp
                self.posts = []
            async def __aenter__(self): return self
            async def __aexit__(self, *_a): return False
            async def post(self, url, **kwargs):
                self.posts.append((url, kwargs))
                return self.resp

        tunnel_resp = SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {"status": "tunnel", "url": "/tunnel?id=1", "filename": "v.mp4"})
        with patch("media_bot.cobalt.shared_client", return_value=Client(tunnel_resp)), \
             patch("media_bot.cobalt._stream_file", AsyncMock(return_value=True)):
            count, err = await download_cobalt("https://x.com/a/status/1", tmp, 100, base)
        self.assertEqual((count, err), (1, ""))

        picker_resp = SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {"status": "picker", "picker": [{"url": "/a", "filename": "a.jpg", "type": "photo"}, {"url": "/b", "filename": "b.mp4", "type": "video"}]})
        with patch("media_bot.cobalt.shared_client", return_value=Client(picker_resp)), \
             patch("media_bot.cobalt._stream_file", AsyncMock(side_effect=[True, False])):
            count, err = await download_cobalt("https://x.com/a/status/1", tmp, 100, base)
        self.assertEqual(count, 0)
        self.assertIn("incomplete", err)

        SimpleNamespace(status_code=400, raise_for_status=lambda: (_ for _ in ()).throw(httpx.HTTPStatusError("bad", request=httpx.Request("POST", base), response=httpx.Response(400))), json=lambda: {"error": {"code": "content.empty"}})
        # 400 empty case handled before raise_for_status
        empty_resp = SimpleNamespace(status_code=400, raise_for_status=lambda: None, json=lambda: {"error": {"code": "content.empty"}})
        with patch("media_bot.cobalt.shared_client", return_value=Client(empty_resp)):
            count, err = await download_cobalt("https://x.com/a/status/1", tmp, 100, base)
        self.assertEqual((count, err), (0, "no usable media"))

        bad_resp = SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {"status": "error", "error": "nope"})
        with patch("media_bot.cobalt.shared_client", return_value=Client(bad_resp)):
            count, err = await download_cobalt("https://x.com/a/status/1", tmp, 100, base)
        self.assertEqual(count, 0)

        with patch("media_bot.cobalt.shared_client", side_effect=httpx.HTTPError("boom")):
            count, err = await download_cobalt("https://x.com/a/status/1", tmp, 100, base)
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
