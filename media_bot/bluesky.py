"""Public Bluesky posts via the official AppView API."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from .direct_providers import _stream, allowed_media_url
from .download import _run
from .links import PostLink

API = "https://public.api.bsky.app/xrpc"
ADULT_LABELS = {"porn", "sexual", "nudity", "graphic-media"}
MODERATION_DID = "did:plc:ar7c4by46qjdydhdevvrndac"
VIDEO_SLOTS = asyncio.Semaphore(2)
_DID_CACHE: dict[str, tuple[str, list[dict], float]] = {}


@dataclass(frozen=True)
class BlueskyPost:
    caption: str
    media: tuple[tuple[str, str], ...]
    adult: bool = False
    alt_texts: tuple[str, ...] = ()


def _caption(post: dict) -> str:
    author = post.get("author") or {}
    record = post.get("record") or {}
    name = author.get("displayName") or author.get("handle") or "Bluesky"
    handle = author.get("handle") or ""
    header = f"{name} (@{handle})" if handle else name
    parts = [header, record.get("text") or ""]
    embed = post.get("embed") or {}
    quoted = embed.get("record") if isinstance(embed.get("record"), dict) else {}
    quoted_post = quoted.get("record") if isinstance(quoted.get("record"), dict) else {}
    if quoted_post:
        quoted_author = quoted.get("author") or {}
        quoted_name = quoted_author.get("displayName") or quoted_author.get("handle") or "Alıntı"
        quoted_text = quoted_post.get("text") or ""
        if quoted_text:
            parts.append(f"Alıntı · {quoted_name}\n{quoted_text}")
    elif quoted.get("$type") in {"app.bsky.embed.record#viewNotFound", "app.bsky.embed.record#viewBlocked", "app.bsky.embed.record#viewDetached"}:
        parts.append("Alıntılanan gönderi kullanılamıyor.")
    return "\n\n".join(part for part in parts if part)


def parse_post(data: dict) -> BlueskyPost | None:
    thread = data.get("thread") or {}
    post = thread.get("post") if isinstance(thread, dict) else None
    if not isinstance(post, dict):
        return None
    embed = post.get("embed") or {}
    media_embed = embed.get("media") if embed.get("$type") == "app.bsky.embed.recordWithMedia#view" else embed
    media_embed = media_embed if isinstance(media_embed, dict) else {}
    media: list[tuple[str, str]] = []
    alt_texts: list[str] = []
    if media_embed.get("$type") in {"app.bsky.embed.images#view", "app.bsky.embed.gallery#view"}:
        for image in media_embed.get("images") or media_embed.get("items") or []:
            url = image.get("fullsize") if isinstance(image, dict) else None
            if isinstance(url, str) and allowed_media_url(url, "bluesky"):
                media.append(("image", url))
                alt_texts.append(str(image.get("alt") or "")[:1024])
    elif media_embed.get("$type") == "app.bsky.embed.video#view":
        url = media_embed.get("playlist")
        if isinstance(url, str) and _video_url(url):
            media.append(("video", url))
            alt_texts.append(str(media_embed.get("alt") or "")[:1024])
    labels = (post.get("labels") or []) + ((post.get("author") or {}).get("labels") or [])
    self_labels = ((post.get("record") or {}).get("labels") or {}).get("values") or []
    adult = any(isinstance(label, dict) and label.get("val") in ADULT_LABELS
                for label in [*labels, *self_labels])
    return BlueskyPost(_caption(post), tuple(media), adult, tuple(alt_texts))


def _video_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https" and parts.hostname == "video.bsky.app"
                and parts.port in {None, 443} and not parts.username and not parts.password
                and parts.path.endswith(".m3u8"))
    except ValueError:
        return False


async def _api_get(client: httpx.AsyncClient, method: str, params: dict) -> httpx.Response:
    url = f"{API}/{method}"
    response = await client.get(url, params=params)
    if response.status_code == 429:
        try:
            delay = min(max(float(response.headers.get("Retry-After", "1")), 0), 3)
        except ValueError:
            delay = 1
        await asyncio.sleep(delay)
        response = await client.get(url, params=params)
    response.raise_for_status()
    return response


async def fetch_bluesky_post(link: PostLink) -> BlueskyPost | None:
    match = re.fullmatch(r"https://bsky\.app/profile/([A-Za-z0-9.:-]+)/post/([A-Za-z0-9]+)", link.url)
    if not match:
        return None
    actor, rkey = match.groups()
    timeout = httpx.Timeout(8, connect=4)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False,
                                 headers={"atproto-accept-labelers": MODERATION_DID}) as client:
        profile_labels: list[dict] = []
        if actor.startswith("did:"):
            did = actor
        else:
            cached = _DID_CACHE.get(actor)
            if cached and cached[2] > time.monotonic():
                did, profile_labels = cached[0], cached[1]
            else:
                profile = await _api_get(client, "app.bsky.actor.getProfile", {"actor": actor})
                profile_data = profile.json()
                did = profile_data.get("did")
                profile_labels = profile_data.get("labels") or []
                if isinstance(did, str):
                    if len(_DID_CACHE) >= 512:
                        _DID_CACHE.clear()
                    _DID_CACHE[actor] = (did, profile_labels, time.monotonic() + 600)
        if not isinstance(did, str) or not re.fullmatch(r"did:(?:plc|web):[A-Za-z0-9.:%_-]+", did):
            return None
        uri = f"at://{did}/app.bsky.feed.post/{rkey}"
        response = await _api_get(client, "app.bsky.feed.getPostThread", {"uri": uri, "depth": 0})
        result = parse_post(response.json())
        if result and any(isinstance(label, dict) and label.get("val") in ADULT_LABELS
                          for label in profile_labels):
            return BlueskyPost(result.caption, result.media, True, result.alt_texts)
        return result


def _variants(playlist_url: str, content: str) -> list[str]:
    """Return trusted HLS variants in descending bandwidth order."""
    ranked: list[tuple[int, str]] = []
    bandwidth = 0
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-STREAM-INF:"):
            match = re.search(r"\bBANDWIDTH=(\d+)", line)
            bandwidth = int(match.group(1)) if match else 0
        elif bandwidth and line and not line.startswith("#"):
            candidate = urljoin(playlist_url, line)
            if _video_url(candidate):
                ranked.append((bandwidth, candidate))
            bandwidth = 0
    return [url for _, url in sorted(ranked, reverse=True)]


async def download_bluesky_media(post: BlueskyPost, folder: Path, max_bytes: int) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5), follow_redirects=False,
                                 trust_env=False) as client:
        for index, (kind, url) in enumerate(post.media, 1):
            if kind == "image":
                output = folder / f"bluesky_{index}.jpg"
                try:
                    if await _stream(client, url, "bluesky", output, max_bytes):
                        files.append(output)
                except httpx.HTTPError:
                    output.unlink(missing_ok=True)
            elif kind == "video" and _video_url(url):
                output = folder / f"bluesky_{index}.mp4"
                async with VIDEO_SLOTS:
                    candidates = [url]
                    while candidates:
                        candidate = candidates.pop(0)
                        try:
                            code, _ = await _run(
                                ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", candidate,
                                 "-c", "copy", "-movflags", "+faststart", str(output)],
                                timeout=120, folder=folder, max_total_bytes=max_bytes,
                            )
                            if code == 0 and output.is_file() and 0 < output.stat().st_size <= max_bytes:
                                files.append(output)
                                break
                        finally:
                            if output not in files:
                                output.unlink(missing_ok=True)
                        if not candidates and candidate == url:
                            try:
                                response = await client.get(url)
                                response.raise_for_status()
                                variants = _variants(url, response.text[:100_000])
                                candidates = variants[1:] if len(variants) > 1 else variants
                            except httpx.HTTPError:
                                pass
    if len(files) != len(post.media):
        for file in files:
            file.unlink(missing_ok=True)
        return []
    return files
