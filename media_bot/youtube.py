"""YouTube format selection and three free download routes."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from . import provider_memory
from .circuit_breaker import BREAKERS, provider_fault
from .cobalt import download_cobalt
from .direct_providers import _stream, allowed_media_url
from .download import DownloadError, _run
from .http import shared_client
from .links import PostLink
from .provider_metrics import METRICS
from .race import first_success

LOG = logging.getLogger(__name__)
CHOICES = {"mp3", "360", "720", "1080"}


async def fetch_youtube_meta(url: str) -> tuple[str | None, str | None]:
    """Return ``(title, channel)`` from the public oEmbed endpoint (no API key)."""
    try:
        async with shared_client(timeout=httpx.Timeout(8, connect=4), trust_env=False) as client:
            response = await client.get("https://www.youtube.com/oembed", params={"url": url, "format": "json"})
            response.raise_for_status()
            data = response.json()
            title = data.get("title")
            author = data.get("author_name")
            return (
                title[:180] if isinstance(title, str) and title else None,
                author[:90] if isinstance(author, str) and author else None,
            )
    except (httpx.HTTPError, ValueError, TypeError):
        return None, None


async def fetch_youtube_title(url: str) -> str | None:
    """The public oEmbed endpoint supplies a title without a Google API key."""
    title, _ = await fetch_youtube_meta(url)
    return title


_UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_NOISE = re.compile(
    r"\s*[\(\[][^)\]]*\b(?:official|officiel|video|audio|lyrics?|lyric|music|m/?v|mv|hd|hq|4k|8k|"
    r"remaster(?:ed)?|visuali[sz]er|clip|premiere)\b[^)\]]*[\)\]]",
    re.IGNORECASE,
)


def clean_song_name(text: str) -> str:
    text = _NOISE.sub(" ", text or "")
    text = _UNSAFE_FILENAME.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip(" .-_")


def song_filename(author: str | None, title: str | None, *, fallback: str = "youtube",
                  limit: int = 90) -> str:
    """Build ``Artist - Title.mp3`` while avoiding duplicated artist names."""
    clean_title = clean_song_name(title or "")
    clean_author = clean_song_name(author or "")
    if not clean_title:
        base = clean_song_name(fallback) or "youtube"
    elif clean_author and clean_author.casefold() not in clean_title.casefold():
        base = f"{clean_author} - {clean_title}"
    else:
        base = clean_title
    if len(base) > limit:
        base = base[:limit].rstrip(" .-_")
    return f"{base or 'youtube'}.mp3"

def gendownload_options(data: dict, choice: str, max_bytes: int) -> list[tuple[str, str]]:
    options: list[tuple[int, str, str]] = []
    for item in (data.get("formats") or [])[:40]:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not allowed_media_url(url, "gendownload") or urlsplit(url).path != "/api/stream":
            continue
        size = item.get("filesize")
        if isinstance(size, (int, float)) and size > max_bytes:
            continue
        extension = item.get("ext")
        if choice == "mp3":
            if item.get("type") == "audio" and extension in {"mp3", "m4a", "webm"}:
                options.append((1 if extension == "mp3" else 0, url, extension))
        elif item.get("type") == "video" and extension == "mp4":
            match = re.search(r"(\d{3,4})p", str(item.get("label", "")), re.IGNORECASE)
            height = int(match.group(1)) if match else 0
            if height and height <= int(choice):
                options.append((height, url, "mp4"))
    options.sort(key=lambda entry: entry[0], reverse=True)
    return [(url, ext) for _, url, ext in options[:4]]


async def _to_mp3(source: Path, target: Path, max_bytes: int) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(source),
        "-vn", "-b:a", "128k", str(target),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        await asyncio.wait_for(proc.communicate(), timeout=90)
    except (TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.communicate()
        if target.exists():
            target.unlink()
        raise
    valid = proc.returncode == 0 and target.is_file() and 0 < target.stat().st_size <= max_bytes
    if not valid:
        target.unlink(missing_ok=True)
    return valid


async def _gendownload(link: PostLink, folder: Path, choice: str, max_bytes: int) -> tuple[int, str]:
    for attempt in range(2):
        try:
            async with shared_client(timeout=httpx.Timeout(30, connect=5), follow_redirects=False, trust_env=False) as client:
                response = await client.post("https://gendownload.com/api/extract", json={"url": link.url})
                response.raise_for_status()
                options = gendownload_options(response.json(), choice, max_bytes)
                for index, (url, ext) in enumerate(options, 1):
                    downloaded = folder / f"source_{index}.{ext}"
                    if not await _stream(client, url, "gendownload", downloaded, max_bytes):
                        continue
                    if choice == "mp3" and ext != "mp3":
                        if await _to_mp3(downloaded, folder / "youtube.mp3", max_bytes):
                            return 1, ""
                    else:
                        downloaded.rename(folder / ("youtube.mp3" if choice == "mp3" else "youtube.mp4"))
                        return 1, ""
                return 0, "uygun kalite bulunamadı"
        except (httpx.ConnectError, httpx.ReadError) as exc:
            if attempt == 0:
                await asyncio.sleep(1)
                continue
            return 0, str(exc)[:180]
        except (httpx.HTTPError, ValueError, TypeError, OSError) as exc:
            return 0, str(exc)[:180]
    return 0, "medya bulunamadı"


async def _yt_dlp(link: PostLink, folder: Path, choice: str, max_bytes: int, cookie_file: Path | None) -> tuple[int, str]:
    base = [sys.executable, "-m", "yt_dlp", "--no-config", "--no-playlist",
               "--socket-timeout", "15",
               "--retries", "3", "--fragment-retries", "3",
               "--concurrent-fragments", "4", "--no-part",
               "--max-filesize", str(max_bytes),
               "-o", str(folder / "youtube.%(ext)s")]
    if os.getenv("YTDLP_REMOTE_EJS", "0") == "1":
        base.extend(("--remote-components", "ejs:github"))
    if cookie_file:
        base.extend(("--cookies", str(cookie_file)))
    if choice == "mp3":
        audio_args = ["-f", "ba/b", "-x", "--audio-format", "mp3", "--audio-quality", "128K"]
        last = (1, "ses alınamadı")
        for client in ("tv,web_embedded", "web_embedded,web,tv", "web_safari,web_embedded,tv"):
            command = [*base, "--extractor-args", f"youtube:player-client={client}",
                       *audio_args, link.url]
            last = await _run(command, timeout=90, folder=folder, max_total_bytes=max_bytes * 3)
            if last[0] == 0 or "HTTP Error 403" not in last[1]:
                return last
            for partial in folder.glob("youtube.*"):
                if partial.is_file():
                    partial.unlink(missing_ok=True)
        return last
    else:
        # Prefer the requested height, then step down only when the result is
        # missing/oversize. A failed 403/network attempt is not retried lower.
        heights = [int(choice)]
        for lower in (720, 480, 360):
            if lower < heights[0]:
                heights.append(lower)
        last = (1, "uygun kalite bulunamadı")
        video_budget = int(max_bytes * 0.75)
        audio_budget = max_bytes - video_budget
        size_hints = ("max-filesize", "larger than", "requested format", "no video formats")
        for height in heights:
            formats = (f"bv[vcodec^=avc1][height<={height}][filesize<{video_budget}]"
                       f"+ba[ext=m4a][filesize<{audio_budget}]"
                       f"/b[ext=mp4][height<={height}][filesize<{max_bytes}]")
            command = [*base, "-f", formats, "--merge-output-format", "mp4", link.url]
            code, error = await _run(command, timeout=180, folder=folder,
                                     max_total_bytes=max_bytes * 3)
            last = (code, error)
            if code == 0 and _selected_file(folder, choice, max_bytes):
                return last
            for partial in folder.glob("youtube.*"):
                if partial.is_file():
                    partial.unlink(missing_ok=True)
            if code != 0 and not any(hint in (error or "").lower() for hint in size_hints):
                return last
        return last


def _selected_file(folder: Path, choice: str, max_bytes: int) -> Path | None:
    suffix = ".mp3" if choice == "mp3" else ".mp4"
    for path in folder.glob("*"):
        if path.is_file() and path.suffix.lower() == suffix and 0 < path.stat().st_size <= max_bytes:
            return path
    return None


async def video_height(path: Path) -> int | None:
    """Read the delivered video's actual height without blocking Discord's event loop."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=height", "-of", "default=nokey=1:noprint_wrappers=1",
            str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            output, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        except (TimeoutError, asyncio.CancelledError):
            proc.kill()
            await proc.communicate()
            raise
        height = int(output.strip()) if proc.returncode == 0 else 0
        return height if height > 0 else None
    except (TimeoutError, OSError, ValueError):
        return None


