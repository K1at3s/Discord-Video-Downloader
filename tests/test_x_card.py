"""Tests for the X post card data normalisation helpers."""

import unittest
from unittest.mock import patch

from PIL import ImageFont

from media_bot.x_card import (
    _count,
    _first_metric,
    _font,
    _merge_post,
    _metric,
    _wrap,
    normalize_fxtwitter,
    normalize_syndication,
    normalize_vxtwitter,
)


class CountTests(unittest.TestCase):
    def test_missing_and_invalid_values(self):
        self.assertEqual(_count(None), "—")
        self.assertEqual(_count("abc"), "—")
        self.assertEqual(_count("0"), "0")
        self.assertEqual(_count(999), "999")

    def test_thousands_and_millions(self):
        self.assertEqual(_count(1_500), "2K")
        self.assertEqual(_count(12_345), "12K")
        self.assertEqual(_count(999_999), "1M")
        self.assertEqual(_count(1_000_000), "1M")
        self.assertEqual(_count(2_400_000), "2.4M")
        self.assertEqual(_count(120_000_000), "120M")


class MetricTests(unittest.TestCase):
    def test_metric_ignores_missing_booleans_and_negatives(self):
        self.assertIsNone(_metric(None))
        self.assertIsNone(_metric(True))
        self.assertIsNone(_metric(False))
        self.assertIsNone(_metric(""))
        self.assertIsNone(_metric("—"))
        self.assertIsNone(_metric("-"))
        self.assertIsNone(_metric("-3"))
        self.assertIsNone(_metric("nope"))

    def test_metric_reads_numbers_and_nested_dicts(self):
        self.assertEqual(_metric("1,234"), 1234)
        self.assertEqual(_metric("12.7"), 12)
        self.assertEqual(_metric(7), 7)
        self.assertEqual(_metric({"count": "5"}), 5)
        self.assertEqual(_metric({"views": {"value": 9}}), 9)
        self.assertIsNone(_metric({"unknown": 1}))

    def test_first_metric_skips_missing_values(self):
        self.assertIsNone(_first_metric(None, "", "x"))
        self.assertEqual(_first_metric(None, {"count": 4}, 9), 4)


class FakeDraw:
    def textlength(self, text, font=None):
        return len(text) * 10


class WrapTests(unittest.TestCase):
    def setUp(self):
        self.draw = FakeDraw()
        self.font = ImageFont.load_default()

    def test_wraps_on_words(self):
        self.assertEqual(_wrap(self.draw, "hello world again", self.font, 110, 5),
                         ["hello world", "again"])

    def test_splits_long_words(self):
        self.assertEqual(_wrap(self.draw, "abcdefghij", self.font, 50, 5),
                         ["abcde", "fghij"])

    def test_limits_lines_and_adds_ellipsis(self):
        lines = _wrap(self.draw, "one two three four five six", self.font, 70, 2)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[-1].endswith("…"))

    def test_empty_text_returns_one_empty_line(self):
        self.assertEqual(_wrap(self.draw, "", self.font, 100, 2), [""])

    def test_missing_fonts_fall_back_to_default(self):
        with patch("media_bot.x_card.ImageFont.truetype", side_effect=OSError), \
                patch("media_bot.x_card.ImageFont.load_default", return_value="default"):
            self.assertEqual(_font(20, bold=True), "default")


class VxTwitterTests(unittest.TestCase):
    def test_requires_text(self):
        self.assertIsNone(normalize_vxtwitter({}))
        self.assertIsNone(normalize_vxtwitter({"text": 5}))

    def test_media_metrics_and_quote(self):
        quote = {"text": "quoted", "user_name": "Other"}
        post = normalize_vxtwitter({
            "text": "root",
            "tweetID": "123",
            "user_name": "Name",
            "user_screen_name": "handle",
            "user_profile_image_url": "https://img/a.png",
            "user_blue_verified": True,
            "date_epoch": 1_700_000_000,
            "replies": "1,200",
            "retweets": 3,
            "likes": {"count": 4},
            "views": "5",
            "media_extended": [{"url": "https://cdn/v.mp4", "type": "video"},
                               {"url": "https://cdn/p.jpg", "type": "photo"}],
            "qrt": quote,
        })
        self.assertEqual(post["id"], "123")
        self.assertEqual(post["author"]["screen_name"], "handle")
        self.assertTrue(post["author"]["verified"])
        self.assertEqual(post["created_at"], "2023-11-14T22:13:20+00:00")
        self.assertEqual((post["replies"], post["reposts"], post["likes"], post["views"]),
                         (1200, 3, 4, 5))
        self.assertEqual([item["type"] for item in post["media"]["all"]], ["video", "photo"])
        self.assertEqual(post["quote"]["text"], "quoted")
        self.assertIsNone(post["quote"]["quote"])

    def test_media_url_fallback_and_string_epoch(self):
        post = normalize_vxtwitter({
            "text": "root",
            "date_epoch": "1700000000",
            "mediaURLs": ["https://cdn/v.mp4", "https://cdn/p.jpg"],
        })
        self.assertEqual([item["type"] for item in post["media"]["all"]], ["video", "photo"])
        self.assertTrue(post["created_at"].startswith("2023-11-14"))
        self.assertIsNone(post["quote"])

    def test_quote_can_be_disabled(self):
        post = normalize_vxtwitter({"text": "root", "qrt": {"text": "quoted"}},
                                   include_quote=False)
        self.assertIsNone(post["quote"])


