"""Best-effort media size estimates from HTTP ``Content-Length`` headers."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urljoin

import httpx

from .http import shared_client
from .links import PostLink
from .safety import is_safe_http_url

LOG = logging.getLogger(__name__)
USER_AGENT = "DiscordVideoBot/1.0 (size estimate)"


def _length_from_headers(headers) -> int | None:
    raw = headers.get("content-length")
    if raw and raw.isdecimal():
        return int(raw)
    content_range = headers.get("content-range") or ""
    _, separator, total = content_range.partition("/")
    if separator and total.isdecimal():
        return int(total)
    return None


async def _head_size(client: httpx.AsyncClient, url: str) -> int | None:
    current = url
    for _ in range(3):
        if not is_safe_http_url(current):
            return None
        try:
            response = await client.head(current)
            if response.status_code in {400, 403, 405}:
                response = await client.get(current, headers={"Range": "bytes=0-0"})
        except (httpx.HTTPError, ValueError):
            return None
        if response.is_redirect:
            location = response.headers.get("location")
            if not location:
                return None
            current = urljoin(current, location)
            continue
        if response.status_code >= 400:
            return None
        length = _length_from_headers(response.headers)
        return length if length and length > 0 else None
    return None


async def estimate_urls_bytes(urls: list[str], *, timeout: float = 3.0,
                              max_urls: int = 10) -> int | None:
    unique: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if isinstance(url, str) and url and url not in seen:
            seen.add(url)
            unique.append(url)
        if len(unique) >= max_urls:
            break
    if not unique:
        return None
    try:
        async with shared_client(
            timeout=httpx.Timeout(timeout, connect=min(2.0, timeout)),
            follow_redirects=False, trust_env=False,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            sizes = await asyncio.gather(
                *(_head_size(client, url) for url in unique),
                return_exceptions=True,
            )
    except (httpx.HTTPError, OSError, ValueError):
        return None
    known = [size for size in sizes if isinstance(size, int) and size > 0]
    return sum(known) if known else None


async def estimate_reddit_media_bytes(link: PostLink, *, timeout: float = 4.0) -> int | None:
    """Fetch the public Reddit JSON only to estimate the media total."""
    if link.platform != "reddit" or not link.post_id:
        return None
    try:
        from .direct_providers import reddit_media_urls

        async with shared_client(
            timeout=httpx.Timeout(timeout, connect=min(2.0, timeout)),
            follow_redirects=False, trust_env=False,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = await client.get(
                f"https://www.reddit.com/comments/{link.post_id}.json",
                params={"raw_json": "1"},
            )
            response.raise_for_status()
            payload = response.json()
        data = payload[0]["data"]["children"][0]["data"]
        urls = [url for url, _kind in reddit_media_urls(data)]
    except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError):
        return None
    return await estimate_urls_bytes(urls, timeout=timeout)
