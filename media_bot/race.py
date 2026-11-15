"""Return the first successful provider result while cleaning up losing tasks."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

T = TypeVar("T")
LOG = logging.getLogger(__name__)


async def _race(names: Sequence[str], attempt: Callable[[str], Awaitable[T | None]],
                stagger: float, parallel: int) -> T | None:
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


async def _result(task: asyncio.Task) -> T | None:
    try:
        return task.result()
    except Exception:
        LOG.exception("Provider attempt raised unexpectedly")
        return None


async def first_success(names: Sequence[str], attempt: Callable[[str], Awaitable[T | None]],
                        *, stagger: float = 0.6, parallel: int = 3,
                        priority: str | None = None, grace: float = 0.0) -> T | None:
    """Run providers until one succeeds.

    With ``priority`` the known-good provider starts alone; the remaining
    providers are only launched if it fails or exceeds ``grace`` seconds.
    This keeps the common case to a single provider while still recovering
    quickly from a slow or broken one.
    """
    providers = list(names)
    if priority is None or priority not in providers:
        return await _race(providers, attempt, stagger, parallel)

    rest = [name for name in providers if name != priority]
    primary = asyncio.create_task(attempt(priority))
    try:
        if grace > 0:
            done, _ = await asyncio.wait({primary}, timeout=grace)
        else:
            await asyncio.wait({primary})
            done = {primary}
        if primary in done:
            result = await _result(primary)
            if result is not None:
                return result
            if not rest:
                return None
            return await _race(rest, attempt, stagger, parallel)

        # The preferred provider is slow: hedge with the remaining providers.
        others = asyncio.create_task(_race(rest, attempt, stagger, parallel))
        try:
            pending = {primary, others}
            while pending:
                finished, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in finished:
                    result = await _result(task)
                    if result is not None:
                        return result
            return None
        finally:
            others.cancel()
            await asyncio.gather(others, return_exceptions=True)
    finally:
        if not primary.done():
            primary.cancel()
        await asyncio.gather(primary, return_exceptions=True)