async def download_youtube(link: PostLink, folder: Path, choice: str, max_bytes: int,
                           cookie_file: Path | None, cobalt_url: str | None,
                           cobalt_key: str | None) -> tuple[Path, str]:
    if choice not in CHOICES:
        raise ValueError("Geçersiz YouTube seçeneği")
    providers = ["cobalt", "gendownload"] if cobalt_url else ["gendownload"]
    providers.append("yt-dlp")
    failures = []

    async def attempt(name: str) -> tuple[Path, str] | None:
        started = time.monotonic()
        if not BREAKERS.allow(name, "youtube"):
            await asyncio.to_thread(METRICS.record, "youtube", name, "skipped", 0)
            failures.append(f"{name}: geçici olarak atlandı")
            return None
        target = folder / name
        reported = False
        try:
            target.mkdir(parents=True, exist_ok=True)
            if name == "gendownload":
                count, error = await asyncio.wait_for(_gendownload(link, target, choice, max_bytes), timeout=100)
            elif name == "cobalt":
                count, error = await asyncio.wait_for(
                    download_cobalt(link.url, target, max_bytes, cobalt_url, cobalt_key,
                                    audio=choice == "mp3", video_quality=720 if choice == "mp3" else int(choice)),
                    timeout=150,
                )
            else:
                count, error = await _yt_dlp(link, target, choice, max_bytes, cookie_file)
            selected = _selected_file(target, choice, max_bytes)
            if selected:
                BREAKERS.success(name, "youtube")
                reported = True
                await asyncio.to_thread(METRICS.record, "youtube", name, "success", int((time.monotonic() - started) * 1000))
                LOG.info("YouTube %s from %s", choice, name)
                return selected, name
            fault = provider_fault(error)
            opened = BREAKERS.failure(name, "youtube") if fault else None
            if opened is None:
                BREAKERS.neutral(name, "youtube")
            elif opened:
                LOG.warning("Provider cooldown opened: %s/youtube", name)
            reported = True
            outcome = ("fault" if fault else "private" if error and
                       any(hint in error.casefold() for hint in ("private", "login", "403", "404")) else
                       "no_media" if not error else "error")
            await asyncio.to_thread(METRICS.record, "youtube", name, outcome, int((time.monotonic() - started) * 1000))
            failures.append(f"{name}: {error or 'uygun dosya yok'}")
        except TimeoutError as exc:
            if BREAKERS.failure(name, "youtube"):
                LOG.warning("Provider cooldown opened: %s/youtube", name)
            reported = True
            await asyncio.to_thread(METRICS.record, "youtube", name, "fault", int((time.monotonic() - started) * 1000))
            failures.append(f"{name}: {type(exc).__name__}")
        except OSError as exc:
            BREAKERS.neutral(name, "youtube")
            reported = True
            await asyncio.to_thread(METRICS.record, "youtube", name, "unavailable", int((time.monotonic() - started) * 1000))
            failures.append(f"{name}: {type(exc).__name__}")
        finally:
            if not reported:
                BREAKERS.abandon(name, "youtube")
                outcome = "cancelled" if asyncio.current_task().cancelling() else "error"
                await asyncio.to_thread(METRICS.record, "youtube", name, outcome, int((time.monotonic() - started) * 1000))
        return None

    key = f"youtube:{choice}"
    preferred = await provider_memory.preferred(key, "youtube", choice)
    # Preferred provider runs alone; the rest only start if it fails or is slow.
    ordered = await provider_memory.ranked("youtube", providers)
    grace = await asyncio.to_thread(
        provider_memory.hedge_seconds, 5.0 if choice == "mp3" else 8.0,
        platform="youtube", provider=preferred,
    )
    result = await first_success(ordered, attempt, priority=preferred, grace=grace)
    if result is not None:
        provider_memory.remember(key, result[1])
        await asyncio.to_thread(METRICS.record_winner, "youtube", result[1], choice)
        return result
    provider_memory.forget(key)
    raise DownloadError("Seçilen format alınamadı. " + "; ".join(failures)[:400])
