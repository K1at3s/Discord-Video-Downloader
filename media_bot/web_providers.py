"""Keyless hosted extractors used as final media fallbacks.

These sites are independent of the local yt-dlp/gallery-dl commands. Keep
their responses in separate provider directories and reject partial albums.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from .direct_providers import _stream, _suffix, allowed_media_url
from .links import PostLink
from .limits import MAX_MEDIA_ITEMS
from .threads_links import CONTENT_HEADERS, _threads_cookies
from .video_fit import fit_video

LOG = logging.getLogger(__name__)
AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
BTCH_BASE = "https://btch-downloader-api-green.vercel.app"
VETTE_BASE = "https://www.socialdownloader.space"
THREADS_BASE = "https://threadsphotodownloader.com"


class _MediaHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, set[str]]] = []
        self.anchors: list[dict[str, str | set[str]]] = []
        self.images: list[dict[str, str | set[str]]] = []
        self.current: dict[str, str | set[str]] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        ancestors = set().union(*(entry[1] for entry in self.stack)) if self.stack else set()
        context = classes | ancestors
        if tag == "a":
            self.current = {"href": html.unescape(values.get("href") or ""),
                            "title": values.get("title") or "", "classes": context, "text": ""}
        elif tag == "img":
            self.images.append({"src": html.unescape(values.get("src") or ""), "classes": context})
        if tag not in {"img", "br", "hr", "input", "meta", "link"}:
            self.stack.append((tag, classes))

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current["text"] = str(self.current["text"]) + data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.current is not None:
            self.anchors.append(self.current)
            self.current = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


def _indown_urls(markup: str) -> list[tuple[str, str]]:
    parser = _MediaHTML()
    parser.feed(markup)
    found: list[tuple[str, str]] = []
    for item in parser.anchors:
        url = str(item["href"])
        if "download-items" not in item["classes"] or not allowed_media_url(url, "indown"):
            continue
        label = (str(item["text"]) + " " + str(item["title"])).lower()
        kind = "video" if re.search(r"video|mp4|reel", label) or re.search(r"\.(?:mp4|mov|webm)(?:\?|$)", url, re.I) else "photo"
        if url not in [entry[0] for entry in found]:
            found.append((url, kind))
    return found[:MAX_MEDIA_ITEMS]


def _tiktokio_urls(markup: str) -> list[tuple[str, str]]:
    parser = _MediaHTML()
    parser.feed(markup)
    slideshow = any("image-item" in item["classes"] for item in parser.anchors + parser.images)
    if slideshow:
        urls = [str(item["href"]) for item in parser.anchors if "image-item" in item["classes"]]
        if not urls:
            urls = [str(item["src"]) for item in parser.images if "image-item" in item["classes"]]
        return [(url, "photo") for url in dict.fromkeys(urls) if allowed_media_url(url, "tiktokio")][:MAX_MEDIA_ITEMS]
    candidates = [str(item["href"]) for item in parser.anchors
                  if "download-btn" in item["classes"] and
                  ("download-btn-blue" in item["classes"] or "download-btn-green" in item["classes"]
                   or "without watermark" in str(item["text"]).lower())]
    return [(url, "video") for url in candidates if allowed_media_url(url, "tiktokio")][:1]


def _embed_media(markup: str) -> list[tuple[str, str]]:
    """Read the full media list from the embed page's escaped contextJSON."""
    match = re.search(r'"contextJSON":"((?:[^"\\]|\\.)*)"', markup)
    if not match:
        return []
    try:
        media = json.loads(json.loads('"' + match.group(1) + '"'))["gql_data"]["shortcode_media"]
    except (ValueError, KeyError, TypeError):
        return []
    children = (media.get("edge_sidecar_to_children") or {}).get("edges") or []
    nodes = [edge.get("node") for edge in children if isinstance(edge, dict)] or [media]
    found: list[tuple[str, str]] = []
    for node in nodes:
        if not isinstance(node, dict):
            return []
        url = node.get("video_url") if node.get("is_video") else node.get("display_url")
        if not isinstance(url, str) or not allowed_media_url(url, "instagram"):
            return []  # a partial album is not worth winning the race with
        found.append((url, "video" if node.get("is_video") else "photo"))
        if len(found) == MAX_MEDIA_ITEMS:
            break
    return found


