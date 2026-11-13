"""Independent media download routes with fast, staggered fallbacks."""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import sys
import time
from pathlib import Path

from .circuit_breaker import BREAKERS, provider_fault
from .cobalt import download_cobalt
from .cookiefile import netscape_jar
from .direct_providers import canonical_facebook_post, download_direct, download_vxtwitter
from .gendownload import download_gendownload
from .links import PostLink
from .limits import MAX_MEDIA_ITEMS
from .race import first_success
from .provider_metrics import METRICS
from .web_providers import download_web

LOG = logging.getLogger(__name__)
MEDIA_SUFFIXES = {".mp4", ".mov", ".webm", ".m4v", ".jpg", ".jpeg", ".png", ".webp", ".gif"}
PROCESS_TIMEOUT = 150
NO_MEDIA_RESULTS = {"no usable media", "no uploadable media"}
PRIVATE_HINTS = ("login", "private", "authrequired")


def looks_private(error: str) -> bool:
    """Providers say "login required" / "private" when the account is not public."""
    lowered = error.lower()
    return any(hint in lowered for hint in PRIVATE_HINTS)


class DownloadError(Exception):
    pass


def media_files(folder: Path, max_bytes: int) -> list[Path]:
    def natural_key(path: Path) -> list[tuple[int, str | int]]:
        return [(1, int(part)) if part.isdigit() else (0, part.lower())
                for part in re.split(r"(\d+)", str(path.relative_to(folder)))]

    files = sorted((p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES), key=natural_key)
    return [p for p in files if 0 < p.stat().st_size <= max_bytes][:MAX_MEDIA_ITEMS]


async def _run(command: list[str], timeout: int = PROCESS_TIMEOUT, folder: Path | None = None, max_total_bytes: int | None = None) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    communication = asyncio.create_task(proc.communicate())
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        while not communication.done():
            try:
                await asyncio.wait_for(asyncio.shield(communication), timeout=1)
            except asyncio.TimeoutError:
                if asyncio.get_running_loop().time() >= deadline:
                    proc.kill()
                    await communication
                    return -1, "zaman aşımı"
                if folder and max_total_bytes:
                    try:
                        total = sum(path.stat().st_size for path in folder.rglob("*") if path.is_file())
                    except OSError:
                        total = 0
                    if total > max_total_bytes:
                        proc.kill()
                        await communication
                        return -1, "geçici dosya sınırı aşıldı"
    except asyncio.CancelledError:
        if proc.returncode is None:
            proc.kill()
        await communication
        raise
    _, stderr = communication.result()
    return proc.returncode or 0, stderr.decode("utf-8", "replace")[-1200:]


def _cookies(cookie_file: Path | None) -> list[str]:
    jar = netscape_jar(cookie_file)
    return ["--cookies", str(jar)] if jar else []


async def _yt_dlp(link: PostLink, folder: Path, max_bytes: int, cookie_file: Path | None) -> tuple[int, str]:
    command = [
        sys.executable, "-m", "yt_dlp", "--no-config",
        "--yes-playlist" if link.platform == "threads" else "--no-playlist",
        "--playlist-end", str(MAX_MEDIA_ITEMS), "--max-filesize", str(max_bytes),
        "--socket-timeout", "15", "--retries", "2", "--fragment-retries", "2",
        "--restrict-filenames", "--no-write-info-json", "--no-write-thumbnail",
        "--merge-output-format", "mp4", "-f", "bv*+ba/best",
        "-o", str(folder / "%(id)s_%(playlist_index)s.%(ext)s"),
        *_cookies(cookie_file), link.url,
    ]
    return await _run(command, folder=folder, max_total_bytes=max_bytes * MAX_MEDIA_ITEMS)


