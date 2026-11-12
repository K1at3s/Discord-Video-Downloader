import unittest

from media_bot.gendownload import media_options


class GenDownloadTests(unittest.TestCase):
    def test_only_hosted_media_streams_and_uploadable_formats(self):
        response = {"formats": [
            {"type": "video", "ext": "mp4", "filesize": 500, "url": "https://dl.gendownload.com/api/stream?t=a&i=0"},
            {"type": "video", "ext": "mp4", "filesize": 5, "url": "https://dl.gendownload.com/api/stream?t=a&i=1"},
            {"type": "audio", "ext": "mp3", "url": "https://dl.gendownload.com/api/stream?t=a&i=2"},
            {"type": "video", "ext": "mp4", "url": "https://dl.gendownload.com.evil.test/api/stream?t=a&i=3"},
        ]}
        self.assertEqual(media_options(response, 100), ([], ["https://dl.gendownload.com/api/stream?t=a&i=1"]))

    def test_carousel_keeps_more_than_ten_images(self):
        formats = [{"type": "image", "ext": "jpg",
                    "url": f"https://dl.gendownload.com/api/stream?t=a&i={index}"}
                   for index in range(23)]
        images, videos = media_options({"formats": formats}, 1024)
        self.assertEqual(len(images), 23)
        self.assertEqual(videos, [])


if __name__ == "__main__":
    unittest.main()
