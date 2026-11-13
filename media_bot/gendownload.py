"""Free hosted GenDownload API fallback for public posts."""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .direct_providers import _stream, allowed_media_url
from .links import PostLink
from .limits import MAX_MEDIA_ITEMS


def media_options(data: dict, max_bytes: int) -> tuple[list[tuple[str, str]], list[str]]:
    """Return image URLs and video quality options, without audio-only formats."""
    images: list[tuple[str, str]] = []
    videos: list[str] = []
    for item in (data.get("formats") or [])[:MAX_MEDIA_ITEMS * 2]:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not allowed_media_url(url, "gendownload"):
            continue
        parsed = urlsplit(url)
        if parsed.path != "/api/stream":
            continue
        size = item.get("filesize")
        if isinstance(size, (int, float)) and size > max_bytes:
            continue
        kind = item.get("type")
        if kind == "image" and item.get("ext") in {"jpg", "jpeg", "png", "webp"}:
            images.append((url, item["ext"]))
        elif kind == "video" and item.get("ext") == "mp4":
            videos.append(url)
    return images[:MAX_MEDIA_ITEMS], videos[:4]


async def download_gendownload(link: PostLink, folder: Path, max_bytes: int) -> tuple[int, str]:
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5), follow_redirects=False,
                                         trust_env=False, transport=httpx.AsyncHTTPTransport(retries=3)) as client:
                response = await client.post("https://gendownload.com/api/extract", json={"url": link.url})
                response.raise_for_status()
                images, videos = media_options(response.json(), max_bytes)
                count = 0
                for index, (url, extension) in enumerate(images, 1):
                    if await _stream(client, url, "gendownload", folder / f"gendownload_{index}.{extension}", max_bytes):
                        count += 1
                for url in videos:
                    if await _stream(client, url, "gendownload", folder / "gendownload_video.mp4", max_bytes):
                        count += 1
                        break
                expected = len(images) + bool(videos)
                if expected and count < expected:
                    return 0, f"incomplete gallery ({count}/{expected})"
                return count, "" if count else "no usable media"
        except (httpx.ConnectError, httpx.ReadError) as exc:
            if attempt == 0:
                await asyncio.sleep(1)
                continue
            return 0, str(exc)[:200]
        except (httpx.HTTPError, ValueError, TypeError, OSError) as exc:
            return 0, str(exc)[:200]
    return 0, "no usable media"
