"""Tests for the YouTube Components V2 prompt and delivery flow."""

import asyncio
import json
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from media_bot.download import DownloadError
from media_bot.job_slots import JobSlots
from media_bot.links import PostLink
from media_bot.youtube_view import FormatButton, YoutubeChoiceView, deliver_youtube, status_view

LINK = PostLink("youtube", "https://www.youtube.com/watch?v=jNQXAC9IVRw", "jNQXAC9IVRw")


def view_text(view) -> str:
    return json.dumps(view.to_components(), ensure_ascii=False)


def http_error() -> discord.HTTPException:
    response = SimpleNamespace(status=500, reason="Internal Server Error")
    return discord.HTTPException(response, "boom")


class FakeRequests:
    def __init__(self):
        self.updates = []

    def update(self, request_id, **kwargs):
        self.updates.append((request_id, kwargs))
        return True


class DeliverYoutubeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.replies = []
        self.requests = FakeRequests()
        self.enterContext(patch("media_bot.youtube_view.REQUESTS", new=self.requests))

        async def reply(_message, content="", files=None, view=None, **_kwargs):
            self.replies.append({"content": content, "files": list(files or []), "view": view})

        self.bot = SimpleNamespace(cookie_file=None, cobalt_url=None, cobalt_key=None,
                                   _reply=reply)
        self.source = object()

        async def caption(_link, _cookie=None):
            return None

        self.enterContext(patch("media_bot.youtube_view.fetch_caption", new=caption))

        async def meta(_url):
            return None, None

        self.enterContext(patch("media_bot.youtube_view.fetch_youtube_meta", new=meta))

    def patch_download(self, name="youtube.mp4", *, error=None):
        async def download(_link, folder, _choice, _limit, _cookie, _cobalt_url, _key):
            if error is not None:
                raise error
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / name
            path.write_bytes(b"x" * 10)
            return path, "gendownload"

        self.enterContext(patch("media_bot.youtube_view.download_youtube", new=download))

    async def test_mp3_is_renamed_and_sent_as_attachment(self):
        self.patch_download("youtube.m4a")

        async def meta(_url):
            return "Song (Official Video)", "Artist"

        def reply_targets(url):
            return ("source-button", url)

        self.enterContext(patch("media_bot.youtube_view.fetch_youtube_meta", new=meta))
        self.enterContext(patch("media_bot.bot.source_button", new=reply_targets))

        ok, files, label = await deliver_youtube(
            self.bot, self.source, LINK, "mp3", 10 * 1024 * 1024, request_id=7,
            started=time.monotonic() - 1)

        self.assertTrue(ok)
        self.assertEqual(files[0].name, "Artist - Song.mp3")
        self.assertEqual(label, "MP3 · Artist - Song")
        self.assertEqual(self.replies[0]["files"][0].name, "Artist - Song.mp3")
        self.assertEqual(self.replies[0]["view"], ("source-button", LINK.url))
        request_id, update = self.requests.updates[0]
        self.assertEqual(request_id, 7)
        self.assertEqual(update["outcome"], "success")
        self.assertEqual(update["detail"], "youtube:mp3:gendownload")
        self.assertEqual(update["file_count"], 1)
        self.assertEqual(update["bytes"], 10)

    async def test_video_reports_the_delivered_height(self):
        self.patch_download()

        async def height(_path):
            return 480

        captured = []

        def gallery(platform, files, caption, url, part=1):
            captured.append((platform, list(files), caption, url, part))
            return ("gallery", part)

        self.enterContext(patch("media_bot.youtube_view.video_height", new=height))
        self.enterContext(patch("media_bot.bot.media_gallery_view", new=gallery))

        ok, files, label = await deliver_youtube(
            self.bot, self.source, LINK, "720", 10 * 1024 * 1024)

        self.assertTrue(ok)
        self.assertEqual(files[0].name, "youtube_480p.mp4")
        self.assertEqual(label, "480p video (en fazla 720p seçildi)")
        self.assertEqual(captured[0][:2], ("youtube", [files[0]]))
        self.assertEqual(captured[0][4], 1)
        self.assertEqual(self.replies[0]["view"], ("gallery", 1))

    async def test_missing_height_keeps_original_name(self):
        self.patch_download()

        async def height(_path):
            return None

        self.enterContext(patch("media_bot.youtube_view.video_height", new=height))

        ok, files, label = await deliver_youtube(
            self.bot, self.source, LINK, "360", 10 * 1024 * 1024)

        self.assertTrue(ok)
        self.assertEqual(files[0].name, "youtube.mp4")
        self.assertEqual(label, "📱 360p")

    async def test_long_caption_is_split_across_messages(self):
        self.patch_download()

        async def height(_path):
            return 720

        async def caption(_link, _cookie=None):
            return "y" * 4_000

        self.enterContext(patch("media_bot.youtube_view.video_height", new=height))
        self.enterContext(patch("media_bot.youtube_view.fetch_caption", new=caption))

        ok, _files, _label = await deliver_youtube(
            self.bot, self.source, LINK, "720", 10 * 1024 * 1024)

        self.assertTrue(ok)
        self.assertEqual(len(self.replies), 2)
        self.assertEqual(self.replies[0]["view"].__class__.__name__, "LayoutView")
        self.assertEqual(self.replies[1]["files"], [])

    async def test_failure_updates_request_log_and_cancels_caption(self):
        self.patch_download(error=DownloadError("boom"))

        async def caption(_link, _cookie=None):
            await asyncio.sleep(30)

        self.enterContext(patch("media_bot.youtube_view.fetch_caption", new=caption))

        ok, files, detail = await deliver_youtube(
            self.bot, self.source, LINK, "720", 10 * 1024 * 1024, request_id=9)

        self.assertFalse(ok)
        self.assertEqual(files, [])
        self.assertEqual(detail, "boom")
        request_id, update = self.requests.updates[0]
        self.assertEqual((request_id, update["outcome"]), (9, "failure"))
        self.assertIn("youtube:720:boom", update["detail"])

    async def test_http_error_is_reported_as_failure(self):
        self.patch_download(error=http_error())

        ok, _files, _detail = await deliver_youtube(
            self.bot, self.source, LINK, "1080", 10 * 1024 * 1024)

        self.assertFalse(ok)
        self.assertEqual(self.requests.updates[0][1]["outcome"], "failure")

    def test_status_view_escapes_markdown(self):
        text = view_text(status_view("**bold**", "detail"))
        self.assertIn("\\\\*\\\\*bold\\\\*\\\\*", text)
        self.assertIn("detail", text)


