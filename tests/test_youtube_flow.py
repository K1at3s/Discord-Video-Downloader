"""Focused tests for the YouTube download routes and quality helpers."""

import asyncio
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from media_bot.circuit_breaker import CircuitBreaker
from media_bot.download import DownloadError
from media_bot.links import PostLink
from media_bot.youtube import (
    _pick_uploadable,
    _selected_file,
    _to_mp3,
    download_youtube,
    fetch_youtube_meta,
    fetch_youtube_title,
    gendownload_options,
    suggest_choice,
    video_height,
)

LINK = PostLink("youtube", "https://www.youtube.com/watch?v=jNQXAC9IVRw", "jNQXAC9IVRw")


class FakeMetrics:
    def __init__(self):
        self.records = []
        self.winners = []

    def record(self, *args):
        self.records.append(args)

    def record_winner(self, *args):
        self.winners.append(args)


class FakeCache:
    def __init__(self, entry=None, *, enabled=False, copied=()):
        self.enabled = enabled
        self.entry = entry
        self.copied = list(copied)
        self.puts = []

    def get(self, *_args):
        return self.entry

    def copy_to(self, _entry, folder):
        folder.mkdir(parents=True, exist_ok=True)
        paths = []
        for name, size in self.copied:
            path = folder / name
            path.write_bytes(b"x" * size)
            paths.append(path)
        return paths

    def put(self, *args):
        self.puts.append(args)


class FakeResponse:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error

    def raise_for_status(self):
        if self.error is not None:
            raise self.error

    def json(self):
        return self.data


class FakeHttp:
    def __init__(self, response):
        self.response = response
        self.calls = []

    @asynccontextmanager
    async def __call__(self, **_kwargs):
        yield self

    async def get(self, url, params=None):
        self.calls.append((url, params))
        return self.response


class SuggestChoiceTests(unittest.TestCase):
    def test_audio_and_invalid_values_pass_through(self):
        self.assertEqual(suggest_choice("mp3", 1024), "mp3")
        self.assertEqual(suggest_choice("auto", 1024), "auto")
        self.assertEqual(suggest_choice("480", 20 * 1024 * 1024), "480")

    def test_quality_is_capped_by_upload_limit(self):
        self.assertEqual(suggest_choice("1080", 40 * 1024 * 1024), "720")
        self.assertEqual(suggest_choice("1080", 7 * 1024 * 1024), "360")
        self.assertEqual(suggest_choice("720", 2 * 1024 * 1024), "360")
        self.assertEqual(suggest_choice("1080", 60 * 1024 * 1024), "1080")


class GendownloadOptionTests(unittest.TestCase):
    def test_oversized_candidates_are_kept_as_fallback(self):
        root = "https://dl.gendownload.com/api/stream?i="
        data = {"formats": [
            {"label": "360p", "type": "video", "ext": "mp4", "filesize": 50, "url": root + "0"},
            {"label": "240p", "type": "video", "ext": "mp4", "filesize": 5, "url": root + "1"},
        ]}
        self.assertEqual([url for url, _ in gendownload_options(data, "360", 10)],
                         [root + "1", root + "0"])

    def test_audio_prefers_mp3_and_limits_results(self):
        root = "https://dl.gendownload.com/api/stream?i="
        formats = [{"label": "Audio", "type": "audio", "ext": "m4a", "url": root + "0"},
                   {"label": "Audio", "type": "audio", "ext": "mp3", "url": root + "1"}]
        formats += [{"label": f"{height}p", "type": "video", "ext": "mp4",
                     "url": root + str(height)} for height in (144, 240, 360, 480, 720)]
        self.assertEqual(gendownload_options({"formats": formats}, "mp3", 10**9),
                         [(root + "1", "mp3"), (root + "0", "m4a")])

    def test_invalid_items_are_skipped(self):
        data = {"formats": ["nope", {"url": 5}, {"url": "https://evil.test/api/stream"},
                            {"label": "720p", "type": "video", "ext": "webm",
                             "url": "https://dl.gendownload.com/api/stream?i=1"},
                            {"label": "no height", "type": "video", "ext": "mp4",
                             "url": "https://dl.gendownload.com/api/stream?i=2"}]}
        self.assertEqual(gendownload_options(data, "720", 10**9), [])
        self.assertEqual(gendownload_options({"formats": None}, "720", 10**9), [])


