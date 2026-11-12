import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from media_bot.bot import MediaBot
from media_bot.links import PostLink
from media_bot.spotify import SpotifyTrack, choose_match, parse_track_page


class SpotifyTests(unittest.TestCase):
    def test_spotify_page_metadata(self):
        page = ('<meta property="og:title" content="FLEX"/>'
                '<meta name="music:musician_description" content="Khontkar, Tuby, Motive"/>'
                '<meta name="music:duration" content="196"/>')
        self.assertEqual(parse_track_page(page), SpotifyTrack("FLEX", "Khontkar, Tuby, Motive", 196))

    def test_duration_and_artist_reject_wrong_song(self):
        track = SpotifyTrack("FLEX", "Khontkar, Tuby, Motive", 196)
        entries = [
            {"id": "aaaaaaaaaaa", "title": "Khontkar & Motive - FLEX (Official Audio)",
             "duration": 105, "channel": "fan"},
            {"id": "Go8Uobt-EVg", "title": "Khontkar - FLEX ft. Motive (prod. by Tuby)",
             "duration": 196, "channel": "Khontkar", "channel_is_verified": True},
        ]
        self.assertEqual(choose_match(entries, track), "Go8Uobt-EVg")


class SpotifyFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_track_is_sent_as_audio_attachment(self):
        link = PostLink("spotify", "https://open.spotify.com/track/6Cd4tpGCTQnxkKVWsy6AUA")
        source = PostLink("youtube", "https://www.youtube.com/watch?v=Go8Uobt-EVg", "Go8Uobt-EVg")
        sent = []

        async def fetch(_link):
            return SpotifyTrack("FLEX", "Khontkar, Tuby, Motive", 196)

        async def find(_track):
            return source

        async def download(_source, folder, _choice, *_args):
            file = folder / "source.mp3"
            file.write_bytes(b"audio")
            return file, "test"

        async def reply(_message, content="", files=None, *, view=None):
            sent.append((content, [path.name for path in files or []], view))

        bot = SimpleNamespace(_reply=reply, upload_bytes=20_000_000, cookie_file=None,
                              cobalt_url=None, cobalt_key=None)
        with patch("media_bot.bot.fetch_track", new=fetch), patch("media_bot.bot.find_audio_source", new=find), patch("media_bot.bot.download_youtube", new=download):
            await MediaBot._handle_spotify(bot, object(), link)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], "")
        self.assertEqual(sent[0][1], ["FLEX.mp3"])
        self.assertIsNone(sent[0][2])


if __name__ == "__main__":
    unittest.main()
