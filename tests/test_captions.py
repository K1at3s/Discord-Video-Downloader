import unittest
from pathlib import Path
from unittest.mock import patch

from media_bot.captions import _page_caption, caption_messages, display_chunks, fetch_caption
from media_bot.bot import media_gallery_view
from media_bot.links import PostLink


class CaptionTests(unittest.TestCase):
    def test_facebook_post_text_and_source(self):
        page = '<meta property="og:description" content="Xiaomi 17 pro Max kullanıcısıyım WhatsApp 3 kere kapandı">'
        caption = _page_caption(page, "facebook")
        self.assertEqual(caption, "Xiaomi 17 pro Max kullanıcısıyım WhatsApp 3 kere kapandı")
        self.assertEqual(caption_messages(caption, "https://www.facebook.com/share/p/123/")[-1],
                         "> Xiaomi 17 pro Max kullanıcısıyım WhatsApp 3 kere kapandı\n\n[Kaynak](https://www.facebook.com/share/p/123/)")

    def test_instagram_open_graph_caption_without_engagement_prefix(self):
        page = '<meta property="og:description" content="50 likes - user on Instagram: &quot;Merhaba 🌍&quot;">'
        self.assertEqual(_page_caption(page, "instagram"), "Merhaba 🌍")

    def test_threads_login_promo_is_not_post_text(self):
        page = '<meta property="og:description" content="Join Threads to share ideas, ask questions, post random thoughts.">'
        self.assertIsNone(_page_caption(page, "threads"))

    def test_long_multiline_caption_fits_discord_messages(self):
        parts = caption_messages("A" * 5000 + "\nİkinci satır", "https://www.reddit.com/r/a/comments/abc/")
        self.assertTrue(all(len(part) <= 1900 for part in parts))
        self.assertTrue(all(line.startswith("> ") for part in parts for line in part.splitlines() if line and not line.startswith("[Kaynak]")))
        self.assertIn("[Kaynak]", parts[-1])

    def test_facebook_truncated_preview_is_rejected(self):
        page = '<meta property="og:description" content="WhatsApp kalici olarak...">'
        self.assertIsNone(_page_caption(page, "facebook"))
        self.assertEqual(_page_caption(page, "facebook", allow_truncated=True), "WhatsApp kalici olarak...")
        view = media_gallery_view("facebook", [Path("photo.jpg")],
                                  "WhatsApp kalici olarak...", "https://facebook.com/posts/1")
        self.assertIn("Açıklama önizlemesi", view.to_components()[0]["components"][3]["content"])

    def test_full_caption_is_not_cut_off(self):
        caption = "A" * 9000
        self.assertEqual("".join(display_chunks(caption)), caption)


class FacebookCaptionFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_is_used_when_complete_caption_is_unavailable(self):
        async def fake_fetch(_link, route):
            return "WhatsApp kalici olarak..." if route == "page" else None

        link = PostLink("facebook", "https://www.facebook.com/share/p/123/")
        with patch("media_bot.captions._fetch", new=fake_fetch):
            self.assertEqual(await fetch_caption(link), "WhatsApp kalici olarak...")

    async def test_complete_caption_wins_over_preview(self):
        async def fake_fetch(_link, route):
            return "WhatsApp kalıcı olarak kapandı." if route == "gendownload" else "WhatsApp kalici olarak..."

        link = PostLink("facebook", "https://www.facebook.com/share/p/123/")
        with patch("media_bot.captions._fetch", new=fake_fetch):
            self.assertEqual(await fetch_caption(link), "WhatsApp kalıcı olarak kapandı.")


if __name__ == "__main__":
    unittest.main()
