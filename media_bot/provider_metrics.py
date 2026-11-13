"""Local, URL-free provider attempt statistics."""

from __future__ import annotations

import argparse
import logging
import math
import sqlite3
import threading
import time
from collections import defaultdict
from contextlib import closing
from pathlib import Path

LOG = logging.getLogger(__name__)
OUTCOMES = {"success", "fault", "no_media", "private", "error", "unavailable", "skipped", "cancelled"}
RETENTION_SECONDS = 14 * 86400


def _percentile(values: list[int], percent: int) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percent / 100) - 1)]


class ProviderMetrics:
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
            db.execute("CREATE TABLE IF NOT EXISTS provider_attempts ("
                       "at REAL NOT NULL, platform TEXT NOT NULL, provider TEXT NOT NULL, "
                       "outcome TEXT NOT NULL, elapsed_ms INTEGER NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS provider_attempts_at ON provider_attempts(at)")
            db.execute("CREATE TABLE IF NOT EXISTS provider_winners ("
                       "at REAL NOT NULL, platform TEXT NOT NULL, provider TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS provider_winners_at ON provider_winners(at)")
            self._cleanup(db, time.time())
            self.path = path

    def _cleanup(self, db: sqlite3.Connection, now: float) -> None:
        if now - self._last_cleanup < 86400:
            return
        db.execute("DELETE FROM provider_attempts WHERE at < ?", (now - RETENTION_SECONDS,))
        db.execute("DELETE FROM provider_winners WHERE at < ?", (now - RETENTION_SECONDS,))
        self._last_cleanup = now

    def record(self, platform: str, provider: str, outcome: str, elapsed_ms: int) -> None:
        if outcome not in OUTCOMES:
            raise ValueError("Invalid provider outcome")
        LOG.info("provider_attempt platform=%s provider=%s outcome=%s elapsed_ms=%d",
                 platform, provider, outcome, elapsed_ms)
        if self.path is None:
            return
        try:
            with self._lock, closing(sqlite3.connect(self.path, timeout=5)) as db, db:
                self._cleanup(db, time.time())
                db.execute("INSERT INTO provider_attempts VALUES (?, ?, ?, ?, ?)",
                           (time.time(), platform, provider, outcome, elapsed_ms))
        except (OSError, sqlite3.Error) as exc:
            LOG.warning("Provider metrics could not be stored: %s", exc)

    def record_winner(self, platform: str, provider: str) -> None:
        LOG.info("provider_winner platform=%s provider=%s", platform, provider)
        if self.path is None:
            return
        try:
            with self._lock, closing(sqlite3.connect(self.path, timeout=5)) as db, db:
                self._cleanup(db, time.time())
                db.execute("INSERT INTO provider_winners VALUES (?, ?, ?)",
                           (time.time(), platform, provider))
        except (OSError, sqlite3.Error) as exc:
            LOG.warning("Provider winner could not be stored: %s", exc)

    def report(self, hours: int = 24) -> list[tuple[str, str, dict[str, int]]]:
        if self.path is None:
            return []
        cutoff = time.time() - hours * 3600
        groups: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
        durations: dict[tuple[str, str], list[int]] = defaultdict(list)
        with self._lock, closing(sqlite3.connect(self.path, timeout=5)) as db:
            for platform, provider, outcome, elapsed_ms in db.execute(
                "SELECT platform, provider, outcome, elapsed_ms FROM provider_attempts WHERE at >= ?", (cutoff,)
            ):
                key = (platform, provider)
                groups[key][outcome] += 1
                if outcome == "success":
                    durations[key].append(elapsed_ms)
            try:
                winners = db.execute(
                    "SELECT platform, provider, COUNT(*) FROM provider_winners WHERE at >= ? "
                    "GROUP BY platform, provider", (cutoff,)
                )
                for platform, provider, count in winners:
                    groups[(platform, provider)]["wins"] = count
            except sqlite3.OperationalError as exc:
                if "no such table: provider_winners" not in str(exc):
                    raise
        for key, values in durations.items():
            groups[key]["p50_ms"] = _percentile(values, 50)
            groups[key]["p95_ms"] = _percentile(values, 95)
        for group in groups.values():
            group["wins"] += 0
        return [(platform, provider, dict(groups[(platform, provider)]))
                for platform, provider in sorted(groups)]

    def summary(self, hours: int = 24) -> list[tuple[str, str, str, int, int]]:
        if self.path is None:
            return []
        with self._lock, closing(sqlite3.connect(self.path, timeout=5)) as db:
            return db.execute(
                "SELECT platform, provider, outcome, COUNT(*), CAST(AVG(elapsed_ms) AS INTEGER) "
                "FROM provider_attempts WHERE at >= ? "
                "GROUP BY platform, provider, outcome ORDER BY platform, provider, outcome",
                (time.time() - hours * 3600,),
            ).fetchall()


METRICS = ProviderMetrics()


def main() -> None:
    parser = argparse.ArgumentParser(description="Son 24 saatin sağlayıcı sonuçları; ağ isteği yapmaz.")
    parser.add_argument("--db", type=Path, default=Path("data/provider_metrics.sqlite3"))
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()
    if not args.db.is_file():
        print("Henüz sağlayıcı ölçümü yok. Bot çalıştıktan sonra tekrar deneyin.")
        return
    rows = ProviderMetrics(args.db, initialize=False).report(args.hours)
    if not rows:
        print("Bu sürede sağlayıcı denemesi yok.")
        return
    print("Platform / Sağlayıcı             Kazanan  Başarı  Deneme  Başarı %  Arıza  Atlandı  p50 ms  p95 ms")
    for platform, provider, group in rows:
        attempted = sum(count for outcome, count in group.items()
                        if outcome in OUTCOMES - {"skipped", "cancelled"})
        success = group.get("success", 0)
        rate = 100 * success / attempted if attempted else 0
        print(f"{platform}/{provider:28} {group.get('wins', 0):7} {success:7} {attempted:7} {rate:8.1f} "
              f"{group.get('fault', 0):6} {group.get('skipped', 0):8} "
              f"{group.get('p50_ms', 0):7} {group.get('p95_ms', 0):7}")


if __name__ == "__main__":
    main()
