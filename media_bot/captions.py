"""Best-effort public post captions, fetched alongside the media download."""

from __future__ import annotations

import asyncio
import html
import logging
import re
import sys
from pathlib import Path

import discord
import httpx

from .direct_providers import _Meta, _facebook_page
from .links import PostLink
from .race import first_success
from .reddit_post import fetch_reddit_post
from .threads_links import CONTENT_HEADERS, _threads_cookies

LOG = logging.getLogger(__name__)
TIMEOUT = httpx.Timeout(8, connect=4)


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = html.unescape(value).replace("\r\n", "\n").strip()
    text = re.sub(r"[ \t]+", " ", text)
    return text or None


def _page_caption(page: str, platform: str, *, allow_truncated: bool = False) -> str | None:
    parser = _Meta()
    parser.feed(page)
    values = parser.values
    if platform == "instagram":
        description = values.get("og:description") or values.get("description") or ""
        # Instagram prefixes captions with engagement counts and an author, e.g.
        # `16K likes, 48 comments - user on November 12, 2026: "<caption>"`.
        match = re.search(r"[\"“](.+)[\"”]\s*$", description, re.S)
        return _clean(match.group(1) if match else description)
    description = values.get("og:description") or values.get("description") or values.get("twitter:description")
    cleaned = _clean(description)
    if platform == "threads" and cleaned and cleaned.lower().startswith("join threads to share ideas"):
        return None
    if cleaned and (allow_truncated or not cleaned.endswith(("...", "…"))) and not re.search(r"^(?:log in|sign up|facebook helps you|see posts, photos)", cleaned, re.I):
        return cleaned
    return None


def _reddit_id(url: str) -> str | None:
    match = re.search(r"/comments/([a-z0-9]+)|/gallery/([a-z0-9]+)|redd\.it/([a-z0-9]+)", url, re.I)
    return next((group for group in match.groups() if group), None) if match else None


async def _fetch(link: PostLink, route: str, cookie_file: Path | None = None) -> str | None:
    threads_page = link.platform == "threads" and route == "page"
    headers = CONTENT_HEADERS if threads_page else None
    cookies = _threads_cookies(cookie_file) if threads_page else None
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False, trust_env=False,
                                 headers=headers, cookies=cookies) as client:
        if route == "tikwm":
            response = await client.post("https://www.tikwm.com/api/", data={"url": link.url, "hd": "1"})
            response.raise_for_status()
            data = response.json()
            return _clean((data.get("data") or {}).get("title")) if data.get("code") == 0 else None
        if route == "reddit":
            post_id = _reddit_id(link.url)
            if not post_id:
                return None
            response = await client.get(f"https://www.reddit.com/comments/{post_id}.json", params={"raw_json": "1"})
            response.raise_for_status()
            data = response.json()[0]["data"]["children"][0]["data"]
            title, body = _clean(data.get("title")), _clean(data.get("selftext"))
            return "\n\n".join(part for part in (title, body) if part) or None
        if route == "reddit_rss":
            post = await fetch_reddit_post(link)
            return "\n\n".join(part for part in (post.title, post.body) if part) if post else None
        if route == "gendownload":
            response = await client.post("https://gendownload.com/api/extract", json={"url": link.url})
            response.raise_for_status()
            data = response.json()
            return _clean(data.get("description")) or _clean(data.get("title"))
        if route == "page":
            if link.platform == "facebook":
                page = await _facebook_page(client, link.url)
            else:
                response = await client.get(link.url)
                response.raise_for_status()
                page = response.text[:5_000_000]
            return _page_caption(page, link.platform, allow_truncated=link.platform == "facebook")
        if route == "youtube":
            response = await client.get("https://www.youtube.com/oembed", params={"url": link.url, "format": "json"})
            response.raise_for_status()
            return _clean(response.json().get("title"))
        if route == "streamable":
            response = await client.get("https://api.streamable.com/oembed.json", params={"url": link.url})
            response.raise_for_status()
            return _clean(response.json().get("title"))
    return None


async def _gallery_caption(link: PostLink, cookie_file: Path) -> str | None:
    """Ask gallery-dl for authenticated Facebook post metadata when configured."""
    from .cookiefile import netscape_jar
    from .direct_providers import canonical_facebook_post

    jar = netscape_jar(cookie_file)
    if jar is None:
        return None
    canonical = await canonical_facebook_post(link)
    target = canonical or link
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "gallery_dl", "--ignore-config", "--no-input",
        "--cookies", str(jar), "--range", "1", "--simulate", "--print", "{post_text}", target.url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        output, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return None
    if proc.returncode != 0:
        return None
    return _clean(output.decode("utf-8", "replace"))


async def fetch_caption(link: PostLink, cookie_file: Path | None = None) -> str | None:
    routes = {
        "instagram": ["page", "gendownload"],
        "facebook": ["page", "gendownload"],
        "tiktok": ["tikwm", "gendownload"],
        "reddit": ["reddit_rss", "reddit"],
        "youtube": ["gendownload", "youtube"],
        "threads": ["page"],
        "douyin": ["page"],
        "weibo": ["page"],
        "streamable": ["streamable", "page"],
    }.get(link.platform, [])

    preview: str | None = None

    async def attempt(route: str) -> str | None:
        nonlocal preview
        try:
            result = await _gallery_caption(link, cookie_file) if route == "gallery" and cookie_file else await _fetch(link, route, cookie_file)
            if link.platform == "facebook" and result and result.endswith(("...", "…")):
                if route == "page":
                    preview = result
                return None
            return result
        except (httpx.HTTPError, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
            LOG.debug("Caption unavailable via %s for %s: %s", route, link.platform, exc)
            return None

    if link.platform == "facebook" and cookie_file:
        authenticated = await attempt("gallery")
        if authenticated:
            return authenticated
    if not routes:
        return None
    try:
        # The public Facebook page returns a preview quickly; do not hold the
        # media message while a third-party caption endpoint stalls.
        if link.platform == "facebook":
            complete = await asyncio.wait_for(first_success(routes, attempt, stagger=0, parallel=2), timeout=5)
        else:
            complete = await first_success(routes, attempt, stagger=0, parallel=2)
    except asyncio.TimeoutError:
        complete = None
    return complete or preview


def display_chunks(caption: str | None, limit: int = 3400) -> list[str]:
    """Split the complete caption across Discord TextDisplay-sized sections."""
    if not caption:
        return []
    escaped = discord.utils.escape_markdown(caption)
    return [escaped[index:index + limit] for index in range(0, len(escaped), limit)]


def caption_messages(caption: str | None, source_url: str) -> list[str]:
    """Quote the full caption in Discord-sized parts and end with a source link."""
    source = f"[Kaynak]({source_url})"
    if not caption:
        return [source]
    escaped = discord.utils.escape_markdown(caption)
    parts: list[str] = []
    current = ""
    for original in escaped.split("\n"):
        remaining = original
        while remaining or not original:
            piece, remaining = remaining[:1600], remaining[1600:]
            quoted = "> " + piece
            if current and len(current) + len(quoted) + 1 > 1800:
                parts.append(current)
                current = ""
            current += ("\n" if current else "") + quoted
            if not original:
                break
    if current:
        parts.append(current.rstrip())
    if not parts:
        return [source]
    if len(parts[-1]) + len(source) + 2 <= 1900:
        parts[-1] += "\n\n" + source
    else:
        parts.append(source)
    return parts