class MetadataTests(unittest.IsolatedAsyncioTestCase):
    async def test_oembed_values_are_truncated(self):
        http = FakeHttp(FakeResponse({"title": "t" * 300, "author_name": "a" * 200}))
        with patch("media_bot.youtube.shared_client", new=http):
            title, author = await fetch_youtube_meta(LINK.url)
        self.assertEqual((len(title), len(author)), (180, 90))
        self.assertEqual(http.calls[0][0], "https://www.youtube.com/oembed")
        self.assertEqual(http.calls[0][1]["url"], LINK.url)
        with patch("media_bot.youtube.shared_client", new=FakeHttp(FakeResponse({"title": "T"}))):
            self.assertEqual(await fetch_youtube_title(LINK.url), "T")

    async def test_http_and_shape_errors_return_none(self):
        cases = [
            FakeHttp(FakeResponse(error=httpx.ConnectError("down"))),
            FakeHttp(FakeResponse({"title": 5, "author_name": 7})),
        ]
        for http in cases:
            with patch("media_bot.youtube.shared_client", new=http):
                self.assertEqual(await fetch_youtube_meta(LINK.url), (None, None))


class SelectionTests(unittest.IsolatedAsyncioTestCase):
    def test_selected_file_matches_extension_and_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "empty.mp4").write_bytes(b"")
            (folder / "too_big.mp4").write_bytes(b"x" * 500)
            (folder / "ok.mp4").write_bytes(b"x" * 10)
            (folder / "song.mp3").write_bytes(b"x" * 10)
            self.assertEqual(_selected_file(folder, "720", 100).name, "ok.mp4")
            self.assertEqual(_selected_file(folder, "mp3", 100).name, "song.mp3")
            self.assertIsNone(_selected_file(folder, "720", 5))

    async def test_oversized_video_is_fitted_down(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "big.mp4").write_bytes(b"x" * 5000)

            async def fit(source, target, max_bytes, **_kwargs):
                target.write_bytes(b"x" * 10)
                return True

            with patch("media_bot.youtube.fit_video", new=fit):
                selected = await _pick_uploadable(folder, "720", 100, 10_000)
            self.assertEqual(selected.name, "youtube_fit_720p.mp4")

    async def test_fit_failures_return_none(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "big.mp4").write_bytes(b"x" * 5000)

            async def fit(*_args, **_kwargs):
                return False

            with patch("media_bot.youtube.fit_video", new=fit):
                self.assertIsNone(await _pick_uploadable(folder, "720", 100, 10_000))
            (folder / "big.mp4").unlink()
            (folder / "song.mp3").write_bytes(b"x" * 5000)
            self.assertIsNone(await _pick_uploadable(folder, "mp3", 100, 10_000))


class FakeProcess:
    def __init__(self, returncode=0, stdout=b"", exc=None, on_run=None, target=None):
        self.returncode = returncode
        self.stdout = stdout
        self.exc = exc
        self.on_run = on_run
        self.target = target
        self.killed = False

    async def communicate(self):
        if self.exc is not None:
            error, self.exc = self.exc, None
            raise error
        if self.on_run is not None:
            self.on_run(self.target)
        return self.stdout, b""

    def kill(self):
        self.killed = True


class VideoHeightTests(unittest.IsolatedAsyncioTestCase):
    async def _probe(self, process):
        async def runner(*_args, **_kwargs):
            return process

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "video.mp4"
            path.write_bytes(b"x")
            with patch("media_bot.youtube.asyncio.create_subprocess_exec", new=runner):
                return await video_height(path)

    async def test_height_is_parsed(self):
        self.assertEqual(await self._probe(FakeProcess(stdout=b"720\n")), 720)

    async def test_zero_and_errors_return_none(self):
        self.assertIsNone(await self._probe(FakeProcess(returncode=1, stdout=b"720")))
        self.assertIsNone(await self._probe(FakeProcess(stdout=b"not-a-number")))
        self.assertIsNone(await self._probe(FakeProcess(stdout=b"0")))

    async def test_timeout_returns_none(self):
        process = FakeProcess(exc=TimeoutError())
        self.assertIsNone(await self._probe(process))
        self.assertTrue(process.killed)

    async def test_missing_ffprobe_returns_none(self):
        async def runner(*_args, **_kwargs):
            raise OSError("missing")

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "video.mp4"
            path.write_bytes(b"x")
            with patch("media_bot.youtube.asyncio.create_subprocess_exec", new=runner):
                self.assertIsNone(await video_height(path))


