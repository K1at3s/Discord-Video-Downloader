import unittest

from media_bot.indownio import indownio_urls, page_for, story_id

RESULT = """
<div class="container mt-4" id="result">
    <div class="row justify-content-center">
        <div class="col-md-4 text-center">
            <div>
                <video controls class="img-fluid" preload="none"
                       poster="https://d3.indown.io/fetch?url=https%3A%2F%2Fscontent.cdninstagram.com%2Fa.jpg%3Fig_cache_key%3DNDAyOTc4MjA2MzE4Njk4MTk4NA%253D%253D.3-ccb7-5">
                    <source src="https://instagram.fabc-1.fna.fbcdn.net/o1/a.mp4?_nc_cat=1&amp;oe=1" type="video/mp4">
                </video>
                <a href="https://instagram.fabc-1.fna.fbcdn.net/o1/a.mp4?_nc_cat=1&amp;oe=1" class="mb-2">Download</a>
                <a href="https://d4.indown.io/fetch?url=https%3A%2F%2Finstagram.fabc-1.fna.fbcdn.net%2Fo1%2Fa.mp4">Server 2</a>
            </div>
        </div>
        <div class="col-md-4 text-center">
            <div class="image-link">
                <img src="https://d4.indown.io/fetch?url=https%3A%2F%2Fscontent.cdninstagram.com%2Fb.jpg%3Fig_cache_key%3DMTIzNDU2Nzg5MA%253D%253D.3-ccb7-5" alt="download">
            </div>
            <a href="https://scontent.cdninstagram.com/b.jpg?ig_cache_key=MTIzNDU2Nzg5MA%3D%3D.3-ccb7-5&amp;dl=1">Download Server 1</a>
        </div>
        <a href="https://evil.example/steal.jpg">Download</a>
        <a href="https://indown.io/en5">home</a>
    </div>
</div>
"""


class IndownioParsingTests(unittest.TestCase):
    def test_reads_each_media_block_once(self):
        self.assertEqual(indownio_urls(RESULT), [
            ("https://instagram.fabc-1.fna.fbcdn.net/o1/a.mp4?_nc_cat=1&oe=1", "video"),
            ("https://scontent.cdninstagram.com/b.jpg?ig_cache_key=MTIzNDU2Nzg5MA%3D%3D.3-ccb7-5&dl=1", "photo"),
        ])

    def test_single_story_link_keeps_only_that_story(self):
        self.assertEqual(indownio_urls(RESULT, "4029782063186981984"), [
            ("https://instagram.fabc-1.fna.fbcdn.net/o1/a.mp4?_nc_cat=1&oe=1", "video"),
        ])
        self.assertEqual(indownio_urls(RESULT, "1234567890"), [(
            "https://scontent.cdninstagram.com/b.jpg?ig_cache_key=MTIzNDU2Nzg5MA%3D%3D.3-ccb7-5&dl=1", "photo",
        )])
        self.assertEqual(indownio_urls(RESULT, "999"), [])

    def test_empty_result_page(self):
        self.assertEqual(indownio_urls("<html><body>Highlights not found</body></html>"), [])

    def test_page_and_story_id(self):
        self.assertEqual(page_for("https://www.instagram.com/stories/highlights/17919681140787123/"),
                         "https://indown.io/instagram-highlights-download/en3")
        self.assertEqual(page_for("https://www.instagram.com/stories/natgeo/4029782063186981984/"),
                         "https://indown.io/insta-stories-download/en3")
        self.assertEqual(page_for("https://www.instagram.com/reel/AbC123/"), "https://indown.io/reels/en5")
        self.assertEqual(page_for("https://www.instagram.com/p/Dfk9rkrD33b/"), "https://indown.io/photo/en4")
        self.assertEqual(story_id("https://www.instagram.com/stories/natgeo/4029782063186981984/"),
                         "4029782063186981984")
        self.assertIsNone(story_id("https://www.instagram.com/stories/highlights/17919681140787123/"))
        self.assertIsNone(story_id("https://www.instagram.com/p/Dfk9rkrD33b/"))


if __name__ == "__main__":
    unittest.main()