class FakeResponse:
    def __init__(self):
        self.messages = []
        self.deferred = False

    async def send_message(self, content, ephemeral=False):
        self.messages.append((content, ephemeral))

    async def defer(self):
        self.deferred = True


class FakeFollowup:
    def __init__(self, error=None):
        self.messages = []
        self.error = error

    async def send(self, content, ephemeral=False):
        if self.error is not None:
            raise self.error
        self.messages.append((content, ephemeral))


class FakePrompt:
    def __init__(self, error=None):
        self.views = []
        self.error = error

    async def edit(self, view):
        if self.error is not None:
            raise self.error
        self.views.append(view)


class FakeInteraction:
    def __init__(self, user_id=1, prompt="prompt", followup_error=None):
        self.user = SimpleNamespace(id=user_id)
        self.response = FakeResponse()
        self.followup = FakeFollowup(followup_error)
        self.message = FakePrompt() if prompt == "prompt" else prompt


class ChoiceViewTests(unittest.IsolatedAsyncioTestCase):
    def make_view(self, *, user_id=1, upload_limit=10 * 1024 * 1024):
        author = SimpleNamespace(id=user_id)
        message = SimpleNamespace(author=author)
        bot = SimpleNamespace(job_slots=JobSlots(asyncio.Semaphore(2)),
                              upload_bytes=upload_limit)
        view = YoutubeChoiceView(bot=bot, source_message=message, link=LINK,
                                 title="Example", request_id=5)
        return view, bot

    async def test_interaction_check_limits_choice_to_author(self):
        view, _bot = self.make_view(user_id=1)
        stranger = FakeInteraction(user_id=2)
        self.assertFalse(await view.interaction_check(stranger))
        self.assertEqual(stranger.response.messages,
                         [("Bu seçim bağlantıyı paylaşan kişiye ait.", True)])
        self.assertTrue(await view.interaction_check(FakeInteraction(user_id=1)))

    async def test_second_choice_is_rejected(self):
        view, _bot = self.make_view()
        view.selected = True
        interaction = FakeInteraction()
        self.assertFalse(await view.choose(interaction, "720"))
        self.assertEqual(interaction.response.messages,
                         [("Bir indirme zaten başlatıldı.", True)])

    async def test_successful_choice_updates_prompt(self):
        view, _bot = self.make_view()
        interaction = FakeInteraction()
        calls = []

        async def deliver(_bot, _message, _link, choice, limit, **_kwargs):
            calls.append((choice, limit))
            return True, [], "720p video (en fazla 720p seçildi)"

        with patch("media_bot.youtube_view.deliver_youtube", new=deliver):
            self.assertTrue(await view.choose(interaction, "720"))

        self.assertTrue(interaction.response.deferred)
        self.assertEqual(calls, [("720", 10 * 1024 * 1024)])
        self.assertIn("gönderildi", view_text(interaction.message.views[-1]))
        self.assertGreaterEqual(len(interaction.message.views), 2)

    async def test_failed_choice_restores_prompt_and_reports_error(self):
        view, _bot = self.make_view()
        interaction = FakeInteraction()

        async def deliver(*_args, **_kwargs):
            return False, [], "boom"

        with patch("media_bot.youtube_view.deliver_youtube", new=deliver):
            self.assertFalse(await view.choose(interaction, "360"))

        self.assertIsInstance(interaction.message.views[-1], YoutubeChoiceView)
        self.assertEqual(interaction.message.views[-1].prompt_message, interaction.message)
        self.assertIn("alınamadı: boom", interaction.followup.messages[0][0])

    async def test_failed_choice_without_prompt_still_reports(self):
        view, _bot = self.make_view()
        interaction = FakeInteraction(prompt=None)

        async def deliver(*_args, **_kwargs):
            return False, [], "boom"

        with patch("media_bot.youtube_view.deliver_youtube", new=deliver):
            self.assertFalse(await view.choose(interaction, "1080"))

        self.assertIn("alınamadı", interaction.followup.messages[0][0])

    async def test_discord_errors_do_not_escape(self):
        view, _bot = self.make_view()
        interaction = FakeInteraction(followup_error=http_error())
        interaction.message = FakePrompt(error=http_error())

        async def deliver(*_args, **_kwargs):
            return True, [], "done"

        with patch("media_bot.youtube_view.deliver_youtube", new=deliver):
            self.assertTrue(await view.choose(interaction, "720"))

        async def fail(*_args, **_kwargs):
            return False, [], "boom"

        with patch("media_bot.youtube_view.deliver_youtube", new=fail):
            self.assertFalse(await view.choose(interaction, "720"))

    async def test_on_timeout_expires_unused_prompt(self):
        view, _bot = self.make_view()
        prompt = FakePrompt()
        view.prompt_message = prompt
        await view.on_timeout()
        self.assertIn("Seçim süresi doldu", view_text(prompt.views[0]))

        view.selected = True
        view.prompt_message = FakePrompt()
        await view.on_timeout()
        self.assertEqual(view.prompt_message.views, [])

        view.selected = False
        view.prompt_message = FakePrompt(error=http_error())
        await view.on_timeout()

        view.prompt_message = None
        await view.on_timeout()

    async def test_format_button_uses_view_choice(self):
        view, _bot = self.make_view()
        button = next(item for item in view.walk_children()
                      if isinstance(item, FormatButton) and item.choice == "720")
        interaction = FakeInteraction()
        with patch.object(YoutubeChoiceView, "choose",
                          new=AsyncMock(return_value=True)) as choose:
            await button.callback(interaction)
        choose.assert_awaited_once_with(interaction, "720")

        stray = FormatButton("360")
        with patch.object(YoutubeChoiceView, "choose",
                          new=AsyncMock(return_value=True)) as choose:
            await stray.callback(interaction)
        choose.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
