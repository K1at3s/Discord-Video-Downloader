"""Owner-controlled maintenance mode, persisted between restarts."""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

LOG = logging.getLogger(__name__)

DEFAULT_MESSAGE = "🛠️ Bot şu anda bakımda. Yeni indirmeler geçici olarak kapalı, lütfen daha sonra tekrar deneyin."


class Maintenance:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._path: Path | None = None
        self._active = False
        self._message = DEFAULT_MESSAGE

    @property
    def active(self) -> bool:
        return self._active

    @property
    def message(self) -> str:
        return self._message

    def configure(self, path: Path | None = None, *, enabled: bool | None = None, message: str = "") -> None:
        """Load persisted state; an explicit ``enabled`` value wins over the file."""
        if message.strip():
            self._message = message.strip()[:300]
        self._path = path
        stored: bool | None = None
        if path is not None:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                stored = bool(data.get("active", False))
            except (OSError, ValueError):
                stored = None
        self._active = bool(enabled) if enabled is not None else bool(stored)
        if path is not None:
            self._save()

    def set(self, active: bool) -> bool:
        with self._lock:
            self._active = bool(active)
            self._save()
        return self._active

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps({"active": self._active}), encoding="utf-8")
        except OSError as exc:
            LOG.warning("Maintenance state could not be stored: %s", exc)


MAINTENANCE = Maintenance()


def env_enabled(name: str = "MAINTENANCE_MODE") -> bool | None:
    """Return the env override, or ``None`` when it is not configured."""
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return None
    return raw not in {"0", "false", "no", "off", "hayir", "hayır"}
