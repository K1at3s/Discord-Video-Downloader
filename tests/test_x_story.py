import tempfile
import unittest
from pathlib import Path

from PIL import Image

from media_bot.x_card import normalize_syndication
from media_bot.x_story import render_story


class XStoryTests(unittest.TestCase):
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
                self.assertIn((39, 167, 231), colors)  # Verification badge is visible.
                self.assertIn((237, 52, 84), colors)  # Heart icon is visible.


if __name__ == "__main__":
    unittest.main()
