import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from media_bot.direct_providers import canonical_facebook_post
from media_bot.download import download_media
from media_bot.links import parse_link


SHARE = "https://www.facebook.com/share/p/1518UcDYZPU/"
POST = "https://www.facebook.com/groups/750797692117425/posts/2462080954322415/"


class FacebookGalleryTests(unittest.IsolatedAsyncioTestCase):
    async def test_share_link_resolves_to_gallery_post(self):
        async def page(_client, _url):
            return f'<meta property="og:url" content="{POST}">'

        with patch("media_bot.direct_providers._facebook_page", new=page):
            result = await canonical_facebook_post(parse_link(SHARE))
        self.assertEqual(result.url, POST)

    async def test_gallery_wins_before_single_photo_preview(self):
        seen = []

        async def canonical(_link):
            return parse_link(POST)

        async def gallery(link, folder, *_args):
            seen.append(link.url)
            for index in (1, 2):
                (folder / f"photo_{index}.jpg").write_bytes(b"photo")
            return 0, ""

        async def direct(*_args):
            raise AssertionError("Open Graph preview must not win the gallery race")

        with tempfile.TemporaryDirectory() as temporary, \
             patch("media_bot.download.canonical_facebook_post", new=canonical), \
             patch("media_bot.download._gallery_dl", new=gallery), \
             patch("media_bot.download.download_direct", new=direct):
            files, provider = await download_media(parse_link(SHARE), Path(temporary), 1024)
            self.assertEqual(len(files), 2)
        self.assertEqual(provider, "gallery-dl")
        self.assertEqual(seen, [POST])


if __name__ == "__main__":
    unittest.main()
