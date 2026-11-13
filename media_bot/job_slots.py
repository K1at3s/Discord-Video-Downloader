"""Bound concurrent bot jobs globally and to one active job per user."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager


class JobSlots:
    def __init__(self, total: asyncio.Semaphore):
        self.total = total
        self._users: dict[int, tuple[asyncio.Lock, int]] = {}

    @asynccontextmanager
    async def slot(self, user_id: int, on_wait: Callable[[], Awaitable[None]]) -> AsyncIterator[None]:
        lock, references = self._users.get(user_id, (asyncio.Lock(), 0))
        self._users[user_id] = (lock, references + 1)
        announced = False
        acquired_user = False
        acquired_total = False
        try:
            if lock.locked() or self.total.locked():
                await on_wait()
                announced = True
            await lock.acquire()
            acquired_user = True
            if self.total.locked() and not announced:
                await on_wait()
            await self.total.acquire()
            acquired_total = True
            yield
        finally:
            if acquired_total:
                self.total.release()
            if acquired_user:
                lock.release()
            _, references = self._users[user_id]
            if references == 1:
                del self._users[user_id]
            else:
                self._users[user_id] = (lock, references - 1)
