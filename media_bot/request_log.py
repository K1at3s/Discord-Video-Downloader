"""Per-request activity log with URL/user/guild context."""

from __future__ import annotations

import argparse
import logging
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

LOG = logging.getLogger(__name__)
OUTCOMES = {"success", "failure", "pending", "skipped"}
RETENTION_SECONDS = 30 * 86400


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
                "outcome TEXT NOT NULL, detail TEXT)"
            )
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
    ) -> None:
        if outcome not in OUTCOMES:
            outcome = "failure"
        LOG.info(
            "request_log guild=%s user=%s platform=%s outcome=%s url=%s detail=%s",
            guild_id, user_id, platform, outcome, url, (detail or "")[:200],
        )
        if self.path is None:
            return
        try:
            with self._lock, closing(sqlite3.connect(path=self.path, timeout=5)) as db, db:
                self._cleanup(db, time.time())
                db.execute(
                    "INSERT INTO request_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    ),
                )
        except (OSError, sqlite3.Error) as exc:
            LOG.warning("Request log could not be stored: %s", exc)

    def summary(self, hours: int = 24) -> dict:
        out = {
            "total": 0,
            "success": 0,
            "failure": 0,
            "pending": 0,
            "skipped": 0,
            "by_platform": {},
            "by_guild": {},
            "by_user": {},
            "recent": [],
        }
        if self.path is None:
            return out
        cutoff = time.time() - hours * 3600
        with self._lock, closing(sqlite3.connect(self.path, timeout=5)) as db:
            row = db.execute(
                "SELECT "
                "COUNT(*), "
                "SUM(CASE WHEN outcome='success' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN outcome='failure' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN outcome='pending' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN outcome='skipped' THEN 1 ELSE 0 END) "
                "FROM request_log WHERE at >= ?",
                (cutoff,),
            ).fetchone()
            if row:
                out["total"] = int(row[0] or 0)
                out["success"] = int(row[1] or 0)
                out["failure"] = int(row[2] or 0)
                out["pending"] = int(row[3] or 0)
                out["skipped"] = int(row[4] or 0)

            for platform, total, success, failure in db.execute(
                "SELECT platform, COUNT(*), "
                "SUM(CASE WHEN outcome='success' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN outcome='failure' THEN 1 ELSE 0 END) "
                "FROM request_log WHERE at >= ? GROUP BY platform ORDER BY COUNT(*) DESC",
                (cutoff,),
            ):
                out["by_platform"][str(platform)] = {
                    "total": int(total or 0),
                    "success": int(success or 0),
                    "failure": int(failure or 0),
                }

            for gid, gname, total, success, failure in db.execute(
                "SELECT guild_id, COALESCE(NULLIF(guild_name,''), CAST(guild_id AS TEXT)), COUNT(*), "
                "SUM(CASE WHEN outcome='success' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN outcome='failure' THEN 1 ELSE 0 END) "
                "FROM request_log WHERE at >= ? GROUP BY guild_id, guild_name ORDER BY COUNT(*) DESC LIMIT 10",
                (cutoff,),
            ):
                out["by_guild"][str(gid)] = {
                    "name": str(gname or gid or "?"),
                    "total": int(total or 0),
                    "success": int(success or 0),
                    "failure": int(failure or 0),
                }

            for uid, uname, total, success, failure in db.execute(
                "SELECT user_id, COALESCE(NULLIF(user_name,''), CAST(user_id AS TEXT)), COUNT(*), "
                "SUM(CASE WHEN outcome='success' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN outcome='failure' THEN 1 ELSE 0 END) "
                "FROM request_log WHERE at >= ? GROUP BY user_id, user_name ORDER BY COUNT(*) DESC LIMIT 10",
                (cutoff,),
            ):
                out["by_user"][str(uid)] = {
                    "name": str(uname or uid or "?"),
                    "total": int(total or 0),
                    "success": int(success or 0),
                    "failure": int(failure or 0),
                }

            for at, gname, uname, platform, outcome, url in db.execute(
                "SELECT at, guild_name, user_name, platform, outcome, url "
                "FROM request_log WHERE at >= ? ORDER BY at DESC LIMIT 10",
                (cutoff,),
            ):
                out["recent"].append(
                    {
                        "at": float(at),
                        "guild": str(gname or "?"),
                        "user": str(uname or "?"),
                        "platform": str(platform),
                        "outcome": str(outcome),
                        "url": str(url),
                    }
                )
        return out

    def format_text(self, hours: int = 24) -> str:
        s = self.summary(hours)
        total = s["total"]
        success = s["success"]
        failure = s["failure"]
        pending = s["pending"]
        if total == 0:
            return f"Son {hours} saatte işlem kaydı yok."
        done = success + failure
        rate = (100 * success / done) if done else 0.0
        lines = [
            f"İşlem özeti (son {hours} saat)",
            f"Toplam: {total} | Başarılı: {success} | Başarısız: {failure} | Beklemede: {pending} | Başarı: %{rate:.1f}",
        ]
        if s["by_platform"]:
            lines.append("Platformlar:")
            for platform, p in list(s["by_platform"].items())[:8]:
                d = p["success"] + p["failure"]
                pr = (100 * p["success"] / d) if d else 0.0
                lines.append(f"- {platform}: toplam={p['total']} başarılı={p['success']} başarısız={p['failure']} (%{pr:.1f})")
        if s["by_guild"]:
            lines.append("Sunucular:")
            for g in list(s["by_guild"].values())[:5]:
                d = g["success"] + g["failure"]
                gr = (100 * g["success"] / d) if d else 0.0
                lines.append(f"- {g['name']}: toplam={g['total']} başarılı={g['success']} başarısız={g['failure']} (%{gr:.1f})")
        if s["by_user"]:
            lines.append("Kullanıcılar:")
            for u in list(s["by_user"].values())[:5]:
                d = u["success"] + u["failure"]
                ur = (100 * u["success"] / d) if d else 0.0
                lines.append(f"- {u['name']}: toplam={u['total']} başarılı={u['success']} başarısız={u['failure']} (%{ur:.1f})")
        if s["recent"]:
            lines.append("Son işlemler:")
            for r in s["recent"][:5]:
                lines.append(f"- [{r['outcome']}] {r['platform']} | {r['guild']} / {r['user']} | {r['url'][:80]}")
        return "\n".join(lines)


REQUESTS = RequestLog()


def main() -> None:
    parser = argparse.ArgumentParser(description="İşlem/indirme isteği kayıt özeti.")
    parser.add_argument("--db", type=Path, default=Path("data/request_log.sqlite3"))
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()
    if not args.db.is_file():
        print("Henüz işlem kaydı yok. Bot çalıştıktan sonra tekrar deneyin.")
        return
    log = RequestLog(args.db, initialize=False)
    print(log.format_text(args.hours))


if __name__ == "__main__":
    main()
