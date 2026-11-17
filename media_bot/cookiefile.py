"""Cookie file helpers shared by the yt-dlp/gallery-dl wrappers."""

from __future__ import annotations

import time
from pathlib import Path


def cookie_report(cookie_file: Path | None, *, days: int = 3,
                  now: float | None = None) -> dict:
    """Summarise a Netscape cookie jar without exposing cookie values.

    Returns totals plus the nearest expiry so the owner can refresh the file
    before Instagram/Threads start failing silently.
    """
    report = {
        "path": str(cookie_file) if cookie_file else "",
        "total": 0,
        "session": 0,
        "expired": 0,
        "expiring": 0,
        "soonest": 0.0,
        "netscape": False,
        "readable": False,
    }
    if cookie_file is None:
        return report
    try:
        text = cookie_file.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return report
    report["readable"] = True
    report["netscape"] = "\t" in text or text.lstrip().startswith("#")
    current = time.time() if now is None else now
    horizon = current + max(0, days) * 86400
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#HttpOnly_"):
            stripped = stripped[len("#HttpOnly_"):]
        elif stripped.startswith("#"):
            continue
        fields = stripped.split("\t")
        if len(fields) < 7:
            continue
        report["total"] += 1
        try:
            expiry = int(float(fields[4]))
        except (TypeError, ValueError):
            expiry = 0
        if expiry <= 0:
            report["session"] += 1
            continue
        if not report["soonest"] or expiry < report["soonest"]:
            report["soonest"] = float(expiry)
        if expiry <= current:
            report["expired"] += 1
        elif expiry <= horizon:
            report["expiring"] += 1
    return report


def cookie_warning(cookie_file: Path | None, *, days: int = 3) -> str | None:
    """Return a Turkish warning when cookies are missing, stale or not a jar."""
    report = cookie_report(cookie_file, days=days)
    if not report["path"]:
        return None
    if not report["readable"]:
        return "COOKIE_FILE okunamadı veya erişilemedi; dosya yolunu kontrol edin."
    if not report["total"]:
        if report["netscape"]:
            return "COOKIE_FILE boş görünüyor."
        return ("COOKIE_FILE Netscape çerez biçiminde değil; yt-dlp/gallery-dl "
                "bu dosyayı kullanamaz. Tarayıcı eklentisiyle dışa aktarın.")
    if report["expired"]:
        return (f"COOKIE_FILE içindeki {report['expired']}/{report['total']} çerez süresi "
                "dolmuş. Instagram/Threads için yenileyin.")
    if report["expiring"]:
        return (f"COOKIE_FILE içindeki {report['expiring']}/{report['total']} çerez "
                f"{days} gün içinde dolacak. Şimdiden yenileyin.")
    return None


def netscape_jar(cookie_file: Path | None) -> Path | None:
    """Return the file only when it is a Netscape jar, not a raw Cookie header.

    Users often paste the browser's `Cookie:` header into COOKIE_FILE. yt-dlp
    and gallery-dl cannot read that shape and log a warning for every attempt,
    so the raw-header file is simply left to our own httpx providers.
    """
    if cookie_file is None:
        return None
    try:
        head = cookie_file.read_text(encoding="utf-8-sig", errors="replace")[:4096]
    except OSError:
        return None
    return cookie_file if "\t" in head or head.startswith("#") else None
