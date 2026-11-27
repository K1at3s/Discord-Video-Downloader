import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from media_bot.backup import (
    backup_databases,
    backup_enabled,
    cleanup_stale_temp_dirs,
    database_paths,
    latest_backup,
    prune_backups,
)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bk-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def _make_db(self, name="bot.sqlite3"):
        path = self.tmp / name
        with sqlite3.connect(path) as db:
            db.execute("create table t(x int)")
            db.execute("insert into t(x) values (1)")
        return path

    def test_backup_and_latest_and_prune(self):
        db = self._make_db()
        root = self.tmp / "backups"
        folder = backup_databases([db], root, keep=1, now=1000)
        self.assertIsNotNone(folder)
        self.assertTrue((folder / db.name).is_file())
        self.assertEqual(latest_backup(root), folder)
        # create another backup, keep=1 should prune old
        folder2 = backup_databases([db], root, keep=1, now=2000)
        self.assertIsNotNone(folder2)
        self.assertEqual(len([p for p in root.iterdir() if p.is_dir()]), 1)
        self.assertIsNone(latest_backup(self.tmp / "missing"))

    def test_prune_by_age(self):
        root = self.tmp / "backups"
        old = root / "20200101-000000"
        new = root / "20200102-000000"
        old.mkdir(parents=True)
        new.mkdir(parents=True)
        os.utime(old, (1, 1))
        os.utime(new, (time.time(), time.time()))
        removed = prune_backups(root, keep=10, max_age_days=1, now=time.time())
        self.assertIn(old, removed)
        self.assertTrue(new.exists())

    def test_backup_nothing_and_copy_fallback(self):
        root = self.tmp / "backups"
        self.assertIsNone(backup_databases([self.tmp / "missing.sqlite3"], root))
        # invalid sqlite file triggers fallback copy
        bad = self.tmp / "bad.sqlite3"
        bad.write_text("not sqlite")
        folder = backup_databases([bad], root, keep=7, now=3000)
        self.assertIsNotNone(folder)
        self.assertEqual((folder / bad.name).read_text(), "not sqlite")

    def test_database_paths_backup_enabled_and_cleanup(self):
        db = self.tmp / "bot.sqlite3"
        paths = database_paths(db)
        self.assertIn(self.tmp / "provider_metrics.sqlite3", paths)
        self.assertIn(db, paths)

        with patch.dict(os.environ, {"BACKUP_ENABLED": "no"}):
            self.assertFalse(backup_enabled())
        with patch.dict(os.environ, {"BACKUP_ENABLED": ""}):
            self.assertTrue(backup_enabled(True))

        stale = Path(tempfile.gettempdir()) / "discord-media-test-stale"
        stale.mkdir(exist_ok=True)
        old = time.time() - 48 * 3600
        os.utime(stale, (old, old))
        removed = cleanup_stale_temp_dirs(prefix="discord-media-test-stale", max_age_hours=1)
        self.assertGreaterEqual(removed, 1)
        self.assertFalse(stale.exists())