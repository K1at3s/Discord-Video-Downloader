import unittest
from pathlib import Path
from unittest.mock import patch

from media_bot.links import PostLink
from media_bot.youtube import _yt_dlp, gendownload_options
from media_bot.youtube_view import YoutubeChoiceView


class YoutubeTests(unittest.TestCase):
    def test_qualities_and_audio(self):
        root = "https://dl.gendownload.com/api/stream?t=abc&i="
        data = {"formats": [
            {"label": "1080p", "type": "video", "ext": "mp4", "filesize": 30, "url": root + "0"},
            {"label": "720p", "type": "video", "ext": "mp4", "filesize": 20, "url": root + "1"},
            {"label": "360p", "type": "video", "ext": "mp4", "filesize": 10, "url": root + "2"},
            {"label": "Audio", "type": "audio", "ext": "m4a", "url": root + "3"},
            {"label": "720p", "type": "video", "ext": "mp4", "url": "https://evil.test/api/stream"},
        ]}
        self.assertEqual(gendownload_options(data, "360", 25), [(root + "2", "mp4")])
        self.assertEqual(gendownload_options(data, "720", 25)[:1], [(root + "1", "mp4")])
        self.assertEqual(gendownload_options(data, "1080", 25)[:1], [(root + "1", "mp4")])
        self.assertEqual(gendownload_options(data, "mp3", 25), [(root + "3", "m4a")])

    def test_components_v2_prompt_has_four_buttons(self):
        class Author:
            id = 1

        class Message:
            author = Author()

        view = YoutubeChoiceView(bot=None, source_message=Message(),
                                 link=PostLink("youtube", "https://www.youtube.com/watch?v=jNQXAC9IVRw", "jNQXAC9IVRw"),
                                 title="Example")
        payload = view.to_components()
        self.assertEqual(payload[0]["type"], 17)  # Components V2 container
        rows = [item for item in payload[0]["components"] if item["type"] == 1]
        self.assertEqual([len(row["components"]) for row in rows], [1, 3])


class YoutubeDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_mp3_retries_403_with_another_player_client(self):
        commands = []

        async def fake_run(command, **_kwargs):
            commands.append(command)
            return (1, "HTTP Error 403: Forbidden") if len(commands) == 1 else (0, "")

        link = PostLink("youtube", "https://www.youtube.com/watch?v=TeJzEnHCIWY", "TeJzEnHCIWY")
        with patch("media_bot.youtube._run", new=fake_run):
            code, _ = await _yt_dlp(link, Path("unused"), "mp3", 20 * 1024 * 1024, None)
        self.assertEqual(code, 0)
        self.assertEqual(len(commands), 2)
        self.assertNotEqual(commands[0][commands[0].index("--extractor-args") + 1],
                            commands[1][commands[1].index("--extractor-args") + 1])

    async def test_format_reserves_audio_space_without_remote_ejs_by_default(self):
        commands = []

        async def fake_run(command, **_kwargs):
            commands.append(command)
            return 0, ""

        link = PostLink("youtube", "https://www.youtube.com/watch?v=TeJzEnHCIWY", "TeJzEnHCIWY")
        with patch("media_bot.youtube._run", new=fake_run), patch.dict("os.environ", {"YTDLP_REMOTE_EJS": "0"}):
            await _yt_dlp(link, Path("unused"), "720", 20 * 1024 * 1024, None)
        command = commands[0]
        self.assertNotIn("--remote-components", command)
        formats = command[command.index("-f") + 1]
        self.assertIn("[filesize<15728640]", formats)
        self.assertIn("[filesize<5242880]", formats)
        self.assertIn("[height<=720]", formats)

    async def test_remote_ejs_requires_explicit_setting(self):
        commands = []

        async def fake_run(command, **_kwargs):
            commands.append(command)
            return 0, ""

        link = PostLink("youtube", "https://www.youtube.com/watch?v=TeJzEnHCIWY", "TeJzEnHCIWY")
        with patch("media_bot.youtube._run", new=fake_run), patch.dict("os.environ", {"YTDLP_REMOTE_EJS": "1"}):
            await _yt_dlp(link, Path("unused"), "720", 20 * 1024 * 1024, None)
        self.assertEqual(commands[0][commands[0].index("--remote-components") + 1], "ejs:github")


if __name__ == "__main__":
    unittest.main()
