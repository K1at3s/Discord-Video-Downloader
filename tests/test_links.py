import unittest

from media_bot.links import find_links, parse_link


class LinkTests(unittest.TestCase):
    def test_platforms_and_tracking(self):
        cases = [
            ("https://x.com/user/status/123456789?ref=abc", "x"),
            ("https://www.instagram.com/reel/AbC123/?igsh=test", "instagram"),
            ("https://www.tiktok.com/@someone/video/123456789", "tiktok"),
            ("https://www.facebook.com/share/p/abc123/", "facebook"),
            ("https://www.reddit.com/r/test/comments/abc123/a_post/", "reddit"),
            ("https://www.reddit.com/r/opencode/s/ykPZRiQsc9", "reddit"),
            ("https://www.threads.com/@nasa/post/DaDiBxclMT9", "threads"),
            ("https://www.douyin.com/video/1234567890123456789", "douyin"),
            ("https://m.weibo.cn/status/P3abcde", "weibo"),
            ("https://streamable.com/hn8hq", "streamable"),
            ("https://bsky.app/profile/example.bsky.social/post/3abc123?ref=tracking", "bluesky"),
            ("https://open.spotify.com/track/123ABCxyz", "spotify"),
            ("https://open.spotify.com/intl-tr/track/C6d4tpGCTQnxkKWVsy6AUA?si=tracking", "spotify"),
            ("https://youtu.be/jNQXAC9IVRw?si=tracking", "youtube"),
            ("https://www.youtube.com/shorts/jNQXAC9IVRw", "youtube"),
        ]
        for url, platform in cases:
            with self.subTest(url=url):
                link = parse_link(url)
                self.assertIsNotNone(link)
                self.assertEqual(link.platform, platform)
                self.assertNotIn("ref=", link.url)

    def test_rejects_profiles_and_fake_domains(self):
        for url in ["https://x.com/user", "https://x.com.evil.test/user/status/123", "http://127.0.0.1/status/123", "https://instagram.com/user/", "https://bsky.app/profile/example.bsky.social"]:
            with self.subTest(url=url):
                self.assertIsNone(parse_link(url))

    def test_youtube_canonical_and_playlist_rejection(self):
        link = parse_link("https://www.youtube.com/watch?v=jNQXAC9IVRw&list=PL123")
        self.assertEqual(link.url, "https://www.youtube.com/watch?v=jNQXAC9IVRw")
        self.assertEqual(link.post_id, "jNQXAC9IVRw")
        self.assertIsNone(parse_link("https://www.youtube.com/playlist?list=PL123"))
        self.assertIsNone(parse_link("https://youtube.com.evil.test/watch?v=jNQXAC9IVRw"))

    def test_unique_limit(self):
        content = "https://x.com/u/status/1 https://x.com/u/status/1 https://www.reddit.com/r/a/comments/abc/title/"
        self.assertEqual(len(find_links(content, 2)), 2)

    def test_spotify_locale_link_is_canonical_and_deduplicated(self):
        localized = "https://open.spotify.com/intl-tr/track/C6d4tpGCTQnxkKWVsy6AUA?si=tracking"
        plain = "https://open.spotify.com/track/C6d4tpGCTQnxkKWVsy6AUA"
        self.assertEqual(parse_link(localized).url, plain)
        self.assertEqual(len(find_links(f"{localized} {plain}")), 1)

    def test_reddit_short_link_has_no_post_id(self):
        link = parse_link("https://www.reddit.com/r/opencode/s/ykPZRiQsc9")
        self.assertIsNotNone(link)
        self.assertIsNone(link.post_id)


if __name__ == "__main__":
    unittest.main()
