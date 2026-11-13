"""Short-lived provider health state shared by concurrent download jobs."""

from __future__ import annotations

import asyncio
import re
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Callable

import httpx


@dataclass
class _State:
    failures: int = 0
    opened_until: float = 0.0
    probing: bool = False


class CircuitBreaker:
    def __init__(self, max_failures: int = 3, cooldown: float = 150,
                 clock: Callable[[], float] = time.monotonic):
        if max_failures < 1 or cooldown <= 0:
            raise ValueError("Invalid circuit breaker limits")
        self.max_failures = max_failures
        self.cooldown = cooldown
        self.clock = clock
        self._states: dict[tuple[str, str], _State] = {}
        self._lock = threading.Lock()

    def allow(self, provider: str, platform: str) -> bool:
        with self._lock:
            state = self._states.setdefault((provider, platform), _State())
            if state.opened_until:
                if self.clock() < state.opened_until or state.probing:
                    return False
                state.probing = True
            return True

    def success(self, provider: str, platform: str) -> None:
        with self._lock:
            self._states[(provider, platform)] = _State()

    def failure(self, provider: str, platform: str) -> bool:
        """Record a provider fault; return True if its circuit is now open."""
        with self._lock:
            state = self._states.setdefault((provider, platform), _State())
            state.failures += 1
            if state.probing or state.failures >= self.max_failures:
                state.opened_until = self.clock() + self.cooldown
                state.probing = False
                return True
            return False

    def neutral(self, provider: str, platform: str) -> None:
        """A private/missing post is not a provider outage."""
        self.success(provider, platform)

    def abandon(self, provider: str, platform: str) -> None:
        """A cancelled half-open probe must not permanently hold its slot."""
        with self._lock:
            state = self._states.get((provider, platform))
            if state and state.probing:
                state.probing = False
                state.opened_until = self.clock()


def provider_fault(error: str | BaseException | None) -> bool:
    if error is None:
        return False
    if isinstance(error, (asyncio.TimeoutError, TimeoutError, ssl.SSLError,
                          httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code >= 500
    message = str(error).casefold()
    if re.search(r"\b(?:401|403|404|429)\b|private|login required|authrequired", message):
        return False
    return bool(re.search(
        r"timed?\s*out|timeout|zaman aşımı|\bssl\b|\btls\b|certificate|handshake|"
        r"connection (?:refused|reset)|connecterror|readerror|"
        r"\b5\d\d\b|bad gateway|service unavailable|gateway timeout",
        message,
    ))


BREAKERS = CircuitBreaker()