async def _gallery_dl(link: PostLink, folder: Path, max_bytes: int, cookie_file: Path | None) -> tuple[int, str]:
    command = [
        sys.executable, "-m", "gallery_dl", "--ignore-config", "--no-input", "--directory", str(folder),
        "--range", f"1-{MAX_MEDIA_ITEMS}", "--filesize-max", str(max_bytes),
        "--no-mtime", *_cookies(cookie_file), link.url,
    ]
    return await _run(command, folder=folder, max_total_bytes=max_bytes * MAX_MEDIA_ITEMS)


async def _instaloader(link: PostLink, folder: Path, max_bytes: int,
                       cookie_file: Path | None = None) -> tuple[int, str]:
    shortcode = link.url.rstrip("/").split("/")[-1]
    command = [
        sys.executable, "-m", "instaloader", "--dirname-pattern", str(folder),
        "--no-captions", "--no-metadata-json", "--no-video-thumbnails",
        "--max-connection-attempts", "1", "--request-timeout", "15",
    ]
    if jar := netscape_jar(cookie_file):
        command += ["--cookiefile", str(jar)]
    command += ["--", "-" + shortcode]
    return await _run(command, folder=folder, max_total_bytes=max_bytes * MAX_MEDIA_ITEMS)


async def _you_get(link: PostLink, folder: Path, max_bytes: int) -> tuple[int, str]:
    command = [sys.executable, "-m", "you_get", "-o", str(folder), link.url]
    return await _run(command, timeout=120, folder=folder, max_total_bytes=max_bytes * MAX_MEDIA_ITEMS)


async def _gallery_rs(link: PostLink, folder: Path, max_bytes: int, cookie_file: Path | None, executable: Path) -> tuple[int, str]:
    command = [str(executable), "--extractor", "native", "--native-config-ignore", "-d", str(folder)]
    if jar := netscape_jar(cookie_file):
        command += ["-C", str(jar)]
    command.append(link.url)
    return await _run(command, folder=folder, max_total_bytes=max_bytes * MAX_MEDIA_ITEMS)


def provider_order(platform: str, cobalt_enabled: bool = False, gallery_rs_enabled: bool = False) -> tuple[str, ...]:
    if platform not in {"instagram", "facebook", "reddit", "x", "tiktok", "threads", "douyin", "weibo", "streamable"}:
        raise ValueError("Desteklenmeyen platform")
    if platform == "instagram":
        providers = ("embed", "indown", "vette", "gendownload", "gallery-dl", "yt-dlp", "instaloader")
    elif platform == "tiktok":
        providers = ("direct", "gendownload", "yt-dlp", "gallery-dl")
    elif platform == "facebook":
        providers = ("direct", "gendownload", "gallery-dl", "yt-dlp", "vette")
    elif platform == "x":
        providers = ("direct", "gendownload", "gallery-dl", "yt-dlp")
    elif platform == "threads":
        providers = ("threadsdl", "threads-page", "yt-dlp", "vette")
    elif platform == "douyin":
        providers = ("yt-dlp", "direct")
    elif platform == "weibo":
        providers = ("gallery-dl", "yt-dlp")
    elif platform == "streamable":
        providers = ("direct", "yt-dlp")
    else:
        # Reddit's anonymous .json endpoint frequently returns 403; try the
        # video services first and leave JSON as a last-resort legacy route.
        providers = ("rapidsave", "vette", "gallery-dl", "yt-dlp", "direct")
    if cobalt_enabled and platform in {"instagram", "facebook", "reddit", "x", "tiktok", "streamable"}:
        providers = providers[:1] + ("cobalt",) + providers[1:]
    if platform == "x":
        providers += ("vxtwitter", "you-get")
    elif platform == "instagram":
        providers += ("you-get",)
    elif platform == "tiktok":
        providers += ("tiktokio", "btch")
    if gallery_rs_enabled and platform in {"instagram", "facebook", "reddit", "x", "tiktok", "threads", "weibo"}:
        providers += ("gallery-dl-rs",)
    return providers


