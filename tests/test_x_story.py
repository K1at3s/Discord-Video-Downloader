import tempfile
import unittest
from pathlib import Path

from PIL import Image

from media_bot.x_card import _count, normalize_fxtwitter, normalize_syndication
from media_bot.x_story import render_story


class XStoryTests(unittest.TestCase):
    def test_fxembed_v2_status_keeps_views_and_reposts(self):
        raw = {
            "status": {
                "type": "status",
                "id": "2123020797023322448",
                "url": "https://x.com/haskologlu/status/2123020797023322448",
                "text": "Merhaba",
                "author": {
                    "screen_name": "haskologlu",
                    "name": "ibrahim Haskoloğlu",
                    "avatar_url": "https://pbs.twimg.com/profile_images/x_200x200.jpg",
                    "verification": {"verified": True, "type": "individual"},
                },
                "replies": 566,
                "reposts": 879,
                "likes": 9298,
                "quotes": 92,
                "views": 463917,
                "created_at": "Wed Nov 18 11:49:36 +0000 2026",
                "created_timestamp": 1795002576,
                "media": {},
            }
        }
        post = normalize_fxtwitter(raw["status"])
        self.assertEqual(post["views"], 463917)
        self.assertEqual(post["reposts"], 879)
        self.assertEqual(post["likes"], 9298)
        self.assertTrue(post["author"]["verified"])

    def test_count_rounds_compactly(self):
        self.assertEqual(_count(463917), "464K")
        self.assertEqual(_count(999500), "1M")
        self.assertEqual(_count(1234567), "1.2M")
        self.assertEqual(_count(999), "999")
    def test_syndication_keeps_quote_avatars_and_verification(self):
        raw = {"id_str": "12345", "text": "Merhaba", "user": {
            "name": "İlk 🇹🇷", "screen_name": "ilk", "is_blue_verified": False,
            "profile_image_url_https": "https://pbs.twimg.com/one.jpg",
        }, "quoted_tweet": {"id_str": "67890", "text": "Alıntı", "user": {
            "name": "İkinci", "screen_name": "ikinci", "is_blue_verified": True,
            "profile_image_url_https": "https://pbs.twimg.com/two.jpg",
        }}}
        post = normalize_syndication(raw)
        self.assertEqual(post["quote"]["author"]["avatar_url"], "https://pbs.twimg.com/two.jpg")
        self.assertTrue(post["quote"]["author"]["verified"])

    def test_dark_story_renders_flag_badge_and_colored_counters(self):
        post = {"text": "Merhaba", "author": {"name": "Test 🇹🇷", "screen_name": "test", "verified": True},
                "quote": {"text": "Alıntı", "author": {"name": "Alıntı", "screen_name": "quote", "verified": True}},
                "replies": 1, "reposts": 2, "likes": 3, "views": 4}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "story.png"
            render_story(post, [None, None], output)
            with Image.open(output) as image:
                self.assertEqual(image.width, 1080)
                colors = set(image.convert("RGB").get_flattened_data())
                self.assertIn((196, 33, 47), colors)  # Turkish flag is visible.
                self.assertIn((29, 155, 240), colors)  # Verification badge is visible.
                self.assertIn((237, 52, 84), colors)  # Heart icon is visible.


if __name__ == "__main__":
    unittest.main()
