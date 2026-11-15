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
from .highlight_state import HIGHLIGHTS, format_wait, highlight_id
from .indownio import download_indownio, is_rate_limited_message
from .links import PostLink
from .limits import MAX_MEDIA_ITEMS
from .media_cache import MEDIA_CACHE
from .race import first_success
from .provider_metrics import METRICS
from .threads_links import highlight_post_link
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
        providers = ("embed", "indown", "indownio", "vette", "gendownload", "gallery-dl", "yt-dlp", "instaloader")
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


async def download_media(link: PostLink, folder: Path, max_bytes: int, cookie_file: Path | None = None, cobalt_url: str | None = None, cobalt_key: str | None = None, gallery_rs_path: Path | None = None, user_id: int | None = None) -> tuple[list[Path], str]:
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
        if highlight := highlight_post_link(link.url):
            # Threads-only short links like /s/highlight:<ig_id> point at an
            # Instagram highlight album, so hand off to the Instagram flow.
            link = highlight
        elif re.search(r"/(?:share|t|s)/", link.url):
            # A short URL has no post page to parse until it is resolved.
            providers = tuple(name for name in providers if name != "threads-page")
    failures: list[str] = []
    oversize = False
    private = False
    rate_limited = False
    async def attempt(name: str) -> tuple[list[Path], str] | None:
        nonlocal oversize, private, rate_limited
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
            elif name == "indownio":
                try:
                    count, error = await asyncio.wait_for(download_indownio(link, target, max_bytes), timeout=150)
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
                rate_limited = rate_limited or "hourly request limit" in error.lower()
            outcome = ("quota" if is_rate_limited_message(error or "") else
                       "fault" if fault else "private" if error and looks_private(error) else
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

    # General short-lived media cache (post-normalization). Exclude Instagram
    # highlights handled by HIGHLIGHTS quota/cache flow to avoid bypassing limits.
    hid = highlight_id(link.url) if link.platform == "instagram" else None
    cache_eligible = MEDIA_CACHE.enabled and not (hid and HIGHLIGHTS.enabled and HIGHLIGHTS.cache)
    cache_key = MEDIA_CACHE.key_for(link.platform, link.url, bool(cookie_file)) if cache_eligible else ""
    cache_leader = False
    cache_fut = None
    if cache_eligible:
        cached = await asyncio.to_thread(MEDIA_CACHE.get, link.platform, link.url, bool(cookie_file))
        if cached:
            copied = await asyncio.to_thread(MEDIA_CACHE.copy_to, cached, folder)
            files = media_files(folder, max_bytes)
            if files:
                await asyncio.to_thread(METRICS.record_winner, link.platform, f"{cached.provider}-cache")
                LOG.info("Served %s from media cache via %s (%d files)", link.platform, cached.provider, len(files))
                return files, cached.provider
        cache_leader, cache_fut = await MEDIA_CACHE.singleflight_start(cache_key)
        if not cache_leader:
            try:
                await cache_fut
            except Exception:
                pass
            cached = await asyncio.to_thread(MEDIA_CACHE.get, link.platform, link.url, bool(cookie_file))
            if cached:
                copied = await asyncio.to_thread(MEDIA_CACHE.copy_to, cached, folder)
                files = media_files(folder, max_bytes)
                if files:
                    await asyncio.to_thread(METRICS.record_winner, link.platform, f"{cached.provider}-cache")
                    return files, cached.provider
            # leader failed; continue with normal download path as fallback

    async def _finish(result: tuple[list[Path], str] | None = None, exc: Exception | None = None):
        nonlocal cache_leader
        if cache_leader:
            await MEDIA_CACHE.singleflight_finish(cache_key, result[1] if result else None, exc)
            cache_leader = False
        if exc is not None:
            raise exc
        return result

    def _cache_store(files: list[Path], provider: str) -> None:
        if not cache_eligible:
            return
        src_dir = folder / provider
        if src_dir.is_dir():
            src_files = [p for p in src_dir.rglob("*") if p.is_file()]
        else:
            src_files = files
        MEDIA_CACHE.put(link.platform, link.url, bool(cookie_file), provider, src_files)

    async def _success(files: list[Path], provider: str) -> tuple[list[Path], str]:
        await asyncio.to_thread(METRICS.record_winner, link.platform, provider)
        await asyncio.to_thread(_cache_store, files, provider)
        await _finish((files, provider))
        return files, provider

    try:
        if canonical and "/share/p/" in original_link.url:
            # The fast Open Graph path contains only the cover photo. Give the
            # gallery extractor first chance at the complete album.
            result = await attempt("gallery-dl")
            if result is not None:
                return await _success(result[0], result[1])
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
                return await _success(result[0], result[1])
            providers = tuple(name for name in providers if name != "threadsdl")
        if link.platform == "instagram" and re.search(r"/(?:stories|highlights)/", link.url):
            # Story and highlight links are not post pages, so the post viewers
            # would only expose the video poster. indown.net reads stories item by
            # item without a quota; it is tried first for both stories and
            # highlights. Highlights often return empty from indown.net, in which
            # case indown.io (keyless but 20/hour rate limited) is the fallback.
            hid = highlight_id(link.url)
            if hid and HIGHLIGHTS.enabled and HIGHLIGHTS.cache:
                # Cache hit: copy to caller folder (no quota/user-limit consumption).
                cached = await asyncio.to_thread(HIGHLIGHTS.cache.get, hid)
                if cached:
                    copied = await asyncio.to_thread(HIGHLIGHTS.cache.copy_to, cached, folder)
                    files = media_files(folder, max_bytes)
                    if files:
                        await asyncio.to_thread(METRICS.record_winner, link.platform, "indownio-cache")
                        LOG.info("Highlight %s served from cache (%d files)", hid, len(files))
                        return files, "indownio"
                # Single-flight: only one download per highlight ID.
                leader, fut = await HIGHLIGHTS.cache.singleflight_start(hid)
                if not leader:
                    try:
                        await fut
                    except Exception:
                        pass
                    cached = await asyncio.to_thread(HIGHLIGHTS.cache.get, hid)
                    if cached:
                        await asyncio.to_thread(HIGHLIGHTS.cache.copy_to, cached, folder)
                        files = media_files(folder, max_bytes)
                        if files:
                            await asyncio.to_thread(METRICS.record_winner, link.platform, "indownio-cache")
                            return files, "indownio"
                    # Leader failed or produced nothing usable; continue with normal attempt below.
                else:
                    claimed = False
                    try:
                        # Per-user hourly+daily limits for highlight requests.
                        if user_id is not None and HIGHLIGHTS.user_quota:
                            ok, msg = await HIGHLIGHTS.user_quota.aclaim(user_id)
                            if not ok:
                                raise DownloadError(msg or "Öne çıkan indirme limitine ulaştın.")
                            claimed = True
                        # Try quota-free indown.net first.
                        try:
                            result = await asyncio.wait_for(attempt("indown"), timeout=90)
                        except asyncio.TimeoutError:
                            result = None
                        if result is None:
                            # Fallback to indown.io with token bucket.
                            # Bucket is a local sliding-window estimate; service may reset earlier,
                            # so allow a short single-flight probe near window end.
                            allowed, wait = HIGHLIGHTS.bucket.try_consume()
                            probed = False
                            if not allowed and wait <= 300:
                                async with HIGHLIGHTS.probe_lock:
                                    allowed, wait = HIGHLIGHTS.bucket.try_consume()
                                    if not allowed:
                                        wait = HIGHLIGHTS.bucket.wait_seconds()
                                        if wait <= 300:
                                            allowed, probed = True, True
                            if not allowed:
                                await asyncio.to_thread(METRICS.record, link.platform, "indownio", "quota", 0)
                                raise DownloadError(
                                    f"Öne çıkan indirme kotası doldu (indown.io, 20/saat). Yaklaşık {format_wait(wait)} sonra tekrar dene."
                                )
                            result = await attempt("indownio")
                            if result is None and rate_limited:
                                wait = HIGHLIGHTS.bucket.mark_exhausted()
                                await asyncio.to_thread(METRICS.record, link.platform, "indownio", "quota", 0)
                                raise DownloadError(
                                    f"Öne çıkan indirme kotası doldu (indown.io, 20/saat). Yaklaşık {format_wait(wait)} sonra tekrar dene."
                                )
                            if probed and result is not None:
                                # Probe succeeded although local bucket thought full; record actual use.
                                HIGHLIGHTS.bucket.try_consume()
                        if result is not None:
                            files, provider = result
                            src = folder / provider
                            if src.is_dir():
                                await asyncio.to_thread(HIGHLIGHTS.cache.put, hid, src)
                            await HIGHLIGHTS.cache.singleflight_finish(hid, provider)
                            await asyncio.to_thread(METRICS.record_winner, link.platform, provider)
                            return result
                        await HIGHLIGHTS.cache.singleflight_finish(hid, None)
                    except Exception as exc:
                        await HIGHLIGHTS.cache.singleflight_finish(hid, None, exc)
                        if claimed and user_id is not None and HIGHLIGHTS.user_quota:
                            await HIGHLIGHTS.user_quota.arelease(user_id)
                        raise
                    # Leader failed without raising (no result); release claim and continue fallback.
                    if claimed and user_id is not None and HIGHLIGHTS.user_quota:
                        await HIGHLIGHTS.user_quota.arelease(user_id)
                    providers = tuple(name for name in providers if name not in {"indown", "indownio"})
                    result = await first_success(providers, attempt, stagger=0)
                    if result is not None:
                        return await _success(result[0], result[1])
                    if oversize:
                        raise DownloadError("Medya Discord yükleme sınırını aşıyor.")
                    if private:
                        raise DownloadError("Bu hesap gizli olduğu için medya indirilemedi. Gizli hesaplar yalnızca oturum açılmış bir çerez dosyasıyla (COOKIE_FILE) indirilebilir.")
                    if rate_limited and link.platform == "instagram":
                        wait = HIGHLIGHTS.bucket.wait_seconds()
                        raise DownloadError(
                            f"Öne çıkan indirme kotası doldu (indown.io, 20/saat). Yaklaşık {format_wait(wait)} sonra tekrar dene."
                        )
                    raise DownloadError("Bu gönderiden medya alınamadı (" + ", ".join(failures) + "). Gönderi özel olabilir veya sağlayıcılar geçici olarak çalışmıyor olabilir.")
            else:
                # Stories or highlights without cache/quota state: original behavior.
                first = "indown"
                try:
                    result = await asyncio.wait_for(attempt(first), timeout=90)
                except asyncio.TimeoutError:
                    result = None
                if result is not None:
                    return await _success(result[0], result[1])
                providers = tuple(name for name in providers if name != first)
        result = await first_success(providers, attempt, stagger=0)
        if result is not None:
            return await _success(result[0], result[1])
        if oversize:
            raise DownloadError("Medya Discord yükleme sınırını aşıyor.")
        if private:
            raise DownloadError("Bu hesap gizli veya oturum gerektiriyor; medya indirilemedi. Erişim hakkınız varsa COOKIE_FILE ayarlayın.")
        if rate_limited and link.platform == "instagram":
            wait = HIGHLIGHTS.bucket.wait_seconds() if HIGHLIGHTS.enabled else 0
            if wait > 0:
                raise DownloadError(f"Instagram indirme kotası/sınırı aşıldı (indown.io). Yaklaşık {format_wait(wait)} sonra tekrar dene.")
            raise DownloadError("Instagram görüntüleyicisi saatlik istek sınırına takıldı. Bir süre sonra tekrar dene.")
        if link.platform == "threads" and re.search(r"/(?:share|t|s)/", link.url):
            # threads.com answers these short links with a /login/ redirect unless a
            # valid session cookie is configured, so no provider can see the post.
            raise DownloadError("Threads paylaşım bağlantısı oturum olmadan açılamıyor. COOKIE_FILE ayarlayın veya gönderiyi tarayıcıda açıp /@kullanıcı/post/... bağlantısını paylaşın.")
        detail = ", ".join(failures[:6])
        if failures:
            raise DownloadError(f"Bu gönderiden medya alınamadı. Gönderi kaldırılmış, özel/giriş gerektiriyor ya da sağlayıcılar geçici olarak yanıt vermiyor olabilir. (denenen: {detail})")
        raise DownloadError("Bu gönderiden medya alınamadı. Gönderi kaldırılmış, özel/giriş gerektiriyor ya da sağlayıcılar geçici olarak yanıt vermiyor olabilir.")
    except Exception as exc:
        await _finish(None, exc)
        raise
    finally:
        # Ensure singleflight released if we somehow exit without finishing.
        if cache_leader:
            await MEDIA_CACHE.singleflight_finish(cache_key, None, None)
