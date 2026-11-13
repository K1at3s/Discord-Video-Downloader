"""Resolve Threads share links before passing them to media extractors."""

from __future__ import annotations

import logging
import re
from http.cookiejar import MozillaCookieJar
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from .links import PostLink, parse_link

LOG = logging.getLogger(__name__)
AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
# Threads only answers /share/ with the real post redirect when the request
# looks like a browser document load; an API-style GET returns an empty shell.
NAV_HEADERS = {
    "User-Agent": AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# A valid session can still receive Threads' empty shell under the Windows UA.
# Match the browser document identity used for post content extraction.
CONTENT_HEADERS = {
    **NAV_HEADERS,
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/139.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _post_link(raw: str) -> PostLink | None:
    link = parse_link(raw)
    if link and link.platform == "threads" and re.fullmatch(
        r"/@[^/]+/post/[A-Za-z0-9_-]+(?:/media)?/?", urlsplit(link.url).path
    ):
        return link
    return None


class _Canonical(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "meta" and values.get("property") in {"og:url", "al:web:url"}:
            self.urls.append(values.get("content") or "")
        elif tag == "link" and values.get("rel") == "canonical":
            self.urls.append(values.get("href") or "")


def _threads_scoped(cookies: httpx.Cookies) -> httpx.Cookies:
    """Threads accepts an Instagram session, so reuse it when no jar entry exists."""
    if not any("threads" in (cookie.domain or "") for cookie in cookies.jar):
        for cookie in list(cookies.jar):
            if (cookie.domain or "").lstrip(".").endswith("instagram.com"):
                for domain in (".threads.com", ".threads.net"):
                    cookies.set(cookie.name, cookie.value, domain=domain)
    return cookies


def _threads_cookies(path: Path | None) -> httpx.Cookies | None:
    """Accept both a Netscape cookie jar and a raw `Cookie:` header value."""
    if not path:
        return None
    try:
        text = path.read_text(encoding="utf-8-sig").strip()
    except OSError as exc:
        LOG.info("Threads cookie file unavailable: %s", exc)
        return None
    if text.startswith("#") or "\t" in text:
        jar = MozillaCookieJar(str(path))
        try:
            jar.load(ignore_discard=True, ignore_expires=False)
        except (OSError, ValueError) as exc:
            LOG.info("Threads cookie file unavailable: %s", exc)
            return None
        return _threads_scoped(httpx.Cookies(jar))
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()
    cookies = httpx.Cookies()
    for part in text.split(";"):
        name, separator, value = part.strip().partition("=")
        if not separator or not name:
            continue
        for domain in (".threads.com", ".threads.net"):
            cookies.set(name, value, domain=domain)
    if not len(cookies.jar):
        LOG.info("Threads cookie file contains no name=value pairs")
        return None
    return cookies


async def resolve_threads_link(link: PostLink, cookie_file: Path | None = None,
                               client: httpx.AsyncClient | None = None) -> PostLink | None:
    """Return the exact post behind a /share or /t link, if Threads reveals it."""
    if link.platform != "threads":
        return None
    if post := _post_link(link.url):
        return post
    if not cookie_file and re.fullmatch(r"/(?:share|t)/[A-Za-z0-9_-]+/?", urlsplit(link.url).path):
        # No cookie file: don't hit threads.com; let the provider resolve the
        # share link itself (e.g. threadsdl / vette).
        return link
    if not re.fullmatch(r"/(?:share|t)/[A-Za-z0-9_-]+/?", urlsplit(link.url).path):
        return None

    async def inspect(http: httpx.AsyncClient) -> PostLink | None:
        current = link.url
        for _ in range(4):
            try:
                response = await http.get(current, headers=NAV_HEADERS)
            except httpx.HTTPError as exc:
                LOG.info("Threads share URL unavailable: %s", exc)
                return None
            if response.is_redirect:
                location = urljoin(current, response.headers.get("location", ""))
                if post := _post_link(location):
                    return post
                parts = urlsplit(location)
                if parts.hostname not in {"threads.com", "www.threads.com", "threads.net", "www.threads.net"}:
                    return None
                if parts.path.rstrip("/") == "/login":
                    LOG.info("Threads share URL redirected to login")
                    return None
                current = location
                continue
            if post := _post_link(str(response.url)):
                return post
            if response.status_code != 200:
                return None
            parser = _Canonical()
            parser.feed(response.text[:2_000_000])
            return next((post for url in parser.urls if (post := _post_link(url))), None)
        return None

    if client is not None:
        return await inspect(client)
    async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=3), follow_redirects=False,
                                 trust_env=False, headers=NAV_HEADERS,
                                 cookies=_threads_cookies(cookie_file)) as http:
        return await inspect(http)