class SyndicationTests(unittest.TestCase):
    def test_requires_text(self):
        self.assertIsNone(normalize_syndication({}))

    def test_video_variants_are_sorted_by_bitrate(self):
        post = normalize_syndication({
            "text": "tweet",
            "id_str": "42",
            "user": "not-a-dict",
            "created_at": "2024-01-02T03:04:05Z",
            "conversation_count": "3",
            "favorite_count": 8,
            "mediaDetails": [
                {"type": "video", "media_url_https": "https://cdn/poster.jpg",
                 "video_info": {"variants": [
                     {"content_type": "video/mp4", "url": "https://cdn/low.mp4", "bitrate": 100},
                     {"content_type": "application/x-mpegURL", "url": "https://cdn/hls.m3u8"},
                     {"content_type": "video/mp4", "url": "https://cdn/high.mp4", "bitrate": 900},
                 ]}},
                {"type": "animated_gif", "media_url": "https://cdn/g.gif"},
                {"type": "photo", "media_url_https": "https://cdn/p.jpg"},
                "not-a-dict",
            ],
            "quoted_tweet": {"text": "quoted", "user": {"screen_name": "other"}},
        })
        self.assertEqual(post["id"], "42")
        self.assertEqual(post["author"]["name"], None)
        self.assertEqual([item["url"] for item in post["media"]["all"]],
                         ["https://cdn/high.mp4", "https://cdn/g.gif", "https://cdn/p.jpg"])
        self.assertEqual(post["quote"]["text"], "quoted")

    def test_quote_depth_is_capped(self):
        quoted = {"text": "quoted", "quoted_tweet": {"text": "deeper"}}
        post = normalize_syndication({"text": "root", "quoted_tweet": quoted}, depth=1)
        self.assertIsNone(post["quote"]["quote"])


class FxTwitterTests(unittest.TestCase):
    def test_requires_text(self):
        self.assertIsNone(normalize_fxtwitter({"text": ""}))

    def test_media_and_author_variants(self):
        post = normalize_fxtwitter({
            "text": "root",
            "id": "abc",
            "author": {"name": "Name", "screen_name": "handle",
                       "avatar_url": "https://img/a.png",
                       "verification": {"verified": True}},
            "created_timestamp": 1_700_000_000,
            "replies": 1,
            "quotes": 2,
            "media": {"all": [{"url": "https://cdn/v.mp4", "type": "gif"},
                              {"url": "https://cdn/p.jpg", "type": "photo"},
                              {"url": 5},
                              "not-a-dict"]},
            "quote": {"text": "quoted"},
        })
        self.assertEqual(post["id"], "abc")
        self.assertTrue(post["author"]["verified"])
        self.assertEqual([item["type"] for item in post["media"]["all"]], ["video", "photo"])
        self.assertEqual((post["replies"], post["reposts"]), (1, 2))
        self.assertEqual(post["quote"]["text"], "quoted")

    def test_videos_and_photos_fallback(self):
        post = normalize_fxtwitter({
            "text": "root",
            "author": "not-a-dict",
            "created_timestamp": "1700000000",
            "media": {"videos": [{"url": "https://cdn/v.mp4"}],
                      "photos": [{"url": "https://cdn/p.jpg"}, {"url": 5}]},
        })
        self.assertEqual([item["type"] for item in post["media"]["all"]], ["video", "photo"])
        self.assertTrue(post["created_at"].startswith("2023-11-14"))
        self.assertIsNone(post["quote"])

    def test_quote_can_be_disabled_and_media_can_be_missing(self):
        post = normalize_fxtwitter({"text": "root", "quote": {"text": "quoted"},
                                    "media": []}, include_quote=False)
        self.assertIsNone(post["quote"])
        self.assertEqual(post["media"], {"all": []})


class MergePostTests(unittest.TestCase):
    def test_empty_inputs(self):
        self.assertIsNone(_merge_post(None, None))
        extra = {"text": "extra"}
        self.assertIs(extra, _merge_post(None, extra))
        base = {"text": "base"}
        self.assertIs(base, _merge_post(base, None))

    def test_missing_fields_are_filled_from_extra(self):
        base = {
            "text": "base",
            "author": {"name": "A"},
            "replies": None,
            "media": {"all": []},
            "quote": {"text": "q1"},
        }
        extra = {
            "id": "9",
            "created_at": "2024-01-01",
            "author": {"screen_name": "h", "avatar_url": "a", "verified": True},
            "replies": "5",
            "likes": 3,
            "media": {"all": [{"url": "https://cdn/x.jpg", "type": "photo"}]},
            "quote": {"text": "q2", "likes": 1},
        }
        merged = _merge_post(base, extra)
        self.assertEqual(merged["id"], "9")
        self.assertEqual(merged["author"],
                         {"name": "A", "screen_name": "h", "avatar_url": "a", "verified": True})
        self.assertEqual((merged["replies"], merged["likes"]), ("5", 3))
        self.assertEqual(merged["media"]["all"][0]["url"], "https://cdn/x.jpg")
        self.assertEqual(merged["quote"]["text"], "q1")
        self.assertEqual(merged["quote"]["likes"], 1)

    def test_existing_media_wins(self):
        base = {"media": {"all": [{"url": "https://cdn/base.jpg"}]}}
        extra = {"media": {"all": [{"url": "https://cdn/extra.jpg"}]}}
        self.assertEqual(_merge_post(base, extra)["media"]["all"][0]["url"],
                         "https://cdn/base.jpg")

    def test_non_dict_quotes_are_dropped(self):
        merged = _merge_post({"quote": "text"}, {"quote": None})
        self.assertEqual(merged["quote"], "text")


if __name__ == "__main__":
    unittest.main()
