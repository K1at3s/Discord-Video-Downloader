import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from media_bot.bot import MediaBot
from media_bot.links import PostLink


class XFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_tweet_card_is_sent_before_media(self):
        sent = []

        async def reply(_message, content, files, **_kwargs):
            self.assertTrue(all(path.is_file() for path in files))
            sent.append(content)

        async def fetch(_post_id):
            return {"text": "Deneme", "media": {"all": [{"url": "https://video.twimg.com/a.mp4"}]}}

        async def card(_post, output, _footer):
            output.write_bytes(b"PNG")

        async def download(_link, folder, *_args):
            await asyncio.sleep(0.01)
            folder.mkdir(parents=True, exist_ok=True)
            file = folder / "media.mp4"
            file.write_bytes(b"MP4")
            return [file], "test"

        bot = SimpleNamespace(upload_bytes=1024, cookie_file=None, cobalt_url=None,
                              cobalt_key=None, gallery_rs_path=None, _reply=reply)
        link = PostLink("x", "https://x.com/i/status/1234567890", "1234567890")
        with patch("media_bot.bot.fetch_post", new=fetch), patch("media_bot.bot.create_card", new=card), \
             patch("media_bot.bot.download_media", new=download):
            await MediaBot._handle_x(bot, object(), link)
        self.assertEqual(len(sent), 2)
        self.assertIn("X gönderisi", sent[0])
        self.assertIn("X medyası", sent[1])

    async def test_text_only_tweet_sends_card(self):
        sent = []

        async def reply(_message, content, files, **_kwargs):
            sent.append((content, len(files)))

        async def fetch(_post_id):
            return {"text": "Sadece metin", "media": {"all": []}}

        async def card(_post, output, _footer):
            output.write_bytes(b"PNG")

        async def download(*_args):
            await asyncio.sleep(10)
            raise AssertionError("Text-only media task should be cancelled")

        bot = SimpleNamespace(upload_bytes=1024, cookie_file=None, cobalt_url=None,
                              cobalt_key=None, gallery_rs_path=None, _reply=reply)
        link = PostLink("x", "https://x.com/i/status/1234567890", "1234567890")
        with patch("media_bot.bot.fetch_post", new=fetch), patch("media_bot.bot.create_card", new=card), \
             patch("media_bot.bot.download_media", new=download):
            await MediaBot._handle_x(bot, object(), link)
        self.assertEqual(sent, [("**X gönderisi**", 1)])

    async def test_text_only_tweet_never_starts_download(self):
        calls = []

        async def reply(_message, _content, _files, **_kwargs):
            pass

        async def fetch(_post_id):
            return {"text": "Sadece metin", "media": {}}

        async def card(_post, output, _footer):
            output.write_bytes(b"PNG")

        async def download(*args):
            calls.append(args)
            return [], "test"

        bot = SimpleNamespace(upload_bytes=1024, cookie_file=None, cobalt_url=None,
                              cobalt_key=None, gallery_rs_path=None, _reply=reply)
        link = PostLink("x", "https://x.com/i/status/1234567890", "1234567890")
        with patch("media_bot.bot.fetch_post", new=fetch), patch("media_bot.bot.create_card", new=card), \
             patch("media_bot.bot.download_media", new=download):
            await MediaBot._handle_x(bot, object(), link)
        self.assertEqual(calls, [])
