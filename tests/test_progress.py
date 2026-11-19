import tempfile
import unittest
from pathlib import Path

from media_bot.progress import ProgressReporter, human_bytes


class FakeStatus:
    def __init__(self):
        self.edits: list[str] = []

    async def edit(self, content=None, **_kwargs):
        self.edits.append(content)


class ProgressTests(unittest.IsolatedAsyncioTestCase):
    def test_human_bytes(self):
        self.assertEqual(human_bytes(None), "0 MB")
        self.assertEqual(human_bytes(0), "0 MB")
        self.assertEqual(human_bytes(900), "900 B")
        self.assertEqual(human_bytes(1536), "1.5 KB")
        self.assertEqual(human_bytes(5 * 1024 * 1024), "5.0 MB")
        self.assertEqual(human_bytes(2 * 1024 ** 3), "2.0 GB")

    async def test_reporter_updates_files_and_estimate(self):
        status = FakeStatus()
        reporter = ProgressReporter(status, "en", interval=0)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "a.mp4"
            path.write_bytes(b"x" * 2048)
            await reporter.on_file([path])
        self.assertEqual(reporter.files, 1)
        self.assertIn("1 file", status.edits[-1])
        await reporter.set_estimate(4096)
        self.assertIn("4.0 KB", status.edits[-1])
        await reporter.uploading(part=1, total=2, force=True)
        self.assertIn("1/2", status.edits[-1])
        await reporter.done(1)
        self.assertIn("Sent 1", status.edits[-1])

    async def test_no_status_message_is_safe(self):
        reporter = ProgressReporter(None, "tr")
        await reporter.downloading(force=True)
        self.assertFalse(reporter.enabled)