def _threads_page_urls(markup: str, post_id: str) -> list[tuple[str, str]]:
    """Extract every item of the requested Threads post from page JSON."""
    found: list[tuple[str, str]] = []

    def visit(value: object, depth: int = 0) -> bool:
        if depth > 40:
            return False
        if isinstance(value, list):
            return any(visit(item, depth + 1) for item in value)
        if not isinstance(value, dict):
            return False
        if value.get("code") == post_id and any(key in value for key in
                                                ("image_versions2", "video_versions", "carousel_media")):
            items = value.get("carousel_media") or [value]
            if not isinstance(items, list) or len(items) > MAX_MEDIA_ITEMS:
                return False
            candidate: list[tuple[str, str]] = []
            for item in items:
                if not isinstance(item, dict):
                    return False
                videos = item.get("video_versions") or []
                images = (item.get("image_versions2") or {}).get("candidates") or []
                if isinstance(videos, list) and videos:
                    url = next((v.get("url") for v in videos if isinstance(v, dict) and
                                isinstance(v.get("url"), str)), None)
                    kind = "video"
                elif isinstance(images, list) and images:
                    valid = [i for i in images if isinstance(i, dict) and isinstance(i.get("url"), str)]
                    best = max(valid, key=lambda i: (i.get("width") if isinstance(i.get("width"), int) else 0) *
                               (i.get("height") if isinstance(i.get("height"), int) else 0),
                               default=None)
                    url = best.get("url") if best else None
                    kind = "photo"
                else:
                    return False
                if not url or not allowed_media_url(url, "threads"):
                    return False
                candidate.append((url, kind))
            found.extend(candidate)
            return bool(found)
        return any(visit(item, depth + 1) for item in value.values())

    for match in re.finditer(r'<script\b[^>]*\btype=["\']application/json["\'][^>]*>(.*?)</script>',
                             markup, re.I | re.S):
        try:
            if visit(json.loads(match.group(1))):
                return found
        except (ValueError, TypeError):
            continue
        found.clear()
    return []


def _threadsdl_urls(markup: str) -> list[tuple[str, str]]:
    """Undo the download site's proxy prefix to reach the original CDN URL."""
    found: list[tuple[str, str]] = []
    for match in re.finditer(r'<(img|video)\b[^>]*\bsrc="([^"]+)"', markup, re.I):
        proxied = html.unescape(match.group(2))
        parts = urlsplit(proxied)
        if (parts.hostname or "").lower() != "cdn.threadsphotodownloader.com":
            continue
        host, _, path = parts.path.lstrip("/").partition("/")
        direct = urlunsplit(("https", host, "/" + path, parts.query, ""))
        if not allowed_media_url(direct, "threads"):
            continue
        # The site lists the author's avatar next to the post media; those URLs
        # use Instagram's profile-picture path types (-19 and t1.*-9).
        if re.search(r"(?:t51\.\d+-19|t1\.\d+-9)/", path):
            continue
        found.append((direct, "video" if match.group(1).lower() == "video" else "photo"))
        if len(found) == MAX_MEDIA_ITEMS:
            break
    return found


def _rapidsave_url(markup: str) -> str | None:
    parser = _MediaHTML()
    parser.feed(markup)
    for item in parser.anchors:
        url = str(item["href"])
        if "downloadbutton" in item["classes"] and allowed_media_url(url, "rapidsave"):
            return url
    return None


def _btch_urls(payload: dict, platform: str) -> list[tuple[str, str]]:
    normalized = payload.get("normalized") or {}
    if not payload.get("success") or normalized.get("kind") != "media":
        return []
    media = normalized.get("media") or []
    if not isinstance(media, list):
        return []
    # This API reports alternative video qualities as separate entries. It
    # does not promise a complete Facebook album or X mixed-media tweet.
    if platform in {"facebook", "x", "tiktok"}:
        for item in media:
            if isinstance(item, dict) and item.get("type") == "video" and isinstance(item.get("url"), str):
                return [(item["url"], "video")]
        return []
    return [(item["url"], "video" if item.get("type") == "video" else "photo")
            for item in media[:MAX_MEDIA_ITEMS] if isinstance(item, dict) and
            item.get("type") in {"video", "image"} and isinstance(item.get("url"), str)]


