import unittest

from media_bot.direct_providers import (
    allowed_media_url,
    facebook_media_urls,
    reddit_media_urls,
    tikwm_media_urls,
    x_media_urls,
    vx_media_urls,
)
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

if __name__ == "__main__":
    unittest.main()
