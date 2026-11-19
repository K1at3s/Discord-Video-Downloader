"""Free-space checks shared by downloads, health output and scripts."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

DEFAULT_MIN_FREE_MB = 512


class LowDiskError(RuntimeError):
    """Raised when the download target has less space than the configured floor."""


def min_free_mb() -> int:
    raw = os.getenv("MIN_FREE_DISK_MB", "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MIN_FREE_MB
    return max(0, min(value, 1_000_000))


def free_mb(path: Path) -> int | None:
    """Return free megabytes for the nearest existing parent, or ``None``."""
    probe = Path(path)
    try:
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        return int(shutil.disk_usage(probe).free // (1024 * 1024))
    except OSError:
        return None


def disk_status(path: Path, *, minimum_mb: int | None = None) -> dict[str, int | bool | None]:
    minimum = min_free_mb() if minimum_mb is None else max(0, int(minimum_mb))
    free = free_mb(path)
    return {
        "free_mb": free,
        "min_free_mb": minimum,
        "low": free is not None and minimum > 0 and free < minimum,
    }


def ensure_free_disk(path: Path, *, minimum_mb: int | None = None) -> None:
    status = disk_status(path, minimum_mb=minimum_mb)
    if status["low"]:
        raise LowDiskError(
            f"Yetersiz boş disk alanı: {status['free_mb']} MB kaldı "
            f"(en az {status['min_free_mb']} MB gerekli). Disk temizliği yapıp tekrar deneyin."
        )
