"""Keyless indown.io Instagram viewer for stories, highlights and posts.

The site exposes a small HTML form (CSRF token + cookie session) instead of a
JSON API. It resolves the link server-side and renders Instagram CDN URLs, so
no Instagram cookie of our own is involved. Cloudflare rejects plain httpx
fingerprints on this host, hence the curl_cffi session.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from .direct_providers import _stream, _suffix, allowed_media_url
from .links import PostLink
from .limits import MAX_MEDIA_ITEMS
from .video_fit import fit_video

try:  # Optional at import time so a missing wheel cannot break the bot.
    from curl_cffi import requests as curl_requests
except ImportError:  # pragma: no cover - exercised only without the dependency
    curl_requests = None

LOG = logging.getLogger(__name__)
BASE = "https://indown.io"
DOWNLOAD = f"{BASE}/download"
PAGES = {
    "highlights": f"{BASE}/instagram-highlights-download/en3",
    "stories": f"{BASE}/insta-stories-download/en3",
    "reels": f"{BASE}/reels/en5",
    "post": f"{BASE}/photo/en4",
}
PRIVATE_HINTS = ("this account is private", "account is private", "private account")
RATE_HINTS = ("too many requests", "maximum 20 times in an hour")


def page_for(url: str) -> str:
    path = urlsplit(url).path.lower()
    if "/highlights/" in path:
        return PAGES["highlights"]
    if "/stories/" in path or path.startswith("/story/"):
        return PAGES["stories"]
    if "/reel/" in path or path.startswith("/tv/"):
        return PAGES["reels"]
    return PAGES["post"]


def story_id(url: str) -> str | None:
    """The media id of a single story link, but not of a highlight album."""
    match = re.search(r"/stories/(?!highlights/)[^/]+/(\d{5,})", urlsplit(url).path, re.I)
    return match.group(1) if match else None


def _cache_key(value: str) -> str | None:
    """Decode the ig_cache_key query value into the numeric media id."""
    raw = parse_qs(urlsplit(value).query).get("ig_cache_key", [""])[0]
    encoded = raw.split(".")[0]
    if not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode("ascii", "ignore")
    except (ValueError, base64.binascii.Error):
        return None
    return decoded if decoded.isdigit() else None


def _proxied_url(value: str) -> str:
    """The Instagram URL wrapped by an indown.io /fetch proxy, if any."""
    return parse_qs(urlsplit(value).query).get("url", [""])[0]


def _kind(url: str) -> str:
    return "video" if Path(urlsplit(url).path).suffix.lower() in {".mp4", ".mov", ".webm"} else "photo"


class _ResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, str, str | None]] = []
        self.key: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "video":
            self.key = _cache_key(_proxied_url(values.get("poster") or "")) or self.key
        elif tag == "img":
            source = values.get("src") or ""
            self.key = _cache_key(_proxied_url(source)) or _cache_key(source) or self.key
        elif tag == "source":
            url = values.get("src") or ""
            if allowed_media_url(url, "instagram"):
                self.items.append((url, "video", self.key))
        elif tag == "a":
            url = values.get("href") or ""
            if allowed_media_url(url, "instagram"):
                self.items.append((url, _kind(url), _cache_key(url) or self.key))


def indownio_urls(markup: str, only_story: str | None = None) -> list[tuple[str, str]]:
    """Return ordered, de-duplicated Instagram media from an indown.io result page."""
    parser = _ResultParser()
    parser.feed(markup)
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for url, kind, key in parser.items:
        if only_story and key and key != only_story:
            continue
        base = urlsplit(url)._replace(query="").geturl()
        if base in seen:
            continue
        seen.add(base)
        found.append((url, kind))
        if len(found) >= MAX_MEDIA_ITEMS:
            break
    return found


def _message(markup: str) -> str:
    lowered = markup.lower()
    if any(hint in lowered for hint in PRIVATE_HINTS):
        return "login required: private account"
    if any(hint in lowered for hint in RATE_HINTS):
        return "indown.io hourly request limit reached (20/hour)"
    return "no usable media"


def is_rate_limited_message(message: str) -> bool:
    lowered = (message or "").lower()
    return any(hint in lowered for hint in RATE_HINTS) or "hourly request limit" in lowered


def _resolve(page: str, link: str) -> tuple[str, str]:
    """Fetch the form token, submit the link and return the result page."""
    if curl_requests is None:
        return "", "curl_cffi kurulu değil"
    with curl_requests.Session(impersonate="chrome") as session:
        form = session.get(page, timeout=40)
        if form.status_code >= 400:
            return "", f"form HTTP {form.status_code}"
        token = re.search(r'name="_token" value="([^"]+)"', form.text)
        if not token:
            return "", "no form token"
        result = session.post(DOWNLOAD, data={
            "referer": page, "locale": "en", "_token": token.group(1), "link": link,
        }, headers={"Origin": BASE, "Referer": page}, timeout=60)
        if result.status_code >= 400:
            return "", f"result HTTP {result.status_code}"
        return result.text, ""


async def resolve_indownio(link: PostLink) -> tuple[int, str]:
    """Resolve an Instagram URL via indown.io without downloading media.

    Returns (media_count, detail). detail is empty on success; on failure it
    contains a message (rate limit, private, service error, etc.).
    """
    try:
        markup, error = await asyncio.to_thread(_resolve, page_for(link.url), link.url)
    except Exception as exc:  # curl_cffi raises its own error hierarchy
        LOG.info("indown.io resolve failed for %s: %s", link.platform, exc)
        return 0, str(exc)[:200]
    if error:
        return 0, error
    urls = indownio_urls(markup, story_id(link.url))
    if urls:
        return len(urls), ""
    return 0, _message(markup)


async def download_indownio(link: PostLink, folder: Path, max_bytes: int) -> tuple[int, str]:
    """Download public Instagram stories, highlights and posts without cookies."""
    try:
        markup, error = await asyncio.to_thread(_resolve, page_for(link.url), link.url)
    except Exception as exc:  # curl_cffi raises its own error hierarchy
        LOG.info("indown.io request failed for %s: %s", link.platform, exc)
        return 0, str(exc)[:200]
    if error:
        return 0, error
    urls = indownio_urls(markup, story_id(link.url))
    if not urls:
        return 0, _message(markup)
    async with httpx.AsyncClient(timeout=httpx.Timeout(40, connect=10), follow_redirects=True,
                                 trust_env=False, transport=httpx.AsyncHTTPTransport(retries=2)) as client:
        count = 0
        for index, (url, kind) in enumerate(urls, 1):
            output = folder / f"indownio_{index}{_suffix(url, kind)}"
            if kind == "video":
                raw = folder / f"indownio_{index}_raw.mp4"
                try:
                    if await _stream(client, url, "instagram", raw, min(max_bytes * 4, 200 * 1024 * 1024)):
                        if await fit_video(raw, output, max_bytes):
                            count += 1
                finally:
                    raw.unlink(missing_ok=True)
            elif await _stream(client, url, "instagram", output, max_bytes):
                count += 1
        if count != len(urls):
            return 0, f"incomplete gallery ({count}/{len(urls)}); media may exceed upload limit"
        return count, ""