def _vette_urls(payload: dict, platform: str) -> list[tuple[str, str]]:
    if not payload.get("success"):
        return []
    metadata = payload.get("metadata") or {}
    images = metadata.get("images") or []
    if platform in {"instagram", "tiktok", "threads"} and isinstance(images, list) and images:
        urls = []
        for item in images[:MAX_MEDIA_ITEMS]:
            original = item.get("url") if isinstance(item, dict) else item
            kind = "video" if isinstance(item, dict) and item.get("kind") == "video" else "photo"
            if not isinstance(original, str) or not original:
                return []
            endpoint = "video" if kind == "video" else "image"
            if original.startswith(f"/api/{endpoint}?"):
                proxied = VETTE_BASE + original
            else:
                proxied = f"{VETTE_BASE}/api/{endpoint}?{urlencode({'url': original})}"
            urls.append((proxied, kind))
        return urls if len(urls) == min(len(images), MAX_MEDIA_ITEMS) else []
    video = payload.get("downloadUrl")
    parsed = urlsplit(video) if isinstance(video, str) else None
    if not parsed or parsed.path != "/api/video" or parsed.netloc not in {"", "www.socialdownloader.space"}:
        return []
    return [(f"{VETTE_BASE}{video}" if not parsed.netloc else video, "video")]


