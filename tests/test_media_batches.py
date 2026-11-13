import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from media_bot.bot import MediaBot, media_gallery_view
from media_bot.direct_providers import tikwm_media_urls
from media_bot.download import media_files
from media_bot.links import PostLink
from media_bot.limits import attachment_batches


class MediaBatchTests(unittest.TestCase):
    def test_instagram_reel_places_video_above_compact_caption_card(self):
        url = "https://www.instagram.com/reel/Example123/"
        view = media_gallery_view("instagram", [Path("reel.mp4")],
                                  "İlk satır\n#ikinci - @user on November 11, 2026", url)
        gallery, details = view.to_components()
        self.assertEqual(gallery["type"], 12)
        self.assertEqual(gallery["items"][0]["media"]["url"], "attachment://reel.mp4")
        self.assertEqual(details["type"], 17)
        header, separator, body, footer = details["components"]
        self.assertEqual(header["content"], "## Instagram Reel")
        self.assertEqual(separator["type"], 14)
        self.assertEqual(body["content"], "> **İlk satır**\n> **#ikinci**")
        self.assertEqual(footer["components"][0]["content"],
                         "-# [@user](https://www.instagram.com/user/) · November 11, 2026")
        self.assertEqual(footer["accessory"]["url"], url)


    def test_instagram_reel_without_author_keeps_plain_caption(self):
        view = media_gallery_view("instagram", [Path("reel.mp4")], "Sadece açıklama",
                                  "https://www.instagram.com/reel/Example123/")
        _, details = view.to_components()
        self.assertEqual([item["type"] for item in details["components"]], [10, 14, 10, 1])
        self.assertEqual(details["components"][2]["content"], "> **Sadece açıklama**")
        self.assertEqual(details["components"][3]["components"][0]["url"],
                         "https://www.instagram.com/reel/Example123/")

    def test_instagram_photo_carousel_keeps_one_gallery(self):
        files = [Path("first.jpg"), Path("second.jpg")]
        view = media_gallery_view("instagram", files, "Açıklama", "https://www.instagram.com/p/Example123/")
        components = view.to_components()[0]["components"]
        self.assertEqual(len(components[1]["items"]), 2)

    def test_two_photos_and_caption_share_one_gallery(self):
        files = [Path("first.jpg"), Path("second.jpg")]
        view = media_gallery_view("facebook", files, "Tam açıklama", "https://facebook.com/posts/1")
        components = view.to_components()[0]["components"]
        self.assertEqual([item["media"]["url"] for item in components[1]["items"]],
                         ["attachment://first.jpg", "attachment://second.jpg"])
        self.assertIn("Tam açıklama", components[3]["content"])
        self.assertEqual(components[4]["components"][0]["url"], "https://facebook.com/posts/1")

    def test_twenty_three_images_keep_order_and_split_ten_per_message(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            for index in range(1, 24):
                (folder / f"direct_{index}.jpg").write_bytes(b"image")
            files = media_files(folder, 1024)
            self.assertEqual([file.name for file in files],
                             [f"direct_{index}.jpg" for index in range(1, 24)])
            batches = attachment_batches(files)
            self.assertEqual([len(batch) for batch in batches], [10, 10, 3])
            self.assertEqual([file for batch in batches for file in batch], files)

    def test_tiktok_slideshow_does_not_stop_at_ten(self):
        images = [f"/photo-{index}.jpg" for index in range(1, 24)]
        self.assertEqual(len(tikwm_media_urls({"images": images})), 23)


class BotBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_bot_replies_with_ten_ten_and_three_files(self):
        counts = []
        contents = []

        async def reply(_message, content="", files=None, *, embed=None, view=None):
            self.assertTrue(all(file.is_file() for file in files or []))
            counts.append(len(files or []))
            contents.append((content, embed, view))

        async def caption(_link, _cookie_file):
            return "Gönderi açıklaması"

        async def download(_link, folder, *_args):
            files = []
            for index in range(1, 24):
                file = folder / f"image_{index}.jpg"
                file.write_bytes(b"image")
                files.append(file)
            return files, "test"

        bot = SimpleNamespace(upload_bytes=1024, cookie_file=None, cobalt_url=None,
                              cobalt_key=None, gallery_rs_path=None, _reply=reply)
        link = PostLink("tiktok", "https://www.tiktok.com/@test/video/1234567890", "1234567890")
        with patch("media_bot.bot.download_media", new=download), patch("media_bot.bot.fetch_caption", new=caption):
            await MediaBot._handle_link(bot, object(), link)
        self.assertEqual(counts, [10, 10, 3])
        first = contents[0][2].to_components()[0]["components"]
        self.assertEqual(len(first[1]["items"]), 10)
        self.assertIn("Gönderi açıklaması", first[3]["content"])
        self.assertEqual(first[4]["components"][0]["url"], link.url)
        self.assertTrue(all(item[1] is None and item[2] is not None for item in contents))


if __name__ == "__main__":
    unittest.main()
