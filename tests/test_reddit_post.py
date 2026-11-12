import unittest
from xml.sax.saxutils import escape
from unittest.mock import patch

import httpx

from media_bot.links import parse_link
from media_bot.reddit_post import _archive_post, _rss_post, fetch_reddit_post


class RedditPostTests(unittest.TestCase):
    def test_atom_post_contains_full_body_author_and_canonical_url(self):
        content = ('<div class="md"><p>First paragraph &amp; more.</p>'
                   '<p>Full second paragraph.</p></div> submitted by someone '
                   '<a href="https://www.reddit.com/r/opencode/comments/1xuc834/deepseek_v41_flash/">[link]</a>')
        xml = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
               '<id>t3_1xuc834</id><title>DeepSeek V4.1 Flash</title>'
               f'<content type="html">{escape(content)}</content>'
               '<author><name>/u/Secret-Builder-8249</name></author>'
               '<published>2026-11-12T13:03:38+00:00</published>'
               '<link href="https://www.reddit.com/r/opencode/comments/1xuc834/deepseek_v41_flash/"/>'
               '</entry></feed>')
        link = parse_link("https://www.reddit.com/r/opencode/s/ykPZRiQsc9")
        post = _rss_post(xml, link, "DeepSeek V4.1 Flash")
        self.assertIsNotNone(post)
        self.assertEqual(post.body, "First paragraph & more.\n\nFull second paragraph.")
        self.assertEqual(post.author, "Secret-Builder-8249")
        self.assertIs(post.is_self, True)
        self.assertEqual(post.local_time, "12.11.2026 16:03")
        self.assertEqual(post.url, "https://www.reddit.com/r/opencode/comments/1xuc834/deepseek_v41_flash/")
        self.assertIsNone(_rss_post(xml, link, "Different post"))

    def test_archive_requires_matching_post_and_subreddit(self):
        data = {"data": [{"id": "1xuc834", "subreddit": "opencode", "title": "Title",
                          "selftext": "Complete text", "author": "someone", "score": 47,
                          "num_comments": 42, "created_utc": 1794488618, "is_self": True}]}
        post = _archive_post(data, "opencode", "1xuc834")
        self.assertEqual(post.body, "Complete text")
        self.assertEqual((post.score, post.comments), (47, 42))
        self.assertIs(post.is_self, True)
        self.assertIsNone(_archive_post(data, "other", "1xuc834"))


class RedditArchiveFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_short_link_resolves_through_archive_when_rss_is_unavailable(self):
        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.host == "www.reddit.com":
                return httpx.Response(403)
            if request.url.path == "/api/short_links":
                return httpx.Response(200, json={"data": [{"resolved_path":
                    "/r/opencode/comments/1xuc834/deepseek_v41_flash/"}]})
            if request.url.path == "/api/posts/ids":
                return httpx.Response(200, json={"data": [{"id": "1xuc834",
                    "subreddit": "opencode", "title": "DeepSeek V4.1 Flash",
                    "selftext": "Complete archived body", "author": "someone"}]})
            return httpx.Response(503)

        client_type = httpx.AsyncClient

        def client_factory(**kwargs):
            return client_type(transport=httpx.MockTransport(respond), **kwargs)

        link = parse_link("https://www.reddit.com/r/opencode/s/ykPZRiQsc9")
        with patch("media_bot.reddit_post.httpx.AsyncClient", side_effect=client_factory):
            post = await fetch_reddit_post(link, "DeepSeek V4.1 Flash")
        self.assertEqual(post.body, "Complete archived body")


if __name__ == "__main__":
    unittest.main()
