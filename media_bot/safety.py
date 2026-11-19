"""Outbound URL checks that keep user links away from local/private hosts.

The bot only accepts an allowlist of public post URLs, but short links and
page fetches can redirect. These helpers validate the scheme, credentials,
port and resolved addresses before an HTTP request is made.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx

_BLOCKED_NAMES = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata",
    "metadata.google.internal",
}
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")


def _public_ip(text: str) -> bool:
    try:
        address = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return False
    return address.is_global and not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def is_safe_http_url(url: str) -> bool:
    """Synchronous structural check; does not resolve DNS."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in {"http", "https"}:
        return False
    if parts.username or parts.password:
        return False
    try:
        port = parts.port
    except ValueError:
        return False
    if port not in {None, 80, 443}:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return False
    if host in _BLOCKED_NAMES or host.endswith(_BLOCKED_SUFFIXES):
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        # A normal DNS name. Resolution is checked by ensure_public_url().
        return True
    return _public_ip(host)


async def ensure_public_url(url: str, *, timeout: float = 3.0) -> bool:
    """Structural check plus DNS resolution; all answers must be public."""
    if not is_safe_http_url(url):
        return False
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    try:
        infos = await asyncio.wait_for(
            asyncio.to_thread(socket.getaddrinfo, host, None, 0, socket.SOCK_STREAM),
            timeout=timeout,
        )
    except (OSError, ValueError, TimeoutError):
        return False
    if not infos:
        return False
    for info in infos:
        address = info[4][0]
        if not _public_ip(address):
            return False
    return True


async def safe_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_redirects: int = 4,
    timeout: float = 3.0,
    **kwargs,
) -> httpx.Response:
    """GET a URL while validating every redirect target."""
    current = url
    kwargs.setdefault("follow_redirects", False)
    for _ in range(max_redirects + 1):
        if not await ensure_public_url(current, timeout=timeout):
            raise ValueError("unsafe outbound URL")
        response = await client.get(current, **kwargs)
        if response.is_redirect:
            location = response.headers.get("location")
            if not location:
                raise ValueError("redirect without location")
            current = urljoin(current, location)
            continue
        return response
    raise ValueError("too many redirects")
