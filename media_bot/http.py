"""Reuse one httpx client per request signature instead of a new TLS handshake.

Provider calls used to open a fresh ``httpx.AsyncClient`` for every attempt. The
clients below are cached per event loop and per connection signature (timeout,
redirect policy, default headers, cookies, transport), so repeated calls to the
same provider keep their HTTP connection alive.
"""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx

_CLIENTS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, httpx.AsyncClient]]" = weakref.WeakKeyDictionary()


def _timeout_key(timeout: Any) -> tuple:
    if isinstance(timeout, httpx.Timeout):
        return ("timeout", timeout.connect, timeout.read, timeout.write, timeout.pool)
    try:
        return ("seconds", float(timeout))
    except (TypeError, ValueError):
        return ("repr", repr(timeout))


def _cookies_key(cookies: Any) -> tuple:
    if cookies is None:
        return ()
    try:
        return tuple(sorted((str(key), str(value)) for key, value in cookies.items()))
    except (AttributeError, TypeError):
        return (repr(cookies),)


def _transport_key(transport: Any) -> tuple:
    if transport is None:
        return ()
    return (type(transport).__name__, getattr(transport, "_retries", None),
            getattr(transport, "_verify", None), getattr(transport, "_proxy", None))


def _signature(*, timeout: Any, follow_redirects: bool, headers: Mapping[str, str] | None,
               cookies: Any, trust_env: bool, transport: Any, kwargs: dict[str, Any]) -> str:
    parts = (
        _timeout_key(timeout),
        bool(follow_redirects),
        tuple(sorted((str(key).lower(), str(value)) for key, value in (headers or {}).items())),
        _cookies_key(cookies),
        bool(trust_env),
        _transport_key(transport),
        tuple(sorted((str(key), repr(value)) for key, value in kwargs.items())),
    )
    return repr(parts)


def _bucket() -> dict[str, httpx.AsyncClient]:
    loop = asyncio.get_running_loop()
    clients = _CLIENTS.get(loop)
    if clients is None:
        clients = {}
        _CLIENTS[loop] = clients
    return clients


@asynccontextmanager
async def shared_client(*, timeout: Any = 5.0, follow_redirects: bool = False,
                        headers: Mapping[str, str] | None = None, cookies: Any = None,
                        trust_env: bool = True, transport: Any = None,
                        **kwargs: Any) -> AsyncIterator[httpx.AsyncClient]:
    """Yield a cached client; the client is closed by :func:`close_shared_clients`."""
    key = _signature(timeout=timeout, follow_redirects=follow_redirects, headers=headers,
                     cookies=cookies, trust_env=trust_env, transport=transport, kwargs=kwargs)
    clients = _bucket()
    client = clients.get(key)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=timeout, follow_redirects=follow_redirects,
                                   headers=headers, cookies=cookies, trust_env=trust_env,
                                   transport=transport, **kwargs)
        clients[key] = client
    yield client


async def close_shared_clients() -> None:
    """Close clients belonging to the running loop (safe to call more than once)."""
    try:
        clients = _CLIENTS.pop(asyncio.get_running_loop(), {})
    except RuntimeError:
        return
    for client in clients.values():
        try:
            await client.aclose()
        except Exception:
            pass
