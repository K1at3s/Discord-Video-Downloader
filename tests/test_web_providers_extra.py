import json
import unittest
from media_bot.web_providers import (
    _btch_urls,
    _embed_media,
    _indown_urls,
    _rapidsave_url,
    _tiktokio_urls,
    _vette_urls,
    _threads_page_urls,
    _threadsdl_urls,
)


class ThreadsPageParsingTests(unittest.TestCase):
    def test_post_by_code_with_carousel_media(self):
        post_id = "ABCDE12345"
        data = [
            {
                "code": post_id,
                "carousel_media": [
                    {
                        "video_versions": [
                            {"url": "https://scontent.cdninstagram.com/a.jpg"}
                        ]
                    },
                    {
                        "image_versions2": {
                            "candidates": [
                                {"url": "https://scontent.cdninstagram.com/b.jpg", "width": 100, "height": 100},
                                {"url": "https://scontent.cdninstagram.com/c.jpg", "width": 1080, "height": 1080},
                            ]
                        }
                    }
                ]
            }
        ]
        markup = '<script type="application/json">' + json.dumps(data) + '</script>'
        self.assertEqual(
            _threads_page_urls(markup, post_id),
            [
                ("https://scontent.cdninstagram.com/a.jpg", "video"),
                ("https://scontent.cdninstagram.com/c.jpg", "photo"),
            ]
        )

    def test_post_by_code_single_image(self):
        post_id = "SINGLE1"
        data = {
            "code": post_id,
            "image_versions2": {
                "candidates": [
                    {"url": "https://scontent.cdninstagram.com/thumb.jpg", "width": 150, "height": 150},
                    {"url": "https://scontent.cdninstagram.com/full.jpg", "width": 720, "height": 720},
                ]
            }
        }
        markup = '<script type="application/json">' + json.dumps(data) + '</script>'
        self.assertEqual(
            _threads_page_urls(markup, post_id),
            [("https://scontent.cdninstagram.com/full.jpg", "photo")]
        )

    def test_wrong_code_is_skipped(self):
        data = {"code": "OTHER", "video_versions": [{"url": "https://cdn/v.mp4"}]}
        markup = '<script type="application/json">' + json.dumps(data) + '</script>'
        self.assertEqual(
            _threads_page_urls(markup, "ABCDE12345"),
            []
        )

    def test_too_many_carousel_items_is_rejected(self):
        post_id = "BIG"
        media = [
            {"image_versions2": {"candidates": [{"url": f"https://cdn/{i}.jpg", "width": 10, "height": 10}]}} 
            for i in range(50)
        ]
        data = {"code": post_id, "carousel_media": media}
        markup = '<script type="application/json">' + json.dumps(data) + '</script>'
        self.assertEqual(_threads_page_urls(markup, post_id), [])

    def test_malformed_json_is_ignored(self):
        markup = '<script type="application/json">not-json</script>'
        self.assertEqual(_threads_page_urls(markup, "anything"), [])

    def test_no_matching_script_returns_empty(self):
        markup = '<html><body>no script</body></html>'
        self.assertEqual(_threads_page_urls(markup, "anything"), [])


class ThreadsDlParsingTests(unittest.TestCase):
    def test_rewrites_proxied_img_and_video(self):
        # Proxied URL whose extracted host is an allowed Threads/CDN host
        markup = (
            '<img src="https://cdn.threadsphotodownloader.com/scontent.cdninstagram.com/'
            'path/a.jpg"/>'
            '<video src="https://cdn.threadsphotodownloader.com/scontent.cdninstagram.com/'
            'path/b.mp4"/>'
        )
        self.assertEqual(
            _threadsdl_urls(markup),
            [
                ("https://scontent.cdninstagram.com/path/a.jpg", "photo"),
                ("https://scontent.cdninstagram.com/path/b.mp4", "video"),
            ]
        )

    def test_host_and_path_partition(self):
        markup = '<img src="https://cdn.threadsphotodownloader.com/scontent.cdninstagram.com/host/only/one.jpg"/>'
        self.assertEqual(
            _threadsdl_urls(markup),
            [("https://scontent.cdninstagram.com/host/only/one.jpg", "photo")]
        )

    def test_stops_at_max_items(self):
        markup = "".join(
            f'<img src="https://cdn.threadsphotodownloader.com/scontent.cdninstagram.com/'
            f'h{i}/p{i}.jpg"/>' for i in range(105)
        )
        result = _threadsdl_urls(markup)
        self.assertEqual(len(result), 100)


