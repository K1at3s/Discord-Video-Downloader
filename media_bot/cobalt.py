"""Third download route: a self-hosted Cobalt API instance."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from .limits import MAX_MEDIA_ITEMS

LOG = logging.getLogger(__name__)
MAX_ITEMS = MAX_MEDIA_ITEMS
EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp3"}


def _same_instance(media_url: str, api_url: str) -> bool:
    """Only fetch media through the configured Cobalt instance's tunnel."""
    try:
        media = urlsplit(media_url)
        api = urlsplit(api_url)
        return media.scheme == api.scheme and media.hostname == api.hostname and media.port == api.port and not media.username and not media.password
    except ValueError:
        return False


def _extension(filename: str, kind: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in EXTENSIONS:
        return suffix
    return {"photo": ".jpg", "video": ".mp4", "gif": ".gif"}.get(kind, ".mp4")


async def _stream_file(client: httpx.AsyncClient, url: str, output: Path, api_url: str, max_bytes: int) -> bool:
    if not _same_instance(url, api_url):
        LOG.warning("Cobalt returned a non-instance URL; refusing it")
        return False
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        size = 0
        with output.open("wb") as destination:
            async for chunk in response.aiter_bytes(64 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    destination.close()
                    output.unlink(missing_ok=True)
                    return False
                destination.write(chunk)
    if size == 0:
        output.unlink(missing_ok=True)
        return False
    return True


async def download_cobalt(source_url: str, folder: Path, max_bytes: int, api_url: str, api_key: str | None = None, *, audio: bool = False, video_quality: int = 720) -> tuple[int, str]:
    """Return (number of files, error). Unsupported response modes fall back."""
    base = api_url.rstrip("/") + "/"
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Api-Key " + api_key
    timeout = httpx.Timeout(45, connect=5)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, headers=headers, trust_env=False) as client:
            body = {"url": source_url, "alwaysProxy": True, "localProcessing": "disabled", "videoQuality": str(video_quality)}
            if audio:
                body.update({"downloadMode": "audio", "audioFormat": "mp3", "audioBitrate": "128"})
            elif "youtube.com/watch" in source_url:
                body["youtubeVideoContainer"] = "mp4"
            response = await client.post(base, json=body)
            if response.status_code == 400:
                try:
                    failure = response.json().get("error") or {}
                except (ValueError, AttributeError):
                    failure = {}
                code = failure.get("code") if isinstance(failure, dict) else ""
                if isinstance(code, str) and code.endswith(".empty"):
                    return 0, "no usable media"
            response.raise_for_status()
            result = response.json()
            status = result.get("status")
            if status in {"tunnel", "redirect"}:
                items = [(result.get("url"), result.get("filename", "media.mp3" if audio else "media.mp4"), "video")]
            elif status == "picker":
                items = [(item.get("url"), item.get("filename", ""), item.get("type", "video")) for item in result.get("picker", [])[:MAX_ITEMS] if isinstance(item, dict)]
            else:
                error = result.get("error") or status or "unknown response"
                return 0, str(error)[:200]
            count = 0
            for index, (url, filename, kind) in enumerate(items[:MAX_ITEMS], 1):
                if not isinstance(url, str):
                    continue
                output = folder / f"cobalt_{index}{_extension(str(filename), str(kind))}"
                if await _stream_file(client, urljoin(base, url), output, base, max_bytes):
                    count += 1
            if items and count < len(items[:MAX_ITEMS]):
                return 0, f"incomplete gallery ({count}/{len(items[:MAX_ITEMS])})"
            return count, "" if count else "no uploadable media"
    except (httpx.HTTPError, ValueError, OSError, TypeError) as exc:
        return 0, str(exc)[:200]
