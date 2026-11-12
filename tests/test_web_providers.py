import unittest

from media_bot.web_providers import _btch_urls, _indown_urls, _rapidsave_url, _tiktokio_urls, _vette_urls


class WebProviderParsingTests(unittest.TestCase):
    def test_indown_keeps_all_album_items(self):
        page = """
        <div class="download-items"><a href="https://cdninstagram.com/a.jpg">Download Photo</a></div>
        <div class="download-items"><a href="https://cdninstagram.com/b.jpg">Download Photo</a></div>
        <a href="https://evil.example/steal.jpg">Download Photo</a>
        """
        self.assertEqual(_indown_urls(page), [
            ("https://cdninstagram.com/a.jpg", "photo"),
            ("https://cdninstagram.com/b.jpg", "photo"),
        ])

    def test_tiktokio_keeps_slides_but_not_music(self):
        page = """
        <div class="images-grid">
          <div class="image-item"><a href="https://tiktokio.com/one.jpg">one</a></div>
          <div class="image-item"><a href="https://tiktokio.com/two.jpg">two</a></div>
        </div><a href="https://tiktokio.com/song.mp3">music</a>
        """
        self.assertEqual(_tiktokio_urls(page), [
            ("https://tiktokio.com/one.jpg", "photo"),
            ("https://tiktokio.com/two.jpg", "photo"),
        ])

    def test_rapidsave_rejects_untrusted_download(self):
        self.assertIsNone(_rapidsave_url('<a class="downloadbutton" href="https://evil.example/a.mp4">save</a>'))
        self.assertEqual(_rapidsave_url('<a class="downloadbutton" href="https://sd.rapidsave.com/download.php?id=1">save</a>'),
                         "https://sd.rapidsave.com/download.php?id=1")

    def test_btch_chooses_one_video_quality(self):
        payload = {"success": True, "normalized": {"kind": "media", "media": [
            {"type": "video", "url": "https://cdn.example/hd.mp4"},
            {"type": "video", "url": "https://cdn.example/sd.mp4"},
            {"type": "audio", "url": "https://cdn.example/a.mp3"},
        ]}}
        self.assertEqual(_btch_urls(payload, "tiktok"), [("https://cdn.example/hd.mp4", "video")])

    def test_vette_keeps_full_gallery_and_existing_proxy(self):
        payload = {"success": True, "metadata": {"images": [
            {"url": "/api/image?url=https%3A%2F%2Fexample.com%2Fa.jpg", "kind": "image"},
            {"url": "https://example.com/b.jpg", "kind": "image"},
        ]}}
        self.assertEqual(_vette_urls(payload, "instagram"), [
            ("https://www.socialdownloader.space/api/image?url=https%3A%2F%2Fexample.com%2Fa.jpg", "photo"),
            ("https://www.socialdownloader.space/api/image?url=https%3A%2F%2Fexample.com%2Fb.jpg", "photo"),
        ])
        payload["metadata"]["images"].append({"kind": "image"})
        self.assertEqual(_vette_urls(payload, "instagram"), [])

    def test_vette_rejects_external_video_endpoint(self):
        self.assertEqual(_vette_urls({"success": True, "downloadUrl": "https://evil.example/api/video?url=x"}, "reddit"), [])


if __name__ == "__main__":
    unittest.main()