async def download_media(link: PostLink, folder: Path, max_bytes: int, cookie_file: Path | None = None, cobalt_url: str | None = None, cobalt_key: str | None = None, gallery_rs_path: Path | None = None) -> tuple[list[Path], str]:
    """Return uploadable files and the successful provider name.

    Each provider gets its own directory so a partial failure cannot be
    mistaken for a successful extraction. The caller owns the temp folder.
    """
    original_link = link
    canonical = await canonical_facebook_post(link) if link.platform == "facebook" else None
    if canonical:
        link = canonical
    providers = provider_order(link.platform, bool(cobalt_url), bool(gallery_rs_path))
    if link.platform == "threads":
        if re.search(r"/(?:share|t)/", link.url):
            # A short URL has no post page to parse until it is resolved.
            providers = tuple(name for name in providers if name != "threads-page")
    failures: list[str] = []
    oversize = False
    private = False
    async def attempt(name: str) -> tuple[list[Path], str] | None:
        nonlocal oversize, private
        started = time.monotonic()
        if not BREAKERS.allow(name, link.platform):
            LOG.info("Skipping %s for %s during provider cooldown", name, link.platform)
            await asyncio.to_thread(METRICS.record, link.platform, name, "skipped", 0)
            failures.append(f"{name} (cooldown)")
            return None
        target = folder / name
        reported = False
        try:
            target.mkdir(parents=True, exist_ok=True)
            if name == "gallery-dl":
                code, error = await _gallery_dl(link, target, max_bytes, cookie_file)
            elif name == "yt-dlp":
                code, error = await _yt_dlp(link, target, max_bytes, cookie_file)
            elif name == "instaloader":
                code, error = await _instaloader(link, target, max_bytes, cookie_file)
            elif name == "direct":
                try:
                    count, error = await asyncio.wait_for(download_direct(link, target, max_bytes), timeout=150)
                    code = 0 if count else 1
                except asyncio.TimeoutError:
                    code, error = -1, "zaman aşımı"
            elif name == "gallery-dl-rs":
                code, error = await _gallery_rs(link, target, max_bytes, cookie_file, gallery_rs_path)
            elif name == "you-get":
                code, error = await _you_get(link, target, max_bytes)
            elif name == "gendownload":
                try:
                    count, error = await asyncio.wait_for(download_gendownload(link, target, max_bytes), timeout=90)
                    code = 0 if count else 1
                except asyncio.TimeoutError:
                    code, error = -1, "zaman aşımı"
            elif name == "vxtwitter":
                try:
                    count, error = await asyncio.wait_for(download_vxtwitter(link.post_id or "", target, max_bytes), timeout=120)
                    code = 0 if count else 1
                except asyncio.TimeoutError:
                    code, error = -1, "zaman aşımı"
            elif name in {"indown", "tiktokio", "rapidsave", "btch", "vette", "embed", "threads-page", "threadsdl"}:
                try:
                    count, error = await asyncio.wait_for(download_web(name, link, target, max_bytes, cookie_file), timeout=90)
                    code = 0 if count else 1
                except asyncio.TimeoutError:
                    code, error = -1, "zaman aşımı"
            else:
                try:
                    count, error = await asyncio.wait_for(download_cobalt(link.url, target, max_bytes, cobalt_url, cobalt_key), timeout=150)
                    code = 0 if count else 1
                except asyncio.TimeoutError:
                    code, error = -1, "zaman aşımı"
            files = media_files(target, max_bytes)
            if files and code == 0:
                BREAKERS.success(name, link.platform)
                reported = True
                await asyncio.to_thread(METRICS.record, link.platform, name, "success", int((time.monotonic() - started) * 1000))
                LOG.info("Downloaded %s using %s (%d files)", link.platform, name, len(files))
                return files, name
            fault = provider_fault(error)
            opened = BREAKERS.failure(name, link.platform) if fault else None
            if opened is None:
                BREAKERS.neutral(name, link.platform)
            elif opened:
                LOG.warning("Provider cooldown opened: %s/%s", name, link.platform)
            reported = True
            if any(p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES and p.stat().st_size > max_bytes for p in target.rglob("*")):
                oversize = True
            if not error or error.strip().lower() in NO_MEDIA_RESULTS:
                LOG.info("%s found no media for %s", name, link.platform)
            else:
                LOG.warning("%s failed for %s (exit %s): %s", name, link.platform, code, error)
                private = private or looks_private(error)
            outcome = ("fault" if fault else "private" if error and looks_private(error) else
                       "no_media" if not error or error.strip().lower() in NO_MEDIA_RESULTS else "error")
            await asyncio.to_thread(METRICS.record, link.platform, name, outcome, int((time.monotonic() - started) * 1000))
            failures.append(name)
        except OSError as exc:
            BREAKERS.neutral(name, link.platform)
            reported = True
            await asyncio.to_thread(METRICS.record, link.platform, name, "unavailable", int((time.monotonic() - started) * 1000))
            LOG.warning("%s unavailable: %s", name, exc)
            failures.append(name)
        except Exception as exc:
            fault = provider_fault(exc)
            opened = BREAKERS.failure(name, link.platform) if fault else None
            if opened is None:
                BREAKERS.neutral(name, link.platform)
            elif opened:
                LOG.warning("Provider cooldown opened: %s/%s", name, link.platform)
            reported = True
            await asyncio.to_thread(METRICS.record, link.platform, name,
                                    "fault" if fault else "error",
                                    int((time.monotonic() - started) * 1000))
            LOG.exception("Unexpected %s failure for %s", name, link.platform)
            failures.append(name)
        finally:
            if not reported:
                BREAKERS.abandon(name, link.platform)
                outcome = "cancelled" if asyncio.current_task().cancelling() else "error"
                await asyncio.to_thread(METRICS.record, link.platform, name, outcome, int((time.monotonic() - started) * 1000))
        return None

    if canonical and "/share/p/" in original_link.url:
        # The fast Open Graph path contains only the cover photo. Give the
        # gallery extractor first chance at the complete album.
        result = await attempt("gallery-dl")
        if result is not None:
            await asyncio.to_thread(METRICS.record_winner, link.platform, result[1])
            return result
        providers = tuple(name for name in providers if name != "gallery-dl")
    if link.platform == "threads" and "threadsdl" in providers:
        # The provider race otherwise lets a faster logged-in page win even
        # when the anonymous downloader works. Bound the first try so a slow
        # hosted service cannot hold up the other routes indefinitely.
        try:
            result = await asyncio.wait_for(attempt("threadsdl"), timeout=12)
        except asyncio.TimeoutError:
            result = None
        if result is not None:
            await asyncio.to_thread(METRICS.record_winner, link.platform, result[1])
            return result
        providers = tuple(name for name in providers if name != "threadsdl")
    result = await first_success(providers, attempt, stagger=0)
    if result is not None:
        await asyncio.to_thread(METRICS.record_winner, link.platform, result[1])
        return result
    if oversize:
        raise DownloadError("Medya Discord yükleme sınırını aşıyor.")
    if private:
        raise DownloadError("Bu hesap gizli olduğu için medya indirilemedi. Gizli hesaplar yalnızca oturum açılmış bir çerez dosyasıyla (COOKIE_FILE) indirilebilir.")
    if link.platform == "threads" and re.search(r"/(?:share|t)/", link.url):
        # threads.com answers these short links with a /login/ redirect unless a
        # valid session cookie is configured, so no provider can see the post.
        raise DownloadError("Threads paylaşım bağlantısı oturum açmadan açılamıyor. COOKIE_FILE ayarlayın veya gönderiyi tarayıcıda açıp adres çubuğundaki /@kullanıcı/post/... bağlantısını yapıştırın.")
    raise DownloadError("Bu gönderiden medya alınamadı (" + ", ".join(failures) + "). Gönderi özel olabilir veya sağlayıcılar geçici olarak çalışmıyor olabilir.")
