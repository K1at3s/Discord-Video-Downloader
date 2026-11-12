"""Render an X post like a tweet shared to a story."""

from __future__ import annotations

import asyncio
import io
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageOps

LOG = logging.getLogger(__name__)
def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        [r"C:\Windows\Fonts\segoeuib.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
        if bold else [r"C:\Windows\Fonts\segoeui.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int, max_lines: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        current = ""
        for word in words:
            candidate = (current + " " + word).strip()
            if draw.textlength(candidate, font=font) <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
                while draw.textlength(current, font=font) > max_width and len(current) > 1:
                    cut = len(current) - 1
                    while cut > 1 and draw.textlength(current[:cut], font=font) > max_width:
                        cut -= 1
                    lines.append(current[:cut])
                    current = current[cut:]
            if len(lines) >= max_lines:
                break
        if len(lines) >= max_lines:
            break
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    if len(lines) == max_lines and len(text) > sum(len(line) for line in lines) + max_lines:
        lines[-1] = lines[-1].rstrip(" .") + "…"
    return lines


def _count(value: object) -> str:
    if value is None:
        return "—"
    try:
        number = int(value or 0)
    except (ValueError, TypeError):
        return "—"
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 100_000:
        return f"{number / 1_000:.1f}K"
    return f"{number:,}".replace(",", ".")


def normalize_vxtwitter(data: dict, *, include_quote: bool = True) -> dict | None:
    """Translate VXTwitter's public response into the card/media schema."""
    text = data.get("text")
    if not isinstance(text, str) or not text:
        return None
    media = []
    for item in (data.get("media_extended") or [])[:10]:
        if isinstance(item, dict) and isinstance(item.get("url"), str):
            media.append({"url": item["url"], "type": "video" if item.get("type") == "video" else "photo"})
    if not media:
        for url in (data.get("mediaURLs") or [])[:10]:
            if isinstance(url, str):
                media.append({"url": url, "type": "video" if ".mp4" in url else "photo"})
    date = data.get("date") or ""
    epoch = data.get("date_epoch")
    if isinstance(epoch, (int, float)):
        date = datetime.fromtimestamp(epoch, timezone.utc).isoformat()
    quote = data.get("qrt")
    return {
        "id": data.get("tweetID"),
        "text": text,
        "author": {"name": data.get("user_name"), "screen_name": data.get("user_screen_name"),
                   "avatar_url": data.get("user_profile_image_url"),
                   "verified": data.get("user_blue_verified") is True or data.get("user_verified") is True},
        "created_at": date,
        "replies": data.get("replies"),
        "reposts": data.get("retweets"),
        "likes": data.get("likes"),
        "views": data.get("views"),
        "media": {"all": media},
        "quote": normalize_vxtwitter(quote, include_quote=False) if include_quote and isinstance(quote, dict) else None,
    }


def normalize_syndication(data: dict, *, depth: int = 0) -> dict | None:
    """Translate X's public embed response, including verification and quotes."""
    text = data.get("text")
    if not isinstance(text, str) or not text:
        return None
    user = data.get("user") or {}
    if not isinstance(user, dict):
        user = {}
    media = []
    for item in (data.get("mediaDetails") or [])[:10]:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        url = item.get("media_url_https") or item.get("media_url")
        if kind in {"video", "animated_gif"}:
            formats = (item.get("video_info") or {}).get("variants") or []
            options = [part for part in formats if isinstance(part, dict) and part.get("content_type") == "video/mp4" and part.get("url")]
            options.sort(key=lambda part: part.get("bitrate") or 0, reverse=True)
            if options:
                url = options[0]["url"]
        if isinstance(url, str):
            media.append({"url": url, "type": "video" if kind in {"video", "animated_gif"} else "photo"})
    quote = data.get("quoted_tweet")
    return {
        "id": data.get("id_str"),
        "text": text,
        "author": {"name": user.get("name"), "screen_name": user.get("screen_name"),
                   "avatar_url": user.get("profile_image_url_https") or user.get("profile_image_url"),
                   "verified": user.get("is_blue_verified") is True or user.get("verified") is True},
        "created_at": data.get("created_at"),
        "replies": data.get("conversation_count"),
        "reposts": data.get("retweet_count"),
        "likes": data.get("favorite_count"),
        "views": data.get("view_count"),
        "media": {"all": media},
        "quote": normalize_syndication(quote, depth=depth + 1) if depth < 2 and isinstance(quote, dict) else None,
    }


async def fetch_post(post_id: str, *, enrich_quotes: bool = True) -> dict | None:
    if not re.fullmatch(r"\d{5,25}", post_id):
        return None
    sources = (("X Syndication", "https://cdn.syndication.twimg.com/tweet-result"),
               ("VxTwitter", f"https://api.vxtwitter.com/i/status/{post_id}"),
               ("FxTwitter", f"https://api.fxtwitter.com/2/status/{post_id}"))
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        for name, url in sources:
            try:
                params = {"id": post_id, "token": "0", "lang": "tr"} if name == "X Syndication" else None
                response = await client.get(url, params=params)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    continue
                if name == "X Syndication":
                    post = normalize_syndication(payload)
                elif name == "VxTwitter":
                    post = normalize_vxtwitter(payload)
                else:
                    post = payload.get("status") or payload.get("tweet")
                if isinstance(post, dict) and post.get("text"):
                    quoted = post.get("quote")
                    quoted_id = quoted.get("id") if isinstance(quoted, dict) else None
                    if enrich_quotes and isinstance(quoted_id, str) and re.fullmatch(r"\d{5,25}", quoted_id) and not quoted.get("quote"):
                        try:
                            detail = await client.get("https://cdn.syndication.twimg.com/tweet-result",
                                                      params={"id": quoted_id, "token": "0", "lang": "tr"})
                            detail.raise_for_status()
                            full_quote = normalize_syndication(detail.json())
                            if full_quote:
                                post["quote"] = full_quote
                        except (httpx.HTTPError, ValueError, TypeError) as exc:
                            LOG.info("Nested X quote unavailable for %s: %s", quoted_id, exc)
                    return post
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                LOG.info("%s metadata unavailable for %s: %s", name, post_id, exc)
    return None


async def _avatar(url: str | None) -> Image.Image | None:
    if not url or not re.fullmatch(r"https://pbs\.twimg\.com/[^\s]+", url):
        return None
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            response = await client.get(url)
            response.raise_for_status()
            if len(response.content) > 2_000_000:
                return None
        image = Image.open(io.BytesIO(response.content)).convert("RGB")
        return ImageOps.fit(image, (96, 96))
    except (httpx.HTTPError, OSError):
        return None


async def create_card(post: dict, output: Path, footer: str = "Discord Video Bot") -> None:
    from .x_story import render_story

    nodes = [post]
    current = post
    for _ in range(2):
        quoted = current.get("quote") if isinstance(current, dict) else None
        if not isinstance(quoted, dict):
            break
        nodes.append(quoted)
        current = quoted
    avatars = await asyncio.gather(*(
        _avatar((node.get("author") or {}).get("avatar_url") or
                (node.get("author") or {}).get("profile_image_url"))
        for node in nodes
    ))
    await asyncio.to_thread(render_story, post, avatars, output, footer)