async def download_web(name: str, link: PostLink, folder: Path, max_bytes: int,
                       cookie_file: Path | None = None) -> tuple[int, str]:
    """Extract then stream a complete set; a partial set never wins the race."""
    try:
        # Some networks drop roughly half of the TLS handshakes to these hosts,
        # so a fresh connection usually succeeds where the first one failed.
        async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5), follow_redirects=False,
                                     trust_env=False, headers={"User-Agent": AGENT},
                                     cookies=_threads_cookies(cookie_file) if name == "threads-page" else None,
                                     transport=httpx.AsyncHTTPTransport(retries=3)) as client:
            if name == "embed" and link.platform == "instagram":
                # Instagram serves the login wall to desktop-browser UAs and the
                # public media JSON to short/bot UAs, exactly like the download sites.
                response = await client.get(link.url.rstrip("/") + "/embed/",
                                            headers={"User-Agent": "Mozilla/5.0"})
                response.raise_for_status()
                if "This account is private" in response.text or "is_private\":true" in response.text:
                    return 0, "login required: private account"
                urls = _embed_media(response.text)
                source = "instagram"
            elif name == "threads-page" and link.platform == "threads" and re.search(r"/post/([A-Za-z0-9_-]+)", link.url):
                response = await client.get(link.url.split("?")[0], headers=CONTENT_HEADERS)
                if response.is_redirect and "/login/" in response.headers.get("location", ""):
                    return 0, "login required"
                response.raise_for_status()
                post_id = re.search(r"/post/([A-Za-z0-9_-]+)", link.url).group(1)
                urls = _threads_page_urls(response.text, post_id)
                source = "threads"
            elif name == "threadsdl" and link.platform == "threads":
                # This site renders the post server-side, unlike threads.com
                # itself which redirects anonymous visitors to /login/.
                form = None
                for attempt in range(4):
                    try:
                        form = await client.get(THREADS_BASE + "/")
                        break
                    except httpx.ConnectError:
                        if attempt == 3:
                            raise
                        await asyncio.sleep(attempt + 1)
                form.raise_for_status()
                token = re.search(r'name="authenticity_token" value="([^"]+)"', form.text)
                if not token:
                    return 0, "no form token"
                job = None
                for attempt in range(4):
                    try:
                        job = await client.post(
                            THREADS_BASE + "/download",
                            data={"authenticity_token": token.group(1), "url": link.url.split("?")[0]},
                            headers={"Referer": THREADS_BASE + "/", "Origin": THREADS_BASE},
                        )
                        break
                    except httpx.ConnectError:
                        if attempt == 3:
                            raise
                        await asyncio.sleep(attempt + 1)
                location = job.headers.get("location") or ""
                if job.is_redirect and "/download/" in location:
                    job_url = urljoin(THREADS_BASE + "/", location).rstrip("/")
                elif "/download/" in job.url.path:
                    job_url = str(job.url).rstrip("/")
                else:
                    return 0, "post media unavailable"
                urls = []
                for _ in range(8):
                    try:
                        status = await client.get(job_url + "/status",
                                                  headers={"Turbo-Frame": "media_resolution"})
                    except httpx.ConnectError:
                        # The network sometimes drops every handshake to this
                        # host for a few seconds; the next poll usually works.
                        await asyncio.sleep(1.5)
                        continue
                    urls = _threadsdl_urls(status.text)
                    if urls or "retrieve media" in status.text:
                        break
                    await asyncio.sleep(1.5)
                source = "threads"
            elif name == "indown" and link.platform == "instagram":
                response = await client.post("https://indown.net/api/ajaxSearch",
                    data={"q": link.url.split("?")[0], "vt": "reel", "t": "media", "lang": "en", "v": "v2"},
                    headers={"X-Requested-With": "XMLHttpRequest", "Origin": "https://indown.net", "Referer": "https://indown.net/"})
                response.raise_for_status()
                payload = response.json()
                urls = _indown_urls(payload.get("data") or "") if payload.get("status") == "ok" else []
                if "/reel/" in urlsplit(link.url).path and any(kind == "video" for _, kind in urls):
                    urls = [(url, kind) for url, kind in urls if kind == "video"]
                source = "indown"
            elif name == "tiktokio" and link.platform == "tiktok":
                response = await client.post("https://tiktokio.com/api/v1/tk/html",
                    json={"vid": link.url.split("?")[0], "prefix": "tiktokio.com"},
                    headers={"Origin": "https://tiktokio.com", "Referer": "https://tiktokio.com/"})
                response.raise_for_status()
                urls = _tiktokio_urls(response.text)
                source = "tiktokio"
            elif name == "rapidsave" and link.platform == "reddit":
                response = await client.get("https://rapidsave.com/info", params={"url": link.url},
                                            headers={"Referer": "https://rapidsave.com/"})
                response.raise_for_status()
                video = _rapidsave_url(response.text)
                urls = [(video, "video")] if video else []
                source = "rapidsave"
            elif name == "vette" and link.platform in {"instagram", "tiktok", "facebook", "reddit", "threads"}:
                if link.platform == "facebook" and not any(segment in urlsplit(link.url).path
                    for segment in ("/videos/", "/watch/", "/reel/")):
                    return 0, "video links only"
                response = await client.post(f"{VETTE_BASE}/api/download", json={"url": link.url})
                response.raise_for_status()
                payload = response.json()
                if not payload.get("success"):
                    return 0, str(payload.get("error") or "no usable media")[:200]
                urls = _vette_urls(payload, link.platform)
                source = "vette"
            elif name == "btch" and (
                (link.platform == "instagram" and "/reel/" in urlsplit(link.url).path)
                or (link.platform == "tiktok" and "/video/" in urlsplit(link.url).path)
                or (link.platform == "facebook" and any(segment in urlsplit(link.url).path for segment in ("/videos/", "/watch/", "/reel/")))
            ):
                platform = "twitter" if link.platform == "x" else link.platform
                response = await client.get(f"{BTCH_BASE}/api/download/{platform}", params={"url": link.url})
                response.raise_for_status()
                urls = _btch_urls(response.json(), link.platform)
                source = "btch"
            else:
                return 0, "unsupported platform"
            if not urls:
                return 0, "no usable media"
            count = 0
            for index, (url, kind) in enumerate(urls, 1):
                if name == "btch":
                    # Use the API's protected streaming endpoint; never fetch an
                    # arbitrary URL supplied by a third-party JSON response.
                    url = str(client.build_request("GET", f"{BTCH_BASE}/api/fetch-media", params={"url": url}).url)
                if not allowed_media_url(url, source):
                    return 0, "untrusted media host"
                suffix_url = parse_qs(urlsplit(url).query).get("url", [url])[0] if name == "vette" else url
                output = folder / f"{name}_{index}{_suffix(suffix_url, kind)}"
                if kind == "video" and link.platform == "instagram":
                    raw = folder / f"{name}_{index}_raw.mp4"
                    try:
                        if await _stream(client, url, source, raw, min(max_bytes * 4, 200 * 1024 * 1024)):
                            if await fit_video(raw, output, max_bytes):
                                count += 1
                    finally:
                        raw.unlink(missing_ok=True)
                elif await _stream(client, url, source, output, max_bytes):
                    count += 1
            if count != len(urls):
                return 0, f"incomplete gallery ({count}/{len(urls)}); media may exceed upload limit"
            return count, ""
    except (httpx.HTTPError, ValueError, TypeError, KeyError, OSError) as exc:
        LOG.info("Web provider %s failed for %s: %s", name, link.platform, exc)
        return 0, str(exc)[:200]
