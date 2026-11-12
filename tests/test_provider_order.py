import unittest

from media_bot.download import provider_order


class ProviderOrderTests(unittest.TestCase):
    def test_provider_routes(self):
        expected = {"instagram": 7, "facebook": 5, "reddit": 5, "x": 6, "tiktok": 6}
        for platform, base_count in expected.items():
            with self.subTest(platform=platform):
                self.assertEqual(len(provider_order(platform)), base_count)
                self.assertEqual(len(provider_order(platform, cobalt_enabled=True)), base_count + 1)
                self.assertEqual(len(provider_order(platform, cobalt_enabled=True, gallery_rs_enabled=True)), base_count + 2)
                if platform != "instagram":
                    self.assertEqual(provider_order(platform)[0], "direct")
                if platform in {"instagram", "facebook", "tiktok", "x"}:
                    self.assertIn("gendownload", provider_order(platform))

    def test_added_platforms_have_real_routes(self):
        self.assertEqual(provider_order("threads"), ("vette",))
        self.assertEqual(provider_order("douyin"), ("yt-dlp", "direct"))
        self.assertEqual(provider_order("weibo"), ("gallery-dl", "yt-dlp"))
        self.assertEqual(provider_order("streamable", cobalt_enabled=True),
                         ("direct", "cobalt", "yt-dlp"))
        with self.assertRaises(ValueError):
            provider_order("spotify")


if __name__ == "__main__":
    unittest.main()
