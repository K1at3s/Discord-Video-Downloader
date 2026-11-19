"""Bound concurrent bot jobs globally and to one active job per user."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager


class JobSlots:
    def __init__(self, total: asyncio.Semaphore):
        self.total = total
        self._users: dict[int, tuple[asyncio.Lock, int]] = {}
        self._active = 0
        self._waiting = 0

    def snapshot(self) -> dict[str, int]:
        """Return live job counts for health/metrics output."""
        return {"active": self._active, "waiting": self._waiting}

    @asynccontextmanager
    async def slot(self, user_id: int, on_wait: Callable[[], Awaitable[None]]) -> AsyncIterator[None]:
        lock, references = self._users.get(user_id, (asyncio.Lock(), 0))
        self._users[user_id] = (lock, references + 1)
        announced = False
        waiting = False
        acquired_user = False
        acquired_total = False
        try:
            if lock.locked() or self.total.locked():
                announced = True
                self._waiting += 1
                waiting = True
                await on_wait()
            await lock.acquire()
            acquired_user = True
            if self.total.locked() and not announced:
                self._waiting += 1
                waiting = True
                await on_wait()
            await self.total.acquire()
            acquired_total = True
            if waiting:
                self._waiting -= 1
                waiting = False
            self._active += 1
            yield
        finally:
            if waiting:
                self._waiting -= 1
            if acquired_total:
                self._active -= 1
                self.total.release()
            if acquired_user:
                lock.release()
            _, references = self._users[user_id]
            if references == 1:
                del self._users[user_id]
            else:
                self._users[user_id] = (lock, references - 1)