class ToMp3Tests(unittest.IsolatedAsyncioTestCase):
    async def _convert(self, process, max_bytes=100, *, prepare=None):
        async def runner(*_args, **_kwargs):
            return process

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        source = folder / "source.m4a"
        source.write_bytes(b"x" * 10)
        target = folder / "song.mp3"
        process.target = target
        if prepare is not None:
            prepare(target)
        with patch("media_bot.youtube.asyncio.create_subprocess_exec", new=runner):
            return await _to_mp3(source, target, max_bytes), target

    async def test_successful_conversion(self):
        def on_run(target):
            target.write_bytes(b"x" * 10)

        ok, target = await self._convert(FakeProcess(on_run=on_run), prepare=lambda path: None)
        self.assertTrue(ok)
        self.assertTrue(target.is_file())

    async def test_failed_or_oversized_conversion_is_removed(self):
        def on_run(target):
            target.write_bytes(b"x" * 500)

        ok, target = await self._convert(FakeProcess(on_run=on_run))
        self.assertFalse(ok)
        self.assertFalse(target.exists())

        ok, target = await self._convert(FakeProcess(returncode=1, on_run=on_run))
        self.assertFalse(ok)
        self.assertFalse(target.exists())

    async def test_timeout_kills_process_and_cleans_up(self):
        def prepare(target):
            target.write_bytes(b"partial")

        process = FakeProcess(exc=TimeoutError())
        with self.assertRaises(TimeoutError):
            await self._convert(process, prepare=prepare)
        self.assertTrue(process.killed)

class DownloadYoutubeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.metrics = FakeMetrics()
        self.cache = FakeCache()
        self.memory = SimpleNamespace(remembered=[], forgotten=[])
        self.enterContext(patch("media_bot.youtube.METRICS", new=self.metrics))
        self.enterContext(patch("media_bot.youtube.MEDIA_CACHE", new=self.cache))
        self.enterContext(patch("media_bot.youtube.BREAKERS", new=CircuitBreaker(max_failures=1)))

        async def preferred(*_args, **_kwargs):
            return None

        async def ranked(_platform, names, **_kwargs):
            return list(names)

        def hedge(default=8.0, **_kwargs):
            return default

        def remember(key, provider):
            self.memory.remembered.append((key, provider))

        def forget(key):
            self.memory.forgotten.append(key)

        self.enterContext(patch("media_bot.youtube.provider_memory.preferred", new=preferred))
        self.enterContext(patch("media_bot.youtube.provider_memory.ranked", new=ranked))
        self.enterContext(patch("media_bot.youtube.provider_memory.hedge_seconds", new=hedge))
        self.enterContext(patch("media_bot.youtube.provider_memory.remember", new=remember))
        self.enterContext(patch("media_bot.youtube.provider_memory.forget", new=forget))

        async def sequential(providers, attempt, **_kwargs):
            for provider in providers:
                result = await attempt(provider)
                if result is not None:
                    return result
            return None

        self.enterContext(patch("media_bot.youtube.first_success", new=sequential))

    async def test_invalid_choice_is_rejected(self):
        with self.assertRaises(ValueError):
            await download_youtube(LINK, self.folder, "4k", 1024, None, None, None)

    async def test_gendownload_success_updates_cache_and_metrics(self):
        async def gendownload(_link, folder, _choice, _limit, _ceiling):
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "youtube.mp4").write_bytes(b"x" * 100)
            return 1, ""

        with patch("media_bot.youtube._gendownload", new=gendownload):
            path, provider = await download_youtube(
                LINK, self.folder, "720", 20 * 1024 * 1024, None, None, None)

        self.assertEqual(provider, "gendownload")
        self.assertTrue(path.is_file())
        self.assertEqual(self.metrics.winners, [("youtube", "gendownload", "720")])
        self.assertEqual(len(self.cache.puts), 1)
        self.assertEqual(self.cache.puts[0][5], f"720|{20 * 1024 * 1024}")
        self.assertEqual(self.memory.remembered, [("youtube:720", "gendownload")])

    async def test_quality_is_capped_before_downloading(self):
        seen = []

        async def gendownload(_link, folder, choice, _limit, _ceiling):
            seen.append(choice)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "youtube.mp4").write_bytes(b"x" * 100)
            return 1, ""

        with patch("media_bot.youtube._gendownload", new=gendownload):
            await download_youtube(LINK, self.folder, "1080", 1024 * 1024, None, None, None)

        self.assertEqual(seen, ["360"])
        self.assertEqual(self.cache.puts[0][5], f"360|{1024 * 1024}")

    async def test_media_cache_hit_skips_providers(self):
        self.cache = FakeCache(SimpleNamespace(provider="cobalt"), enabled=True,
                               copied=[("cached.mp4", 100)])
        self.enterContext(patch("media_bot.youtube.MEDIA_CACHE", new=self.cache))

        async def gendownload(*_args, **_kwargs):
            raise AssertionError("providers must not run on a cache hit")

        with patch("media_bot.youtube._gendownload", new=gendownload):
            path, provider = await download_youtube(
                LINK, self.folder, "720", 20 * 1024 * 1024, None, None, None)

        self.assertEqual(provider, "cobalt")
        self.assertEqual(path.name, "cached.mp4")
        self.assertEqual(self.metrics.winners, [("youtube", "cobalt-cache")])

    async def test_unusable_cache_entry_falls_back_to_providers(self):
        self.cache = FakeCache(SimpleNamespace(provider="cobalt"), enabled=True,
                               copied=[("cached.mp4", 25 * 1024 * 1024)])
        self.enterContext(patch("media_bot.youtube.MEDIA_CACHE", new=self.cache))

        async def gendownload(_link, folder, _choice, _limit, _ceiling):
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "youtube.mp4").write_bytes(b"x" * 100)
            return 1, ""

        with patch("media_bot.youtube._gendownload", new=gendownload):
            _path, provider = await download_youtube(
                LINK, self.folder, "720", 20 * 1024 * 1024, None, None, None)

        self.assertEqual(provider, "gendownload")

    async def test_cobalt_runs_when_configured(self):
        calls = []

        async def cobalt(url, folder, ceiling, cobalt_url, key, audio=False, video_quality=720):
            calls.append((url, ceiling, cobalt_url, key, audio, video_quality))
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "youtube.mp4").write_bytes(b"x" * 100)
            return 1, ""

        with patch("media_bot.youtube.download_cobalt", new=cobalt):
            _path, provider = await download_youtube(
                LINK, self.folder, "720", 1024 * 1024, None, "http://cobalt:9000", "key")

        self.assertEqual(provider, "cobalt")
        self.assertEqual(calls[0][2:4], ("http://cobalt:9000", "key"))

    async def test_errors_are_retried_on_the_next_provider(self):
        async def gendownload(*_args, **_kwargs):
            raise TimeoutError("slow")

        async def ytdlp(_link, folder, _choice, _limit, _ceiling, _cookie):
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "youtube.mp4").write_bytes(b"x" * 100)
            return 0, ""

        with patch("media_bot.youtube._gendownload", new=gendownload), \
                patch("media_bot.youtube._yt_dlp", new=ytdlp):
            _path, provider = await download_youtube(
                LINK, self.folder, "720", 1024 * 1024, None, None, None)

        self.assertEqual(provider, "yt-dlp")
        self.assertIn(("youtube", "gendownload", "fault"),
                      [record[:3] for record in self.metrics.records])

    async def test_open_circuit_is_skipped(self):
        breaker = CircuitBreaker(max_failures=1)
        breaker.failure("gendownload", "youtube")
        self.enterContext(patch("media_bot.youtube.BREAKERS", new=breaker))

        async def gendownload(*_args, **_kwargs):
            raise AssertionError("an open circuit must not be called")

        async def ytdlp(_link, folder, _choice, _limit, _ceiling, _cookie):
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "youtube.mp4").write_bytes(b"x" * 100)
            return 0, ""

        with patch("media_bot.youtube._gendownload", new=gendownload), \
                patch("media_bot.youtube._yt_dlp", new=ytdlp):
            _path, provider = await download_youtube(
                LINK, self.folder, "720", 1024 * 1024, None, None, None)

        self.assertEqual(provider, "yt-dlp")
        self.assertIn(("youtube", "gendownload", "skipped", 0), self.metrics.records)

    async def test_all_providers_failing_raises_with_reasons(self):
        async def gendownload(*_args, **_kwargs):
            return 0, "private post"

        async def ytdlp(*_args, **_kwargs):
            return 1, "no video formats"

        with patch("media_bot.youtube._gendownload", new=gendownload), \
                patch("media_bot.youtube._yt_dlp", new=ytdlp):
            with self.assertRaises(DownloadError) as raised:
                await download_youtube(LINK, self.folder, "720", 20 * 1024 * 1024, None, None, None)

        message = str(raised.exception)
        self.assertIn("gendownload: private post", message)
        self.assertIn("yt-dlp: no video formats", message)
        self.assertEqual(self.memory.forgotten, ["youtube:720"])

    async def test_cancelled_attempt_is_abandoned(self):
        async def gendownload(*_args, **_kwargs):
            await asyncio.sleep(10)

        task = asyncio.create_task(download_youtube(
            LINK, self.folder, "720", 1024 * 1024, None, None, None))
        with patch("media_bot.youtube._gendownload", new=gendownload):
            for _ in range(5):
                await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        outcomes = [record[2] for record in self.metrics.records]
        self.assertIn("cancelled", outcomes)


if __name__ == "__main__":
    unittest.main()
