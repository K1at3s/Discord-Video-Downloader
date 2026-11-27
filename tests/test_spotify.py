import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx

from media_bot.bot import MediaBot
from media_bot.download import DownloadError
from media_bot.links import PostLink
from media_bot.spotify import SpotifyTrack, choose_match, fetch_track, find_audio_source, parse_track_page


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


class SpotifyExtraTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_track_page_ignores_bad_meta_and_rejects_missing(self):
        page = '<div>ignore</div><meta property="og:title"/><meta name="music:duration" content="abc"/>'
        self.assertIsNone(parse_track_page(page))
        long_page = ('<meta property="og:title" content="' + 'A' * 201 + '"/>'
                     '<meta name="music:musician_description" content="Artist"/>')
        self.assertIsNone(parse_track_page(long_page))

    async def test_fetch_track_rejects_non_track_url(self):
        link = PostLink("spotify", "https://open.spotify.com/album/abc")
        with self.assertRaises(DownloadError):
            await fetch_track(link)

    async def test_fetch_track_http_error_and_missing_track(self):
        link = PostLink("spotify", "https://open.spotify.com/track/abc123")

        class FakeClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): return False
            async def get(self, _url):
                raise httpx.HTTPError("boom")

        with patch("media_bot.spotify.shared_client", return_value=FakeClient()):
            with self.assertRaises(DownloadError):
                await fetch_track(link)

        class OkClient(FakeClient):
            async def get(self, _url):
                return SimpleNamespace(text="<html></html>", raise_for_status=lambda: None)

        with patch("media_bot.spotify.shared_client", return_value=OkClient()):
            with self.assertRaises(DownloadError):
                await fetch_track(link)

    def test_choose_match_filters_and_scores(self):
        track = SpotifyTrack("FLEX", "Khontkar, Tuby, Motive", 196)
        entries = [
            "bad",
            {"id": "badid", "title": "Khontkar FLEX", "duration": 196, "channel": "Khontkar"},
            {"id": "aaaaaaaaaaa", "title": "Wrong title", "duration": 196, "channel": "Khontkar"},
            {"id": "bbbbbbbbbbb", "title": "Khontkar FLEX", "duration": 10, "channel": "Khontkar"},
            {"id": "ccccccccccc", "title": "Khontkar FLEX", "duration": 250, "channel": "Khontkar"},
            {"id": "ddddddddddd", "title": "Khontkar FLEX", "duration": 196, "channel": "Random"},
            {"id": "eeeeeeeeeee", "title": "Khontkar FLEX official", "duration": 196,
             "channel": "Topic", "channel_is_verified": True},
        ]
        self.assertEqual(choose_match(entries, track), "eeeeeeeeeee")
        self.assertIsNone(choose_match([], SpotifyTrack("X", "Y", 100)))

    async def test_find_audio_source_success_timeout_and_bad_json(self):
        track = SpotifyTrack("FLEX", "Khontkar, Tuby, Motive", 196)

        async def make_proc(stdout=b"", raise_timeout=False, raise_cancel=False):
            proc = AsyncMock()
            calls = {"n": 0}

            async def communicate():
                calls["n"] += 1
                if calls["n"] == 1:
                    if raise_cancel:
                        raise asyncio.CancelledError()
                    if raise_timeout:
                        raise TimeoutError()
                return stdout, b""

            proc.communicate.side_effect = communicate
            proc.kill = Mock()
            return proc

        good_payload = json.dumps({"entries": [{"id": "Go8Uobt-EVg", "title": "Khontkar FLEX Motive", "duration": 196, "channel": "Khontkar"}]}).encode()
        with patch("media_bot.spotify.asyncio.create_subprocess_exec", AsyncMock(return_value=await make_proc(good_payload))):
            link = await find_audio_source(track)
        self.assertEqual(link.post_id, "Go8Uobt-EVg")

        with patch("media_bot.spotify.asyncio.create_subprocess_exec", AsyncMock(return_value=await make_proc(b"not-json"))):
            with self.assertRaises(DownloadError):
                await find_audio_source(track)

        with patch("media_bot.spotify.asyncio.create_subprocess_exec", AsyncMock(return_value=await make_proc(raise_timeout=True))):
            with self.assertRaises(DownloadError):
                await find_audio_source(track)

        with patch("media_bot.spotify.asyncio.create_subprocess_exec", AsyncMock(return_value=await make_proc(raise_cancel=True))):
            with self.assertRaises(asyncio.CancelledError):
                await find_audio_source(track)


if __name__ == "__main__":
    unittest.main()
