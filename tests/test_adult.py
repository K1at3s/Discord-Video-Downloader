import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from media_bot.adult import AdultQuota, channel_is_nsfw, reddit_adult_status
from media_bot.bot import MediaBot
from media_bot.links import PostLink
from media_bot.reddit_post import RedditPost


class QuotaTests(unittest.TestCase):
    def test_one_claim_per_user_per_istanbul_day_persists(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bot.sqlite3"
            store = AdultQuota(path)
            self.assertTrue(store.claim(1, "2026-11-12"))
            self.assertFalse(AdultQuota(path).claim(1, "2026-11-12"))
            self.assertTrue(store.claim(2, "2026-11-12"))
            self.assertTrue(store.claim(1, "2026-11-13"))
            store.release(1, "2026-11-12")
            self.assertTrue(store.claim(1, "2026-11-12"))

    def test_thread_inherits_parent_nsfw(self):
        channel = SimpleNamespace(is_nsfw=lambda: False,
                                  parent=SimpleNamespace(is_nsfw=lambda: True))
        self.assertTrue(channel_is_nsfw(channel))


class AdultGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_subreddit_flag_overrides_safe_post_flag(self):
        post = RedditPost("post", "", "example", "author",
                          "https://www.reddit.com/r/example/comments/abc123/post/", over_18=False)
        client_type = httpx.AsyncClient

        def client_factory(**kwargs):
            transport = httpx.MockTransport(lambda request: httpx.Response(
                200, json={"data": {"over18": True}}))
            return client_type(transport=transport, **kwargs)

        with patch("media_bot.adult.httpx.AsyncClient", side_effect=client_factory):
            self.assertTrue(await reddit_adult_status(post))

    async def test_adult_post_requires_nsfw_and_daily_quota(self):
        link = PostLink("reddit", "https://www.reddit.com/r/example/comments/abc123/post/", "abc123")
        post = RedditPost("post", "", "example", "author", link.url, over_18=True)
        sent = []
        delivered = []

        async def reply(_message, text):
            sent.append(text)

        async def fetch(*_args):
            return post

        async def deliver(*_args):
            delivered.append(True)

        with tempfile.TemporaryDirectory() as folder:
            quota = AdultQuota(Path(folder) / "bot.sqlite3")
            bot = SimpleNamespace(_reply=reply, adult_quota=quota)
            channel = SimpleNamespace(is_nsfw=lambda: False)
            message = SimpleNamespace(channel=channel, author=SimpleNamespace(id=42))
            with patch("media_bot.bot.fetch_reddit_post", new=fetch), patch("media_bot.bot.reddit_adult_status", return_value=True), patch.object(MediaBot, "_deliver_reddit", new=deliver):
                await MediaBot._handle_reddit(bot, message, link)
                self.assertEqual(delivered, [])
                channel.is_nsfw = lambda: True
                await MediaBot._handle_reddit(bot, message, link)
                await MediaBot._handle_reddit(bot, message, link)
        self.assertEqual(delivered, [True])
        self.assertIn("NSFW", sent[0])
        self.assertIn("Günlük", sent[1])

    async def test_unknown_maturity_fails_closed(self):
        link = PostLink("reddit", "https://www.reddit.com/r/example/comments/abc123/post/", "abc123")
        sent = []

        async def reply(_message, text):
            sent.append(text)

        async def fetch(*_args):
            return None

        bot = SimpleNamespace(_reply=reply)
        with patch("media_bot.bot.fetch_reddit_post", new=fetch):
            await MediaBot._handle_reddit(bot, object(), link)
        self.assertIn("doğrulanamadı", sent[0])


if __name__ == "__main__":
    unittest.main()
