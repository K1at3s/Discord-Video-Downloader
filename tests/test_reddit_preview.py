import unittest
from pathlib import Path
from types import SimpleNamespace

import discord
from unittest.mock import patch

from media_bot.bot import MediaBot, reddit_embed_preview
from media_bot.links import PostLink
from media_bot.reddit_post import RedditPost


class RedditPreviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_short_text_post_prefers_full_body_over_discord_excerpt(self):
        link = PostLink("reddit", "https://www.reddit.com/r/opencode/s/ykPZRiQsc9")
        source = discord.Embed(title="DeepSeek V4.1 Flash", description="I'm the founder of Sable.",
                               url=link.url)
        preview = reddit_embed_preview(link, [source])
        self.assertEqual(preview.title, "r/opencode · DeepSeek V4.1 Flash")
        self.assertEqual(preview.description, "I'm the founder of Sable.")

        replies = []

        async def reply(_message, content="", files=None, *, embed=None, view=None):
            self.assertTrue(all(path.is_file() for path in files or []))
            replies.append((content, [path.name for path in files or []], embed, view))

        bot = SimpleNamespace(_reddit_message_preview=lambda *_: None, _reply=reply,
                              upload_bytes=1024, cookie_file=None, cobalt_url=None,
                              cobalt_key=None, gallery_rs_path=None)

        async def message_preview(*_):
            return preview

        bot._reddit_message_preview = message_preview

        async def no_media(*_args):
            self.fail("A text-only Reddit post must not start media providers")

        async def full_post(_link, _title):
            return RedditPost("DeepSeek V4.1 Flash", "I'm the founder of Sable.\n\nFull second paragraph.",
                              "opencode", "Secret-Builder-8249",
                              "https://www.reddit.com/r/opencode/comments/1xuc834/deepseek_v41_flash/",
                              is_self=True)

        with patch("media_bot.bot.download_media", new=no_media), patch("media_bot.bot.fetch_reddit_post", new=full_post), patch("media_bot.bot.reddit_adult_status", return_value=False):
            await MediaBot._handle_reddit(bot, object(), link)
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0][0], "")
        self.assertEqual(replies[0][1], ["reddit_01.png"])
        self.assertIsNone(replies[0][2])
        components = replies[0][3].to_components()[0]["components"]
        self.assertEqual([item["media"]["url"] for item in components[1]["items"]],
                         ["attachment://reddit_01.png"])
        self.assertIn("/comments/1xuc834/", components[-1]["components"][0]["url"])

    async def test_two_card_pages_share_one_media_gallery_message(self):
        link = PostLink("reddit", "https://www.reddit.com/r/opencode/s/ykPZRiQsc9")
        post = RedditPost("Long post", "Full body", "opencode", "someone",
                          "https://www.reddit.com/r/opencode/comments/1xuc834/post/", is_self=True)
        sent = []

        async def preview(*_args):
            return discord.Embed(title="Long post", url=link.url)

        async def full_post(*_args):
            return post

        async def reply(_message, content="", files=None, *, embed=None, view=None):
            sent.append(([path.name for path in files or []], view))

        def render(_post, folder: Path, _footer, *, preview_only=False):
            folder.mkdir(parents=True, exist_ok=True)
            pages = [folder / "reddit_01.png", folder / "reddit_02.png"]
            for page in pages:
                page.write_bytes(b"png")
            return pages

        bot = SimpleNamespace(_reddit_message_preview=preview, _reply=reply,
                              upload_bytes=1024, cookie_file=None, cobalt_url=None,
                              cobalt_key=None, gallery_rs_path=None)
        with patch("media_bot.bot.fetch_reddit_post", new=full_post), patch("media_bot.bot.render_reddit_cards", new=render), patch("media_bot.bot.reddit_adult_status", return_value=False):
            await MediaBot._handle_reddit(bot, object(), link)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], ["reddit_01.png", "reddit_02.png"])
        components = sent[0][1].to_components()[0]["components"]
        self.assertEqual([item["media"]["url"] for item in components[1]["items"]],
                         ["attachment://reddit_01.png", "attachment://reddit_02.png"])

    async def test_media_post_still_downloads_after_image_card(self):
        link = PostLink("reddit", "https://www.reddit.com/r/videos/comments/abc123/example/", "abc123")
        post = RedditPost("Video title", "", "videos", "someone", link.url, is_self=False)
        sent: list[list[str]] = []

        async def reply(_message, content="", files=None, *, embed=None, view=None):
            sent.append([path.name for path in files or []])

        async def full_post(*_args):
            return post

        async def media(_link, folder: Path, *_args):
            file = folder / "video.mp4"
            file.write_bytes(b"media")
            return [file], "test"

        bot = SimpleNamespace(_reply=reply, upload_bytes=1024, cookie_file=None,
                              cobalt_url=None, cobalt_key=None, gallery_rs_path=None)
        with patch("media_bot.bot.fetch_reddit_post", new=full_post), patch("media_bot.bot.download_media", new=media), patch("media_bot.bot.reddit_adult_status", return_value=False):
            await MediaBot._handle_reddit(bot, object(), link)
        self.assertEqual(sent, [["reddit_01.png"], ["video.mp4"]])


if __name__ == "__main__":
    unittest.main()