class BtchParsingTests(unittest.TestCase):
    def test_facebook_filters_to_first_video(self):
        payload = {
            "success": True,
            "normalized": {
                "kind": "media",
                "media": [
                    {"type": "image", "url": "https://cdn/a.jpg"},
                    {"type": "video", "url": "https://cdn/v.mp4"},
                ]
            }
        }
        self.assertEqual(_btch_urls(payload, "facebook"), [("https://cdn/v.mp4", "video")])

    def test_x_filters_to_first_video(self):
        payload = {
            "success": True,
            "normalized": {
                "kind": "media",
                "media": [
                    {"type": "video", "url": "https://cdn/v.mp4"},
                    {"type": "image", "url": "https://cdn/a.jpg"},
                ]
            }
        }
        self.assertEqual(_btch_urls(payload, "x"), [("https://cdn/v.mp4", "video")])

    def test_tiktok_filters_to_first_video(self):
        payload = {
            "success": True,
            "normalized": {
                "kind": "media",
                "media": [{"type": "image", "url": "https://cdn/a.jpg"}]
            }
        }
        self.assertEqual(_btch_urls(payload, "tiktok"), [])

    def test_other_platform_keeps_all_media(self):
        payload = {
            "success": True,
            "normalized": {
                "kind": "media",
                "media": [
                    {"type": "video", "url": "https://cdn/v.mp4"},
                    {"type": "image", "url": "https://cdn/a.jpg"},
                ]
            }
        }
        self.assertEqual(
            _btch_urls(payload, "instagram"),
            [("https://cdn/v.mp4", "video"), ("https://cdn/a.jpg", "photo")]
        )

    def test_rejects_invalid_payload(self):
        self.assertEqual(_btch_urls({"success": False}, "instagram"), [])
        self.assertEqual(
            _btch_urls({"success": True, "normalized": {"kind": "album"}}, "instagram"),
            []
        )
        self.assertEqual(
            _btch_urls({"success": True, "normalized": {"kind": "media", "media": "nope"}}, "instagram"),
            []
        )


class VetteParsingTests(unittest.TestCase):
    def test_video_download_url(self):
        payload = {"success": True, "downloadUrl": "/api/video?id=123"}
        self.assertEqual(
            _vette_urls(payload, "reddit"),
            [("https://www.socialdownloader.space/api/video?id=123", "video")]
        )

    def test_absolute_video_url_is_kept(self):
        payload = {"success": True, "downloadUrl": "https://www.socialdownloader.space/api/video?id=123"}
        self.assertEqual(
            _vette_urls(payload, "reddit"),
            [("https://www.socialdownloader.space/api/video?id=123", "video")]
        )

    def test_image_gallery_count_mismatch(self):
        payload = {
            "success": True,
            "metadata": {
                "images": [
                    {"url": "/api/image?url=a", "kind": "image"}
                ]
            }
        }
        self.assertEqual(
            _vette_urls(payload, "instagram"),
            [("https://www.socialdownloader.space/api/image?url=a", "photo")]
        )

    def test_rejects_external_video_endpoint(self):
        self.assertEqual(
            _vette_urls(
                {"success": True, "downloadUrl": "https://evil.example/api/video?id=1"},
                "reddit"
            ),
            []
        )

    def test_non_matching_path_is_rejected(self):
        self.assertEqual(
            _vette_urls(
                {"success": True, "downloadUrl": "/api/other?id=1"},
                "reddit"
            ),
            []
        )


class EdgeParsingTests(unittest.TestCase):
    def test_embed_sidecar_without_children_uses_media(self):
        context = json.dumps({
            "gql_data": {
                "shortcode_media": {
                    "is_video": True,
                    "video_url": "https://scontent.cdninstagram.com/v.mp4",
                }
            }
        })
        page = '{"contextJSON":' + json.dumps(context) + ',"other":1}'
        self.assertEqual(_embed_media(page), [("https://scontent.cdninstagram.com/v.mp4", "video")])

    def test_embed_sidecar_invalid_json_returns_empty(self):
        self.assertEqual(_embed_media('{"contextJSON":"notjson"}'), [])
        self.assertEqual(_embed_media(""), [])

    def test_indown_untrusted_host_is_dropped(self):
        page = '<div class="download-items"><a href="https://evil.example/a.mp4">x</a></div>'
        self.assertEqual(_indown_urls(page), [])

    def test_tiktokio_download_btn_without_quality(self):
        page = '<a class="download-btn" href="https://tiktokio.com/v.mp4">download</a>'
        self.assertEqual(_tiktokio_urls(page), [])

    def test_rapidsave_trusted_host(self):
        self.assertEqual(
            _rapidsave_url('<a class="downloadbutton" href="https://sd.rapidsave.com/download.php?id=1">save</a>'),
            "https://sd.rapidsave.com/download.php?id=1"
        )


if __name__ == "__main__":
    unittest.main()