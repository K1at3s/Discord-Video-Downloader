"""Return the first successful provider result while cleaning up losing tasks."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

T = TypeVar("T")
LOG = logging.getLogger(__name__)


async def first_success(names: Sequence[str], attempt: Callable[[str], Awaitable[T | None]],
                        *, stagger: float = 0.6, parallel: int = 3) -> T | None:
    slots = asyncio.Semaphore(parallel)

    async def run(index: int, name: str) -> T | None:
        if index:
            await asyncio.sleep(index * stagger)
        async with slots:
            try:
                return await attempt(name)
            except Exception:
                LOG.exception("Provider attempt %s raised unexpectedly", name)
                return None

    tasks = [asyncio.create_task(run(index, name)) for index, name in enumerate(names)]
    try:
        for completed in asyncio.as_completed(tasks):
            result = await completed
            if result is not None:
                return result
        return None
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
