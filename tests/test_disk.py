import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from media_bot.disk import (
    DEFAULT_MIN_FREE_MB,
    LowDiskError,
    disk_status,
    ensure_free_disk,
    free_mb,
    min_free_mb,
)


class DiskGuardTests(unittest.TestCase):
    def test_min_free_reads_env_and_falls_back(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(min_free_mb(), DEFAULT_MIN_FREE_MB)
        with patch.dict(os.environ, {"MIN_FREE_DISK_MB": "128"}):
            self.assertEqual(min_free_mb(), 128)
        with patch.dict(os.environ, {"MIN_FREE_DISK_MB": "0"}):
            self.assertEqual(min_free_mb(), 0)
        with patch.dict(os.environ, {"MIN_FREE_DISK_MB": "not-a-number"}):
            self.assertEqual(min_free_mb(), DEFAULT_MIN_FREE_MB)

    def test_status_reports_free_space(self):
        with tempfile.TemporaryDirectory() as temporary:
            status = disk_status(Path(temporary), minimum_mb=1)
        self.assertIsNotNone(status["free_mb"])
        self.assertEqual(status["min_free_mb"], 1)
        self.assertFalse(status["low"])

    def test_guard_raises_when_space_is_below_floor(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            status = disk_status(path, minimum_mb=10**9)
            self.assertTrue(status["low"])
            with self.assertRaises(LowDiskError):
                ensure_free_disk(path, minimum_mb=10**9)
            ensure_free_disk(path, minimum_mb=0)

    def test_free_mb_returns_none_for_unreadable_path(self):
        with patch("media_bot.disk.shutil.disk_usage", side_effect=OSError("boom")):
            self.assertIsNone(free_mb(Path.cwd()))


if __name__ == "__main__":
    unittest.main()
