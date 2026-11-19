import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from media_bot.backup import backup_databases, database_paths, latest_backup, prune_backups
from media_bot.bot import MediaBot
from media_bot.circuit_breaker import CircuitBreaker
from media_bot.cookiefile import cookie_report, cookie_warning
from media_bot.health import HealthServer


def _jar(rows: list[tuple[str, int]]) -> str:
    lines = ["# Netscape HTTP Cookie File"]
    for name, expiry in rows:
        lines.append(f".instagram.com\tTRUE\t/\tTRUE\t{expiry}\t{name}\tvalue")
    return "\n".join(lines) + "\n"


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "bot.sqlite3"
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("CREATE TABLE t (value TEXT)")
            db.execute("INSERT INTO t VALUES ('kept')")

    def tearDown(self):
        self.temp.cleanup()

    def test_backup_copies_rows_into_timestamped_folder(self):
        root = self.root / "backups"
        self.assertIsNone(latest_backup(root))
        folder = backup_databases([self.source], root, now=1_700_000_000)
        self.assertIsNotNone(folder)
        self.assertEqual(folder.parent.name, "backups")
        self.assertEqual(latest_backup(root), folder)
        with closing(sqlite3.connect(folder / "bot.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT value FROM t").fetchone()[0], "kept")

    def test_missing_database_is_ignored(self):
        self.assertIsNone(backup_databases([self.root / "nope.sqlite3"], self.root / "backups"))

    def test_keep_limit_prunes_oldest(self):
        root = self.root / "backups"
        names = ["20240101-000000", "20240102-000000", "20240103-000000"]
        for name in names:
            (root / name).mkdir(parents=True)
        removed = prune_backups(root, keep=2)
        self.assertEqual([path.name for path in removed], names[:1])
        self.assertEqual(sorted(p.name for p in root.iterdir()), names[1:])

    def test_database_paths_include_known_stores(self):
        paths = {path.name for path in database_paths(self.root / "bot.sqlite3")}
        self.assertIn("bot.sqlite3", paths)
        self.assertIn("provider_metrics.sqlite3", paths)
        self.assertIn("request_log.sqlite3", paths)


class CookieReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "cookies.txt"

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_file_warns(self):
        self.assertIn("okunamadı", cookie_warning(Path(self.temp.name) / "missing.txt") or "")

    def test_expired_cookies_warn(self):
        now = time.time()
        self.path.write_text(_jar([("a", int(now - 60)), ("b", int(now + 86400 * 30))]), encoding="utf-8")
        report = cookie_report(self.path, now=now)
        self.assertEqual(report["total"], 2)
        self.assertEqual(report["expired"], 1)
        self.assertIn("1/2", cookie_warning(self.path))

    def test_expiring_cookies_warn_before_they_break(self):
        now = time.time()
        self.path.write_text(_jar([("a", int(now + 86400))]), encoding="utf-8")
        self.assertIn("3 gün içinde", cookie_warning(self.path))

    def test_raw_header_file_is_reported_not_netscape(self):
        self.path.write_text("Cookie: sessionid=abc; csrftoken=def", encoding="utf-8")
        self.assertIn("Netscape", cookie_warning(self.path) or "")
        self.path.write_text("sessionid=abc", encoding="utf-8")
        self.assertIn("Netscape", cookie_warning(self.path) or "")

    def test_fresh_jar_has_no_warning(self):
        self.path.write_text(_jar([("a", int(time.time() + 86400 * 60))]), encoding="utf-8")
        self.assertIsNone(cookie_warning(self.path))


class CircuitSnapshotTests(unittest.TestCase):
    def test_open_circuit_appears_in_snapshot(self):
        breaker = CircuitBreaker(max_failures=1, cooldown=60, clock=lambda: 100.0)
        self.assertTrue(breaker.failure("cobalt", "instagram"))
        snapshot = breaker.snapshot()
        self.assertEqual(snapshot["open_count"], 1)
        self.assertIn("cobalt/instagram", snapshot["open"])

    def test_success_removes_circuit_from_snapshot(self):
        breaker = CircuitBreaker(max_failures=1, clock=lambda: 100.0)
        breaker.failure("cobalt", "youtube")
        breaker.success("cobalt", "youtube")
        self.assertEqual(breaker.snapshot()["open_count"], 0)


class HealthServerTests(unittest.TestCase):
    def test_endpoint_returns_payload(self):
        server = HealthServer(("127.0.0.1", 0), lambda: {"status": "ok", "guilds": 2})
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(payload, {"status": "ok", "guilds": 2})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_unknown_path_is_404(self):
        server = HealthServer(("127.0.0.1", 0), dict)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", timeout=5)
            self.assertEqual(caught.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class BotHealthPayloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bot = MediaBot(
            channel_ids=set(), upload_bytes=0, max_links=3, concurrency=2,
            cookie_file=None, cobalt_url="http://127.0.0.1:9000/",
            cobalt_key=None, gallery_rs_path=None,
            database_path=Path(self.temp.name) / "bot.sqlite3",
        )

    async def asyncTearDown(self):
        await self.bot.close()
        self.temp.cleanup()

    async def test_payload_has_operational_fields(self):
        payload = self.bot.health_payload()
        self.assertEqual(payload["status"], "starting")
        self.assertEqual(payload["concurrency"], 2)
        self.assertEqual(payload["queue"], {"active": 0, "waiting": 0, "capacity": 2})
        self.assertIn("free_mb", payload["disk"])
        self.assertIn("min_free_mb", payload["disk"])
        self.assertIsNone(payload["backup"]["last"])
        self.assertIsNone(payload["backup"]["age_hours"])
        self.assertTrue(payload["cobalt"])
        self.assertIn("open", payload["providers"])
        self.assertEqual(payload["cookies"]["total"], 0)
        self.assertGreaterEqual(payload["uptime_seconds"], 0)

    async def test_no_health_server_when_port_disabled(self):
        with patch.dict(os.environ, {"HEALTH_PORT": "0"}):
            await self.bot.on_ready()
        self.assertIsNone(self.bot._health_server)


if __name__ == "__main__":
    unittest.main()
