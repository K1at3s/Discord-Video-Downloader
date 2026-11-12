import unittest

from media_bot.links import parse_link
from media_bot.metadata_fallback import Preview, parse_preview, preview_embeds


class MetadataFallbackTests(unittest.TestCase):
    def test_threads_login_page_is_not_a_post_preview(self):
        page = ('<meta property="og:title" content="Threads · Log in">'
                '<meta property="og:description" content="Join Threads to share ideas and ask questions.">')
        self.assertEqual(parse_preview(page, "threads"), Preview())

    def test_open_graph_keeps_metadata_and_rejects_foreign_image(self):
        page = '<meta property="og:title" content="A post"><meta property="og:description" content="Hello &amp; welcome"><meta property="og:image" content="https://evil.example/image.jpg">'
        self.assertEqual(parse_preview(page, "reddit"), Preview("A post", "Hello & welcome", None))

    def test_long_caption_is_present_across_embeds(self):
        link = parse_link("https://www.reddit.com/r/test/comments/abc123/example/")
        caption = "A" * 8000
        embeds = preview_embeds(link, Preview(title="Example"), caption)
        self.assertGreater(len(embeds), 1)
        self.assertEqual("".join(embed.description or "" for embed in embeds), "> " + caption)
        self.assertTrue(all(len(embed.description or "") <= 3800 for embed in embeds))


if __name__ == "__main__":
    unittest.main()
