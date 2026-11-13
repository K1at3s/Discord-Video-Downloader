import json
import unittest

from media_bot.web_providers import _threads_page_urls, _threadsdl_urls


class ThreadsPageTests(unittest.TestCase):
    def test_photo_carousel_preserves_all_items_and_order(self):
        def photo(name):
            return {"image_versions2": {"candidates": [
                {"url": f"https://scontent.cdninstagram.com/{name}-small.jpg", "width": 100, "height": 100},
                {"url": f"https://scontent.cdninstagram.com/{name}.jpg", "width": 1000, "height": 1000},
            ]}}

        page = {"other": {"code": "OTHER", **photo("wrong")},
                "post": {"code": "ABC123", "carousel_media": [photo("first"), photo("second")]}}
        markup = '<script type="application/json">' + json.dumps(page) + '</script>'
        self.assertEqual(_threads_page_urls(markup, "ABC123"), [
            ("https://scontent.cdninstagram.com/first.jpg", "photo"),
            ("https://scontent.cdninstagram.com/second.jpg", "photo"),
        ])

    def test_rejects_partial_or_untrusted_carousel(self):
        page = {"code": "ABC123", "carousel_media": [
            {"image_versions2": {"candidates": [{"url": "https://scontent.cdninstagram.com/good.jpg"}]}},
            {"image_versions2": {"candidates": [{"url": "https://example.org/bad.jpg"}]}},
        ]}
        markup = '<script type="application/json">' + json.dumps(page) + '</script>'
        self.assertEqual(_threads_page_urls(markup, "ABC123"), [])

    def test_download_site_proxy_urls_become_direct_cdn_urls(self):
        page = (
            '<img alt="Threads media 1" src="https://cdn.threadsphotodownloader.com/'
            'scontent.cdninstagram.com/v/a.jpg?stp=dst&amp;oh=1" />'
            '<video src="https://cdn.threadsphotodownloader.com/'
            'instagram.fhio3-1.fna.fbcdn.net/o1/v/a.mp4?oh=2"></video>'
        )
        self.assertEqual(_threadsdl_urls(page), [
            ("https://scontent.cdninstagram.com/v/a.jpg?stp=dst&oh=1", "photo"),
            ("https://instagram.fhio3-1.fna.fbcdn.net/o1/v/a.mp4?oh=2", "video"),
        ])
        self.assertEqual(_threadsdl_urls(
            '<img src="https://cdn.threadsphotodownloader.com/evil.example/x.jpg">'), [])
        self.assertEqual(_threadsdl_urls(
            '<img src="https://scontent.cdninstagram.com/not-proxied.jpg">'), [])

    def test_download_site_author_avatar_is_not_post_media(self):
        page = (
            '<img alt="Threads media 1" src="https://cdn.threadsphotodownloader.com/'
            'scontent.cdninstagram.com/v/t1.6435-9/avatar.png?oh=1" />'
            '<img alt="Threads media 2" src="https://cdn.threadsphotodownloader.com/'
            'scontent.cdninstagram.com/v/t51.82787-15/post.jpg?oh=2" />'
        )
        self.assertEqual(_threadsdl_urls(page), [
            ("https://scontent.cdninstagram.com/v/t51.82787-15/post.jpg?oh=2", "photo"),
        ])


if __name__ == "__main__":
    unittest.main()
