"""Platform-specific fallback routes independent of yt-dlp/gallery-dl."""

from __future__ import annotations

import html
import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from .links import PostLink, parse_link
from .limits import MAX_MEDIA_ITEMS
from .x_card import fetch_post

LOG = logging.getLogger(__name__)
LIMIT = MAX_MEDIA_ITEMS
USER_AGENT = "DiscordMediaBot/1.0 (public post media; contact server administrator)"
MEDIA_HOSTS = {
    "gendownload": ("gendownload.com",),
    "indown": ("indown.net", "snapcdn.app", "rapidcdn.app", "cdninstagram.com", "fbcdn.net"),
    "tiktokio": ("tiktokio.com", "tiktokcdn.com", "tiktokcdn-us.com", "tiktokcdn-eu.com", "tiktokv.com", "byteoversea.com"),
    "rapidsave": ("rapidsave.com", "redd.it", "redditmedia.com"),
    "btch": ("btch-downloader-api-green.vercel.app",),
    "vette": ("socialdownloader.space",),
    "x": ("twimg.com",),
    "reddit": ("redd.it", "redditmedia.com"),
    "tiktok": ("tikwm.com", "tiktokcdn.com", "tiktokcdn-us.com", "tiktokcdn-eu.com", "tiktokv.com", "tiktok.com", "byteoversea.com"),
    "facebook": ("fbcdn.net", "fbsbx.com", "facebook.com"),
    "instagram": ("cdninstagram.com", "fbcdn.net", "instagram.com"),
    "streamable": ("streamable.com",),
    "douyin": ("douyin.com", "douyinvod.com", "douyinpic.com", "byteimg.com", "byteoversea.com"),
    "threads": ("fbcdn.net", "cdninstagram.com", "threads.com"),
    "weibo": ("sinaimg.cn", "weibocdn.com", "weibo.com"),
    "spotify": ("scdn.co",),
    "bluesky": ("cdn.bsky.app", "cdn.bsky.social"),
}


