"""Remember which provider actually delivered so repeat requests skip the race."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Sequence

from .provider_metrics import METRICS, _percentile

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


def hedge_seconds(default: float = 8.0, *, platform: str = "", provider: str | None = None) -> float:
    """How long the preferred provider runs alone before others are started.

    Measured p95 of that provider (when enough samples exist) adapts the wait:
    a fast provider gets hedged later, a slow one earlier. An explicit env value
    still wins so the behaviour can be pinned during an incident.
    """
    raw = os.getenv("PROVIDER_HEDGE_SECONDS", "").strip()
    if raw:
        try:
            return max(0.0, min(float(raw), 300.0))
        except ValueError:
            pass
    if platform and provider:
        p95 = METRICS.p95_ms(platform, provider)
        if p95 > 0:
            return max(3.0, min(20.0, p95 / 1000 * 1.2))
    return default


async def ranked(platform: str, names: Sequence[str], *, min_samples: int = 4) -> list[str]:
    """Order providers by measured p95 success latency; unknown ones keep their order."""
    stats = await asyncio.to_thread(METRICS.successful_latencies, platform)
    measured: dict[str, int] = {
        name: _percentile(values, 95)
        for name, values in stats.items()
        if len(values) >= min_samples
    }
    original = list(names)
    positions = {name: index for index, name in enumerate(original)}
    return sorted(original, key=lambda name: (
        1 if measured.get(name, 0) <= 0 else 0,
        measured.get(name, 0),
        positions[name],
    ))

