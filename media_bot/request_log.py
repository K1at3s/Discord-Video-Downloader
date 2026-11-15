"""Per-request activity log with URL/user/guild context and summaries."""

from __future__ import annotations

import argparse
import logging
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOG = logging.getLogger(__name__)
OUTCOMES = {"success", "failure", "pending", "skipped"}
RETENTION_SECONDS = 30 * 86400
ISTANBUL = timezone(timedelta(hours=3))

PLATFORM_LABELS = {
    "instagram": "instagram",
    "tiktok": "tiktok",
    "facebook": "facebook",
    "reddit": "reddit",
    "spotify": "müzik",
    "youtube": "youtube",
    "x": "twitter",
    "threads": "threads",
    "douyin": "douyin",
    "weibo": "weibo",
    "streamable": "streamable",
    "bluesky": "bluesky",
}


def _human_bytes(value: int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def _human_duration(ms: int) -> str:
    if ms <= 0:
        return "0 sn"
    if ms < 1000:
        return f"{ms} ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f} sn"
    minutes, sec = divmod(int(seconds), 60)
    return f"{minutes} dk {sec} sn"


def _human_date(timestamp: float | None) -> str:
    if not timestamp:
        return "-"
    moment = datetime.fromtimestamp(float(timestamp), ISTANBUL)
    return moment.strftime("%d.%m.%Y %H:%M")


def _pct(success: int, failure: int) -> float:
    done = success + failure
    return (100.0 * success / done) if done else 0.0


class RequestLog:
    def __init__(self, path: Path | None = None, *, initialize: bool = True):
        self.path: Path | None = None
        self._lock = threading.Lock()
        self._last_cleanup = 0.0
        if path is not None:
            if initialize:
                self.configure(path)
            else:
                self.path = path

    def configure(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, closing(sqlite3.connect(path, timeout=5)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS request_log ("
                "at REAL NOT NULL, guild_id INTEGER, guild_name TEXT, "
                "channel_id INTEGER, user_id INTEGER, user_name TEXT, "
                "platform TEXT NOT NULL, url TEXT NOT NULL, "
                "outcome TEXT NOT NULL, detail TEXT, "
                "elapsed_ms INTEGER NOT NULL DEFAULT 0, "
                "file_count INTEGER NOT NULL DEFAULT 0, "
                "bytes INTEGER NOT NULL DEFAULT 0)"
            )
            # Migrate older databases that predate the stats columns.
            columns = {row[1] for row in db.execute("PRAGMA table_info(request_log)")}
            for name, definition in (
                ("elapsed_ms", "INTEGER NOT NULL DEFAULT 0"),
                ("file_count", "INTEGER NOT NULL DEFAULT 0"),
                ("bytes", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in columns:
                    db.execute(f"ALTER TABLE request_log ADD COLUMN {name} {definition}")
            db.execute("CREATE INDEX IF NOT EXISTS request_log_at ON request_log(at)")
            db.execute("CREATE INDEX IF NOT EXISTS request_log_user ON request_log(user_id, at)")
            db.execute("CREATE INDEX IF NOT EXISTS request_log_guild ON request_log(guild_id, at)")
            self._cleanup(db, time.time())
            self.path = path

    def _cleanup(self, db: sqlite3.Connection, now: float) -> None:
        if now - self._last_cleanup < 86400:
            return
        db.execute("DELETE FROM request_log WHERE at < ?", (now - RETENTION_SECONDS,))
        self._last_cleanup = now

    def record(
        self,
        *,
        guild_id: int | None,
        guild_name: str | None,
        channel_id: int | None,
        user_id: int | None,
        user_name: str | None,
        platform: str,
        url: str,
        outcome: str,
        detail: str = "",
        elapsed_ms: int = 0,
        file_count: int = 0,
        bytes: int = 0,
    ) -> None:
        if outcome not in OUTCOMES:
            outcome = "failure"
        LOG.info(
            "request_log guild=%s user=%s platform=%s outcome=%s files=%d bytes=%d elapsed_ms=%d url=%s detail=%s",
            guild_id, user_id, platform, outcome, file_count, bytes, elapsed_ms, url, (detail or "")[:200],
        )
        if self.path is None:
            return
        try:
            with self._lock, closing(sqlite3.connect(self.path, timeout=5)) as db, db:
                self._cleanup(db, time.time())
                db.execute(
                    "INSERT INTO request_log "
                    "(at, guild_id, guild_name, channel_id, user_id, user_name, "
                    "platform, url, outcome, detail, elapsed_ms, file_count, bytes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        time.time(),
                        guild_id,
                        (guild_name or "")[:120],
                        channel_id,
                        user_id,
                        (user_name or "")[:120],
                        platform,
                        url[:1000],
                        outcome,
                        (detail or "")[:300],
                        max(0, int(elapsed_ms)),
                        max(0, int(file_count)),
                        max(0, int(bytes)),
                    ),
                )
        except (OSError, sqlite3.Error) as exc:
            LOG.warning("Request log could not be stored: %s", exc)

    def _totals(self, db: sqlite3.Connection, where: str, params: tuple) -> dict:
        row = db.execute(
            "SELECT COUNT(*), "
            "COALESCE(SUM(outcome='success'), 0), "
            "COALESCE(SUM(outcome='failure'), 0), "
            "COALESCE(SUM(outcome='pending'), 0), "
            "COALESCE(SUM(outcome='skipped'), 0), "
            "COALESCE(SUM(file_count), 0), "
            "COALESCE(SUM(bytes), 0), "
            "COALESCE(AVG(CASE WHEN outcome='success' THEN elapsed_ms END), 0), "
            "MAX(at) "
            f"FROM request_log {where}",
            params,
        ).fetchone()
        return {
            "total": int(row[0] or 0),
            "success": int(row[1] or 0),
            "failure": int(row[2] or 0),
            "pending": int(row[3] or 0),
            "skipped": int(row[4] or 0),
            "files": int(row[5] or 0),
            "bytes": int(row[6] or 0),
            "avg_ms": int(row[7] or 0),
            "last_at": float(row[8]) if row[8] else None,
        }

    def _platform_rows(self, db: sqlite3.Connection, where: str, params: tuple) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for platform, total, success, failure, pending, files, size, avg_ms in db.execute(
            "SELECT platform, COUNT(*), "
            "COALESCE(SUM(outcome='success'), 0), "
            "COALESCE(SUM(outcome='failure'), 0), "
            "COALESCE(SUM(outcome='pending'), 0), "
            "COALESCE(SUM(file_count), 0), "
            "COALESCE(SUM(bytes), 0), "
            "COALESCE(AVG(CASE WHEN outcome='success' THEN elapsed_ms END), 0) "
            f"FROM request_log {where} GROUP BY platform ORDER BY COUNT(*) DESC",
            params,
        ):
            result[str(platform)] = {
                "total": int(total or 0),
                "success": int(success or 0),
                "failure": int(failure or 0),
                "pending": int(pending or 0),
                "files": int(files or 0),
                "bytes": int(size or 0),
                "avg_ms": int(avg_ms or 0),
            }
        return result

    def user_summary(self, user_id: int, hours: int = 24) -> dict:
        out: dict = {
            "total": 0, "success": 0, "failure": 0, "pending": 0, "skipped": 0,
            "files": 0, "bytes": 0, "avg_ms": 0, "last_at": None,
            "by_platform": {}, "last_platform": None, "last_url": None,
        }
        if self.path is None:
            return out
        cutoff = time.time() - hours * 3600
        with self._lock, closing(sqlite3.connect(self.path, timeout=5)) as db:
            out.update(self._totals(db, "WHERE user_id = ? AND at >= ?", (user_id, cutoff)))
            out["by_platform"] = self._platform_rows(db, "WHERE user_id = ? AND at >= ?", (user_id, cutoff))
            row = db.execute(
                "SELECT platform, url FROM request_log WHERE user_id = ? AND at >= ? "
                "ORDER BY at DESC LIMIT 1", (user_id, cutoff),
            ).fetchone()
            if row:
                out["last_platform"], out["last_url"] = str(row[0]), str(row[1])
        return out

    def global_summary(self, hours: int = 24) -> dict:
        out: dict = {
            "total": 0, "success": 0, "failure": 0, "pending": 0, "skipped": 0,
            "files": 0, "bytes": 0, "avg_ms": 0, "last_at": None,
            "by_platform": {}, "users": 0, "guilds": 0, "active_users": 0, "active_guilds": 0,
        }
        if self.path is None:
            return out
        cutoff = time.time() - hours * 3600
        day_cutoff = time.time() - 86400
        with self._lock, closing(sqlite3.connect(self.path, timeout=5)) as db:
            out.update(self._totals(db, "WHERE at >= ?", (cutoff,)))
            out["by_platform"] = self._platform_rows(db, "WHERE at >= ?", (cutoff,))
            row = db.execute(
                "SELECT COUNT(DISTINCT user_id), COUNT(DISTINCT guild_id) "
                "FROM request_log WHERE at >= ? AND user_id IS NOT NULL", (cutoff,),
            ).fetchone()
            out["users"] = int(row[0] or 0) if row else 0
            out["guilds"] = int(row[1] or 0) if row else 0
            row = db.execute(
                "SELECT COUNT(DISTINCT user_id), COUNT(DISTINCT guild_id) "
                "FROM request_log WHERE at >= ? AND user_id IS NOT NULL", (day_cutoff,),
            ).fetchone()
            out["active_users"] = int(row[0] or 0) if row else 0
            out["active_guilds"] = int(row[1] or 0) if row else 0
        return out

    def _format_platforms(self, rows: dict[str, dict], limit: int = 12) -> list[str]:
        lines: list[str] = []
        for platform, p in list(rows.items())[:limit]:
            label = PLATFORM_LABELS.get(platform, platform)
            bits = [f"✅ {p['success']} ❌ {p['failure']}"]
            if p.get("pending"):
                bits.append(f"⏳ {p['pending']}")
            if p.get("avg_ms"):
                bits.append(_human_duration(p["avg_ms"]))
            if p.get("bytes"):
                bits.append(_human_bytes(p["bytes"]))
            if p.get("files"):
                bits.append(f"{p['files']} dosya")
            bits.append(f"{p['total']} iş")
            lines.append(f"• {label}: " + " · ".join(bits))
        return lines

    def format_user(self, user_id: int, hours: int = 24, display_name: str | None = None) -> str:
        s = self.user_summary(user_id, hours)
        name = display_name or str(user_id)
        if s["total"] == 0:
            return f"{name} için son {hours} saatte işlem yok."
        lines = [
            f"👤 {name} · {user_id}",
            f"📥 Toplam istek: {s['total']}",
            f"✅ Başarılı: {s['success']}",
            f"❌ Başarısız: {s['failure']}",
        ]
        if s["pending"]:
            lines.append(f"⏳ Bekleyen: {s['pending']}")
        lines.append(f"📊 Başarı oranı: %{_pct(s['success'], s['failure']):.1f}")
        lines.append(f"🕒 Son işlem: {_human_date(s['last_at'])}")
        if s["by_platform"]:
            lines.append("")
            lines.append("Platformlar")
            lines.extend(self._format_platforms(s["by_platform"]))
        return "\n".join(lines)

    def format_panel(self, hours: int = 24, role: str = "Kullanıcı", maintenance: bool = False) -> str:
        s = self.global_summary(hours)
        lines = [
            "🛡️ Yönetim Paneli",
            f"🏷️ Rol: {role}",
            "💎 Premium: —",
            f"👥 Toplam kullanıcı: {s['users']}",
            f"🗂️ Toplam sunucu: {s['guilds']}",
            f"🟢 Bugün aktif: {s['active_users']} kullanıcı / {s['active_guilds']} sunucu",
            f"📥 Toplam istek: {s['total']}",
            f"✅ Başarılı: {s['success']}",
            f"❌ Başarısız: {s['failure']}",
            f"⏳ Bekleyen: {s['pending']}",
            f"📊 Başarı oranı: %{_pct(s['success'], s['failure']):.1f}",
            f"🧰 Bakım: {'Açık' if maintenance else 'Kapalı'}",
            f"🕒 Son işlem: {_human_date(s['last_at'])}",
        ]
        if s["by_platform"]:
            lines.append("")
            lines.append("Platformlar")
            lines.extend(self._format_platforms(s["by_platform"], limit=8))
        return "\n".join(lines)

    def format_text(self, hours: int = 24) -> str:
        return self.format_panel(hours)

    def recent(self, hours: int = 24, limit: int = 10) -> list[dict]:
        if self.path is None:
            return []
        cutoff = time.time() - hours * 3600
        with self._lock, closing(sqlite3.connect(self.path, timeout=5)) as db:
            rows = db.execute(
                "SELECT at, guild_name, user_name, platform, outcome, url, file_count, bytes "
                "FROM request_log WHERE at >= ? ORDER BY at DESC LIMIT ?", (cutoff, limit),
            ).fetchall()
        return [
            {
                "at": float(at), "guild": str(guild or "?"), "user": str(user or "?"),
                "platform": str(platform), "outcome": str(outcome), "url": str(url),
                "files": int(files or 0), "bytes": int(size or 0),
            }
            for at, guild, user, platform, outcome, url, files, size in rows
        ]


REQUESTS = RequestLog()


def main() -> None:
    parser = argparse.ArgumentParser(description="İşlem/indirme isteği kayıt özeti.")
    parser.add_argument("--db", type=Path, default=Path("data/request_log.sqlite3"))
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--user", type=int, default=None)
    args = parser.parse_args()
    if not args.db.is_file():
        print("Henüz işlem kaydı yok. Bot çalıştıktan sonra tekrar deneyin.")
        return
    log = RequestLog(args.db, initialize=False)
    if args.user is not None:
        print(log.format_user(args.user, args.hours))
        return
    print(log.format_panel(args.hours))
    rows = log.recent(args.hours, limit=8)
    if rows:
        print()
        print("Son işlemler:")
        for row in rows:
            print(f"  [{row['outcome']}] {row['platform']} | {row['guild']} / {row['user']} | "
                  f"{row['files']} dosya {_human_bytes(row['bytes'])} | {row['url'][:80]}")


if __name__ == "__main__":
    main()
