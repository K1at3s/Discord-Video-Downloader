"""YouTube format selection and three free download routes."""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .cobalt import download_cobalt
from .direct_providers import _stream, allowed_media_url
from .download import DownloadError, _run
from .links import PostLink
from .race import first_success

LOG = logging.getLogger(__name__)
CHOICES = {"mp3", "360", "720", "1080"}


async def fetch_youtube_title(url: str) -> str | None:
    """The public oEmbed endpoint supplies a title without a Google API key."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=4), trust_env=False) as client:
            response = await client.get("https://www.youtube.com/oembed", params={"url": url, "format": "json"})
            response.raise_for_status()
            title = response.json().get("title")
            return title[:180] if isinstance(title, str) and title else None
    except (httpx.HTTPError, ValueError, TypeError):
        return None


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
            match = re.search(r"(\d{3,4})p", str(item.get("label", "")), re.I)
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
    except (asyncio.TimeoutError, asyncio.CancelledError):
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
            async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5), follow_redirects=False, trust_env=False) as client:
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
               "--remote-components", "ejs:github", "--socket-timeout", "15",
               "--retries", "2", "--fragment-retries", "2", "--max-filesize", str(max_bytes),
               "-o", str(folder / "youtube.%(ext)s")]
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
        height = int(choice)
        # Reserve space for the audio stream before selecting a video format.
        # yt-dlp's --max-filesize rejects an oversize merged result but does
        # not automatically retry a lower resolution.
        video_budget = int(max_bytes * 0.75)
        audio_budget = max_bytes - video_budget
        formats = (f"bv[vcodec^=avc1][height<={height}][filesize<{video_budget}]"
                   f"+ba[ext=m4a][filesize<{audio_budget}]"
                   f"/b[ext=mp4][height<={height}][filesize<{max_bytes}]")
        command = [*base, "-f", formats, "--merge-output-format", "mp4", link.url]
        return await _run(command, timeout=180, folder=folder, max_total_bytes=max_bytes * 3)


def _selected_file(folder: Path, choice: str, max_bytes: int) -> Path | None:
    suffix = ".mp3" if choice == "mp3" else ".mp4"
    for path in folder.glob("*"):
        if path.is_file() and path.suffix.lower() == suffix and 0 < path.stat().st_size <= max_bytes:
            return path
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
        target = folder / name
        target.mkdir(parents=True, exist_ok=True)
        try:
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
                LOG.info("YouTube %s from %s", choice, name)
                return selected, name
            failures.append(f"{name}: {error or 'uygun dosya yok'}")
        except (asyncio.TimeoutError, OSError) as exc:
            failures.append(f"{name}: {type(exc).__name__}")
        return None

    result = await first_success(providers, attempt)
    if result is not None:
        return result
    raise DownloadError("Seçilen format alınamadı. " + "; ".join(failures)[:400])
