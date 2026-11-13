"""Cookie file helpers shared by the yt-dlp/gallery-dl wrappers."""

from __future__ import annotations

from pathlib import Path


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