def allowed_media_url(url: str, platform: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        return (parsed.scheme == "https" and not parsed.username and not parsed.password
                and parsed.port in {None, 443}
                and any(host == domain or host.endswith("." + domain) for domain in MEDIA_HOSTS.get(platform, ())))
    except ValueError:
        return False


def _suffix(url: str, kind: str) -> str:
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix in {".mp4", ".mov", ".webm", ".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return suffix
    return ".mp4" if kind == "video" else ".jpg"


async def _stream(client: httpx.AsyncClient, url: str, platform: str, output: Path, max_bytes: int) -> bool:
    for _ in range(4):
        if not allowed_media_url(url, platform):
            return False
        async with client.stream("GET", url) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    return False
                url = urljoin(url, location)
                continue
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if content_type.startswith(("text/", "application/json")):
                return False
            content_length = response.headers.get("content-length", "")
            if content_length.isdecimal() and int(content_length) > max_bytes:
                return False
            size = 0
            with output.open("wb") as file:
                async for chunk in response.aiter_bytes(64 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        file.close()
                        output.unlink(missing_ok=True)
                        return False
                    file.write(chunk)
            if size:
                return True
            output.unlink(missing_ok=True)
            return False
    return False


async def _facebook_page(client: httpx.AsyncClient, url: str) -> str:
    for _ in range(4):
        host = (urlsplit(url).hostname or "").lower()
        if not (host == "fb.watch" or host.endswith(".fb.watch") or host == "facebook.com" or host.endswith(".facebook.com")):
            raise ValueError("Facebook dışı yönlendirme")
        response = await client.get(url)
        if response.is_redirect:
            location = response.headers.get("location")
            if not location:
                raise ValueError("Geçersiz Facebook yönlendirmesi")
            url = urljoin(url, location)
            continue
        response.raise_for_status()
        return response.text[:5_000_000]
    raise ValueError("Çok fazla Facebook yönlendirmesi")


def x_media_urls(post: dict) -> list[tuple[str, str]]:
    media = post.get("media") or {}
    items = media.get("all") or [*(media.get("photos") or []), *(media.get("videos") or [])]
    if not items and isinstance(post.get("quote"), dict):
        quoted_media = post["quote"].get("media") or {}
        items = quoted_media.get("all") or [*(quoted_media.get("photos") or []), *(quoted_media.get("videos") or [])]
    urls: list[tuple[str, str]] = []
    for item in items[:LIMIT]:
        if not isinstance(item, dict):
            continue
        kind = "video" if item.get("type") in {"video", "gif"} else "photo"
        formats = [f for f in item.get("formats", []) if isinstance(f, dict) and f.get("container") == "mp4" and f.get("url")]
        formats.sort(key=lambda f: (f.get("height") or 0, f.get("bitrate") or 0), reverse=True)
        url = (formats[0]["url"] if formats else item.get("url"))
        if isinstance(url, str):
            urls.append((url, kind))
    return urls


def reddit_media_urls(data: dict) -> list[tuple[str, str]]:
    urls: list[tuple[str, str]] = []
    metadata = data.get("media_metadata") or {}
    for item in (data.get("gallery_data") or {}).get("items", [])[:LIMIT]:
        source = (metadata.get(item.get("media_id")) or {}).get("s") or {}
        url = source.get("mp4") or source.get("gif") or source.get("u")
        if url:
            urls.append((html.unescape(url), "video" if source.get("mp4") else "photo"))
    if urls:
        return urls
    direct = data.get("url_overridden_by_dest") or data.get("url")
    if isinstance(direct, str) and allowed_media_url(direct, "reddit") and re.search(r"\.(?:jpg|jpeg|png|webp|gif)(?:\?|$)", direct, re.I):
        return [(html.unescape(direct), "photo")]
    video = ((data.get("secure_media") or data.get("media") or {}).get("reddit_video") or {}).get("fallback_url")
    if isinstance(video, str):
        return [(video, "video")]
    return []


def tikwm_media_urls(data: dict) -> list[tuple[str, str]]:
    images = data.get("images") or []
    if images:
        return [(urljoin("https://www.tikwm.com/", url), "photo") for url in images[:LIMIT] if isinstance(url, str)]
    url = data.get("hdplay") or data.get("play")
    return [(urljoin("https://www.tikwm.com/", url), "video")] if isinstance(url, str) else []


class _Meta(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "meta":
            return
        fields = dict(attrs)
        key = fields.get("property") or fields.get("name")
        if key and fields.get("content"):
            self.values[key] = fields["content"]


async def canonical_facebook_post(link: PostLink) -> PostLink | None:
    """Resolve a Facebook share link to the actual post URL for gallery extractors."""
    if link.platform != "facebook" or "/share/p/" not in urlsplit(link.url).path:
        return None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12, connect=4), follow_redirects=False,
                                     trust_env=False, headers={"User-Agent": USER_AGENT}) as client:
            page = await _facebook_page(client, link.url)
        parser = _Meta()
        parser.feed(page)
        canonical = parser.values.get("og:url")
        result = parse_link(canonical) if canonical else None
        return result if result and result.platform == "facebook" else None
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        LOG.info("Facebook canonical URL unavailable: %s", exc)
        return None


def facebook_media_urls(page: str, path: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for key in ("browser_native_hd_url", "browser_native_sd_url", "playable_url_quality_hd", "playable_url"):
        match = re.search(r'"' + key + r'"\s*:\s*"((?:\\.|[^"\\])+)"', page)
        if match:
            raw = match.group(1)
            try:
                url = bytes(raw, "utf-8").decode("unicode_escape")
            except UnicodeError:
                continue
            url = html.unescape(url.replace("\\/", "/"))
            if allowed_media_url(url, "facebook"):
                found.append((url, "video"))
                break
    parser = _Meta()
    parser.feed(page)
    if not found:
        for key in ("og:video:secure_url", "og:video"):
            url = parser.values.get(key)
            if url and allowed_media_url(url, "facebook"):
                found.append((url, "video"))
                break
    if not found and ("/photo" in path or "/share/p/" in path or "/posts/" in path):
        url = parser.values.get("og:image")
        if url and allowed_media_url(url, "facebook"):
            found.append((url, "photo"))
    return found


async def download_direct(link: PostLink, folder: Path, max_bytes: int) -> tuple[int, str]:
    """Download via one platform-specific API or public-page extractor."""
    try:
        timeout = httpx.Timeout(30, connect=5)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False, headers={"User-Agent": USER_AGENT}) as client:
            if link.platform == "x":
                post = await fetch_post(link.post_id or "", enrich_quotes=False)
                urls = x_media_urls(post) if post else []
            elif link.platform == "reddit":
                match = re.search(r"/comments/([a-z0-9]+)|/gallery/([a-z0-9]+)|redd\.it/([a-z0-9]+)", link.url, re.I)
                if not match:
                    return 0, "unsupported Reddit URL"
                post_id = next(group for group in match.groups() if group)
                response = await client.get(f"https://www.reddit.com/comments/{post_id}.json",
                                            params={"raw_json": "1"}, timeout=httpx.Timeout(2, connect=1))
                response.raise_for_status()
                payload = response.json()
                data = payload[0]["data"]["children"][0]["data"]
                urls = reddit_media_urls(data)
            elif link.platform == "tiktok":
                response = await client.post("https://www.tikwm.com/api/", data={"url": link.url, "hd": "1"})
                response.raise_for_status()
                payload = response.json()
                urls = tikwm_media_urls(payload.get("data") or {}) if payload.get("code") == 0 else []
            elif link.platform == "facebook":
                page = await _facebook_page(client, link.url)
                urls = facebook_media_urls(page, urlsplit(link.url).path)
            elif link.platform == "streamable":
                response = await client.get(f"https://api.streamable.com/videos/{link.post_id}")
                response.raise_for_status()
                variants = response.json().get("files") or {}
                urls = []
                for name in ("mp4", "mp4-mobile"):
                    item = variants.get(name) or {}
                    raw = item.get("url")
                    if isinstance(raw, str):
                        candidate = urljoin("https://streamable.com/", raw)
                        if allowed_media_url(candidate, "streamable"):
                            urls = [(candidate, "video")]
                            break
            elif link.platform == "douyin" and "/video/" in urlsplit(link.url).path:
                response = await client.get(link.url)
                response.raise_for_status()
                parser = _Meta()
                parser.feed(response.text[:3_000_000])
                candidate = parser.values.get("og:video") or parser.values.get("og:video:secure_url")
                urls = [(candidate, "video")] if candidate and allowed_media_url(candidate, "douyin") else []
            else:
                return 0, "unsupported platform"
            count = 0
            for index, (url, kind) in enumerate(urls[:LIMIT], 1):
                output = folder / f"direct_{index}{_suffix(url, kind)}"
                if await _stream(client, url, link.platform, output, max_bytes):
                    count += 1
            if urls and count < len(urls[:LIMIT]):
                return 0, f"incomplete gallery ({count}/{len(urls[:LIMIT])})"
            return count, "" if count else "no usable media"
    except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError, OSError) as exc:
        LOG.info("Direct provider failed for %s: %s", link.platform, exc)
        return 0, str(exc)[:200]


def vx_media_urls(data: dict) -> list[tuple[str, str]]:
    extended = data.get("media_extended") or []
    if extended:
        return [(item["url"], "video" if item.get("type") == "video" else "photo")
                for item in extended[:LIMIT] if isinstance(item, dict) and isinstance(item.get("url"), str)]
    return [(url, "video" if ".mp4" in urlsplit(url).path.lower() else "photo")
            for url in (data.get("mediaURLs") or [])[:LIMIT] if isinstance(url, str)]


async def download_vxtwitter(post_id: str, folder: Path, max_bytes: int) -> tuple[int, str]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5), follow_redirects=False, trust_env=False,
                                     headers={"User-Agent": USER_AGENT}) as client:
            response = await client.get(f"https://api.vxtwitter.com/i/status/{post_id}")
            response.raise_for_status()
            urls = vx_media_urls(response.json())
            count = 0
            for index, (url, kind) in enumerate(urls, 1):
                output = folder / f"vx_{index}{_suffix(url, kind)}"
                if await _stream(client, url, "x", output, max_bytes):
                    count += 1
            if urls and count < len(urls):
                return 0, f"incomplete gallery ({count}/{len(urls)})"
            return count, "" if count else "no usable media"
    except (httpx.HTTPError, ValueError, TypeError, KeyError, OSError) as exc:
        LOG.info("VxTwitter provider failed: %s", exc)
        return 0, str(exc)[:200]


