"""Remember which provider actually delivered so repeat requests skip the race."""

from __future__ import annotations

import asyncio
import logging
import os

from .provider_metrics import METRICS

LOG = logging.getLogger(__name__)

_PREFERRED: dict[str, str] = {}


def cached(key: str) -> str | None:
    return _PREFERRED.get(key)


def remember(key: str, provider: str) -> None:
    _PREFERRED[key] = provider


def forget(key: str) -> None:
    _PREFERRED.pop(key, None)


def reset() -> None:
    _PREFERRED.clear()


async def preferred(key: str, platform: str, variant: str = "") -> str | None:
    """In-memory winner first, then the most recent persisted winner."""
    provider = _PREFERRED.get(key)
    if provider:
        return provider
    provider = await asyncio.to_thread(METRICS.last_winner, platform, variant=variant)
    if provider:
        _PREFERRED[key] = provider
    return provider


def hedge_seconds(default: float = 8.0) -> float:
    """How long the preferred provider runs alone before others are started."""
    raw = os.getenv("PROVIDER_HEDGE_SECONDS", "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(0.0, min(value, 300.0))
