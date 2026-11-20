"""Tests for the ffmpeg-based video size fitting helper."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from media_bot.video_fit import _command, fit_video

# A 10 second clip keeps the bitrate maths easy to reason about.
PROBE_OK = (0, json.dumps({"format": {"duration": "10"}}).encode())


class FakeProcess:
    def __init__(self, returncode=0, stdout=b"", exc=None, on_run=None):
        self.returncode = returncode
        self.stdout = stdout
        self.exc = exc
        self.on_run = on_run
        self.killed = False
        self.args = ()

    async def communicate(self):
        if self.exc is not None:
            raise self.exc
        if self.on_run is not None:
            self.on_run(self.args)
        return self.stdout, b""

    def kill(self):
        self.killed = True


def fake_exec(*, probe=PROBE_OK, encodes=(), probe_exc=None):
    """Build a create_subprocess_exec replacement plus the recorded calls."""
    calls = []
    processes = []
    encodes_done = 0

    async def runner(*args, **_kwargs):
        nonlocal encodes_done
        calls.append(args)
        if args[0] == "ffprobe":
            process = FakeProcess(returncode=probe[0], stdout=probe[1], exc=probe_exc)
        else:
            code, size = encodes[encodes_done] if encodes_done < len(encodes) else encodes[-1]
            encodes_done += 1
            target = Path(args[-1])
            if size is not None:
                target.write_bytes(b"x" * size)
            process = FakeProcess(returncode=code)
        process.args = args
        processes.append(process)
        return process

    return runner, calls, processes


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_stdout_and_zero_for_missing_returncode(self):
        runner, _calls, _processes = fake_exec(probe=(None, b"hello"))
        with patch("media_bot.video_fit.asyncio.create_subprocess_exec", new=runner):
            code, output = await _command("ffprobe", timeout=5)
        self.assertEqual((code, output), (0, b"hello"))

    async def test_timeout_kills_process_and_reraises(self):
        runner, _calls, processes = fake_exec(probe=(0, b""), probe_exc=TimeoutError())
        with patch("media_bot.video_fit.asyncio.create_subprocess_exec", new=runner):
            with self.assertRaises(TimeoutError):
                await _command("ffprobe", timeout=1)
        self.assertTrue(processes[0].killed)


class FitVideoTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_and_empty_sources_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            self.assertFalse(await fit_video(folder / "missing.mp4", folder / "out.mp4", 100))
            empty = folder / "empty.mp4"
            empty.write_bytes(b"")
            self.assertFalse(await fit_video(empty, folder / "out.mp4", 100))

    async def test_small_file_is_moved_without_ffmpeg(self):
        async def explode(*_args, **_kwargs):
            raise AssertionError("ffmpeg must not run for a file that already fits")

        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source = folder / "small.mp4"
            source.write_bytes(b"x" * 10)
            target = folder / "target.mp4"
            with patch("media_bot.video_fit.asyncio.create_subprocess_exec", new=explode):
                self.assertTrue(await fit_video(source, target, 1_000))
            self.assertTrue(target.is_file())
            self.assertFalse(source.exists())

    async def test_force_reencodes_even_a_small_file(self):
        runner, calls, _processes = fake_exec(encodes=((0, 500),))
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source = folder / "small.mp4"
            source.write_bytes(b"x" * 10)
            target = folder / "target.mp4"
            with patch("media_bot.video_fit.asyncio.create_subprocess_exec", new=runner):
                self.assertTrue(await fit_video(source, target, 1_000_000, force=True))
            self.assertTrue(target.is_file())
            self.assertEqual(sum(1 for call in calls if call[0] == "ffmpeg"), 1)

    async def test_probe_failure_returns_false(self):
        for probe in ((1, b""), (0, b""), (0, b"{}"), (0, b'{"format": {"duration": "0"}}'),
                      (0, b"not json")):
            runner, _calls, _processes = fake_exec(probe=probe, encodes=())
            with tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                source = folder / "big.mp4"
                source.write_bytes(b"x" * 50)
                with patch("media_bot.video_fit.asyncio.create_subprocess_exec", new=runner):
                    self.assertFalse(await fit_video(source, folder / "out.mp4", 10, force=True), probe)

    async def test_budget_too_small_for_any_video_bitrate(self):
        runner, calls, _processes = fake_exec(probe=PROBE_OK, encodes=())
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source = folder / "big.mp4"
            source.write_bytes(b"x" * 50)
            with patch("media_bot.video_fit.asyncio.create_subprocess_exec", new=runner):
                self.assertFalse(await fit_video(source, folder / "out.mp4", 100_000, force=True))
            self.assertEqual(sum(1 for call in calls if call[0] == "ffmpeg"), 0)

    async def test_second_attempt_succeeds_after_first_encode_fails(self):
        runner, calls, _processes = fake_exec(encodes=((1, None), (0, 500)))
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source = folder / "big.mp4"
            source.write_bytes(b"x" * 50)
            target = folder / "out.mp4"
            with patch("media_bot.video_fit.asyncio.create_subprocess_exec", new=runner):
                self.assertTrue(await fit_video(source, target, 1_000_000, force=True))
            self.assertEqual(sum(1 for call in calls if call[0] == "ffmpeg"), 2)
            self.assertEqual(target.stat().st_size, 500)

    async def test_oversized_outputs_are_removed(self):
        runner, _calls, _processes = fake_exec(encodes=((0, 2_000_000), (0, 2_000_000)))
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source = folder / "big.mp4"
            source.write_bytes(b"x" * 50)
            target = folder / "out.mp4"
            with patch("media_bot.video_fit.asyncio.create_subprocess_exec", new=runner):
                self.assertFalse(await fit_video(source, target, 1_000_000, force=True))
            self.assertFalse(target.exists())

    async def test_probe_timeout_returns_false(self):
        runner, _calls, processes = fake_exec(probe_exc=TimeoutError())
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source = folder / "big.mp4"
            source.write_bytes(b"x" * 50)
            with patch("media_bot.video_fit.asyncio.create_subprocess_exec", new=runner):
                self.assertFalse(await fit_video(source, folder / "out.mp4", 1_000, force=True))
        self.assertTrue(processes[0].killed)

    async def test_missing_ffmpeg_returns_false(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source = folder / "big.mp4"
            source.write_bytes(b"x" * 50)
            with patch("media_bot.video_fit.asyncio.create_subprocess_exec",
                       side_effect=OSError("ffmpeg not found")):
                self.assertFalse(await fit_video(source, folder / "out.mp4", 1_000, force=True))


if __name__ == "__main__":
    unittest.main()
