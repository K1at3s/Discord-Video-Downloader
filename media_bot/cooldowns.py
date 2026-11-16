"""Small per-user cooldown used by chat commands."""

from __future__ import annotations

import time


class Cooldowns:
    """Return ``False`` while a user is inside a short command cooldown."""

    def __init__(self, seconds: float = 2.0, *, max_entries: int = 4096):
        self.seconds = max(0.0, float(seconds))
        self.max_entries = max(1, int(max_entries))
        self._seen: dict[int, float] = {}

    def allow(self, user_id: int, now: float | None = None) -> bool:
        if self.seconds <= 0 or not user_id:
            return True
        current = time.monotonic() if now is None else now
        last = self._seen.get(user_id)
        if last is not None and current - last < self.seconds:
            return False
        self._seen[user_id] = current
        if len(self._seen) > self.max_entries:
            cutoff = current - self.seconds * 4
            self._seen = {key: seen for key, seen in self._seen.items() if seen >= cutoff}
            if len(self._seen) > self.max_entries:
                for key in list(self._seen)[: len(self._seen) - self.max_entries]:
                    del self._seen[key]
        return True

    def clear(self) -> None:
        self._seen.clear()
