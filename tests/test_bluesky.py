import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from media_bot.bluesky import BlueskyPost, _api_get, _variants, _video_url, download_bluesky_media, parse_post
from media_bot.bot import MediaBot, media_gallery_view
from media_bot.links import parse_link


class BlueskyTests(unittest.TestCase):
    def test_image_gallery_and_quote(self):
        post = {
            "author": {"displayName": "Ada", "handle": "ada.bsky.social"},
            "record": {"text": "Açıklama"},
            "embed": {
                "$type": "app.bsky.embed.recordWithMedia#view",
                "media": {"$type": "app.bsky.embed.images#view", "images": [
                    {"fullsize": "https://cdn.bsky.app/img/one@jpeg", "alt": "Birinci görsel"},
                    {"fullsize": "https://cdn.bsky.app/img/two@jpeg"},
                    {"fullsize": "https://cdn.bsky.app/img/three@jpeg"},
                    {"fullsize": "https://cdn.bsky.app/img/four@jpeg"},
                ]},
                "record": {"record": {"text": "Alıntılanan metin"},
                           "author": {"displayName": "Berke"}},
            },
            "labels": [],
        }
        parsed = parse_post({"thread": {"post": post}})
        self.assertEqual(len(parsed.media), 4)
        self.assertIn("Ada (@ada.bsky.social)", parsed.caption)
        self.assertIn("Berke\nAlıntılanan metin", parsed.caption)
        self.assertEqual(parsed.alt_texts[0], "Birinci görsel")
        view = media_gallery_view("bluesky", [Path(f"{name}.jpg") for name in ("one", "two", "three", "four")], parsed.caption,
                                  "https://bsky.app/profile/ada.bsky.social/post/3abc123",
                                  descriptions=list(parsed.alt_texts))
        gallery = view.to_components()[0]["components"][1]
        self.assertEqual(len(gallery["items"]), 4)
        self.assertEqual(gallery["items"][0]["description"], "Birinci görsel")
        self.assertEqual(view.to_components()[0]["components"][-1]["components"][0]["url"],
                         "https://bsky.app/profile/ada.bsky.social/post/3abc123")

    def test_video_and_adult_label(self):
        parsed = parse_post({"thread": {"post": {
            "author": {"handle": "example.bsky.social"}, "record": {"text": "Video"},
            "labels": [{"val": "sexual"}],
            "embed": {"$type": "app.bsky.embed.video#view",
                      "playlist": "https://video.bsky.app/watch/did:plc:abc/cid/playlist.m3u8"},
        }}})
        self.assertTrue(parsed.adult)
        self.assertEqual(parsed.media[0][0], "video")
        self.assertFalse(_video_url("https://evil.example/playlist.m3u8"))

    def test_rejects_untrusted_image_and_missing_post(self):
        self.assertIsNone(parse_post({"thread": {"$type": "app.bsky.feed.defs#notFoundPost"}}))
        post = parse_post({"thread": {"post": {
            "record": {"text": "Merhaba"}, "embed": {"$type": "app.bsky.embed.images#view",
            "images": [{"fullsize": "https://cdn.bsky.app.evil.test/image"}]},
        }}})
        self.assertEqual(post.media, ())
        self.assertIn("Merhaba", post.caption)
        self.assertEqual(parse_link("https://bsky.app/profile/ada.bsky.social/post/3abc123").post_id, "3abc123")
        self.assertEqual(parse_link("https://bsky.app/profile/did:plc:abc123/post/3abc123").platform, "bluesky")

    def test_account_and_self_labels_and_missing_quote(self):
        parsed = parse_post({"thread": {"post": {
            "author": {"handle": "example.bsky.social", "labels": [{"val": "porn"}]},
            "record": {"text": "metin"},
            "embed": {"$type": "app.bsky.embed.record#view",
                      "record": {"$type": "app.bsky.embed.record#viewNotFound"}},
        }}})
        self.assertTrue(parsed.adult)
        self.assertIn("Alıntılanan gönderi kullanılamıyor", parsed.caption)
        self.assertTrue(parse_post({"thread": {"post": {
            "record": {"text": "x", "labels": {"values": [{"val": "nudity"}]}},
        }}}).adult)

    def test_hls_variants_reject_external_hosts_and_order_by_bandwidth(self):
        master = "https://video.bsky.app/watch/id/playlist.m3u8"
        content = ("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=4000000\n/high.m3u8\n"
                   "#EXT-X-STREAM-INF:BANDWIDTH=800000\n/low.m3u8\n"
                   "#EXT-X-STREAM-INF:BANDWIDTH=100000\nhttps://evil.test/other.m3u8")
        self.assertEqual(_variants(master, content),
                         ["https://video.bsky.app/high.m3u8", "https://video.bsky.app/low.m3u8"])


class BlueskyDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_only_post_has_one_source_card(self):
        fake_bot = SimpleNamespace(_reply=AsyncMock(), upload_bytes=1024)
        message = SimpleNamespace()
        link = parse_link("https://bsky.app/profile/example.bsky.social/post/3abc123")
        with patch("media_bot.bot.fetch_bluesky_post", new_callable=AsyncMock,
                   return_value=BlueskyPost("Yazar\n\nSadece metin", ())), \
                patch("media_bot.bot.download_bluesky_media", new_callable=AsyncMock,
                      return_value=[]):
            await MediaBot._handle_bluesky(fake_bot, message, link)
        self.assertEqual(fake_bot._reply.await_count, 1)
        view = fake_bot._reply.await_args.kwargs["view"]
        components = view.to_components()[0]["components"]
        self.assertIn("Sadece metin", components[2]["content"])
        self.assertEqual(components[-1]["components"][0]["url"], link.url)

    async def test_video_post_passes_mp4_in_same_reply_as_source_button(self):
        fake_bot = SimpleNamespace(_reply=AsyncMock(), upload_bytes=1024)
        message = SimpleNamespace()
        link = parse_link("https://bsky.app/profile/example.bsky.social/post/3abc123")
        observed = []

        async def download(_post, folder, _limit):
            output = folder / "bluesky_1.mp4"
            output.write_bytes(b"video")
            return [output]

        async def reply(_message, **kwargs):
            observed.append((kwargs["files"][0].is_file(), kwargs["view"].to_components()))

        fake_bot._reply.side_effect = reply
        with patch("media_bot.bot.fetch_bluesky_post", new_callable=AsyncMock,
                   return_value=BlueskyPost("Video açıklaması", (("video", "https://video.bsky.app/v.m3u8"),))), \
                patch("media_bot.bot.download_bluesky_media", side_effect=download):
            await MediaBot._handle_bluesky(fake_bot, message, link)
        self.assertEqual(fake_bot._reply.await_count, 1)
        self.assertTrue(observed[0][0])
        self.assertEqual(observed[0][1][0]["components"][1]["items"][0]["media"]["url"],
                         "attachment://bluesky_1.mp4")
        self.assertEqual(observed[0][1][0]["components"][-1]["components"][0]["url"], link.url)

    async def test_large_hls_video_tries_lower_variant_and_cleans_partial_file(self):
        master = "https://video.bsky.app/watch/id/playlist.m3u8"
        high = "https://video.bsky.app/watch/id/high.m3u8"
        low = "https://video.bsky.app/watch/id/low.m3u8"
        post = BlueskyPost("video", (("video", master),))
        attempts = []

        async def run(command, **_kwargs):
            attempts.append(command[6])
            output = Path(command[-1])
            output.write_bytes(b"oversize" if len(attempts) == 1 else b"mp4")
            return (-1, "geçici dosya sınırı aşıldı") if len(attempts) == 1 else (0, "")

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, _url):
                return httpx.Response(200, text="#EXTM3U", request=httpx.Request("GET", _url))

        with tempfile.TemporaryDirectory() as temporary, \
                patch("media_bot.bluesky._run", side_effect=run), \
                patch("media_bot.bluesky._variants", return_value=[high, low]), \
                patch("media_bot.bluesky.httpx.AsyncClient", return_value=Client()):
            files = await download_bluesky_media(post, Path(temporary), 5)
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_bytes(), b"mp4")
            self.assertEqual(attempts, [master, low])

    async def test_adult_post_is_not_sent_to_regular_channel(self):
        fake_bot = SimpleNamespace(_reply=AsyncMock())
        message = SimpleNamespace(channel=SimpleNamespace(is_nsfw=lambda: False))
        link = parse_link("https://bsky.app/profile/example.bsky.social/post/3abc123")
        with patch("media_bot.bot.fetch_bluesky_post", new_callable=AsyncMock,
                   return_value=BlueskyPost("adult", (), True)):
            await MediaBot._handle_bluesky(fake_bot, message, link)
        self.assertIn("NSFW", fake_bot._reply.await_args.args[1])

    async def test_second_rate_limit_gives_retry_message(self):
        fake_bot = SimpleNamespace(_reply=AsyncMock())
        message = SimpleNamespace()
        link = parse_link("https://bsky.app/profile/example.bsky.social/post/3abc123")
        request = httpx.Request("GET", "https://public.api.bsky.app/xrpc/test")
        response = httpx.Response(429, request=request)
        error = httpx.HTTPStatusError("rate limited", request=request, response=response)
        with patch("media_bot.bot.fetch_bluesky_post", new_callable=AsyncMock, side_effect=error):
            await MediaBot._handle_bluesky(fake_bot, message, link)
        self.assertIn("kısa süre sonra", fake_bot._reply.await_args.args[1])

    async def test_rate_limit_retries_once_with_bounded_delay(self):
        request = httpx.Request("GET", "https://public.api.bsky.app/xrpc/test")
        client = AsyncMock()
        client.get.side_effect = [
            httpx.Response(429, headers={"Retry-After": "30"}, request=request),
            httpx.Response(200, json={"ok": True}, request=request),
        ]
        with patch("media_bot.bluesky.asyncio.sleep", new_callable=AsyncMock) as sleep:
            response = await _api_get(client, "test", {})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get.await_count, 2)
        sleep.assert_awaited_once_with(3)

    async def test_incomplete_gallery_is_not_delivered(self):
        post = BlueskyPost("caption", (("image", "https://cdn.bsky.social/one"),
                                       ("image", "https://cdn.bsky.social/two")))
        async def stream(_client, _url, _platform, output, _max_bytes):
            if output.name.endswith("1.jpg"):
                output.write_bytes(b"image")
                return True
            return False
        with tempfile.TemporaryDirectory() as temporary, \
                patch("media_bot.bluesky._stream", side_effect=stream):
            folder = Path(temporary)
            self.assertEqual(await download_bluesky_media(post, folder, 1024), [])
            self.assertFalse((folder / "bluesky_1.jpg").exists())


if __name__ == "__main__":
    unittest.main()
