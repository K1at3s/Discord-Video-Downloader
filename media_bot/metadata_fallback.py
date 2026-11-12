"""Safe Open Graph preview when a public post's files cannot be downloaded."""

from __future__ import annotations

import html
from dataclasses import dataclass

import discord
import httpx

from .direct_providers import _Meta, _facebook_page, allowed_media_url
from .links import PostLink


@dataclass(frozen=True)
class Preview:
    title: str | None = None
    description: str | None = None
    image: str | None = None

    @property
    def has_content(self) -> bool:
        return bool(self.title or self.description or self.image)


def parse_preview(page: str, platform: str) -> Preview:
    parser = _Meta()
    parser.feed(page[:2_000_000])
    values = parser.values
    title = html.unescape(values.get("og:title") or values.get("twitter:title") or "").strip() or None
    description = html.unescape(values.get("og:description") or values.get("description") or
                                values.get("twitter:description") or "").strip() or None
    image = values.get("og:image") or values.get("twitter:image")
    if platform == "threads" and description and description.lower().startswith("join threads to share ideas"):
        return Preview()
    if image and not allowed_media_url(html.unescape(image), platform):
        image = None
    if title and title.casefold() in {platform.casefold(), "instagram", "facebook", "tiktok", "reddit", "x"}:
        title = None
    return Preview(title=title, description=description, image=html.unescape(image) if image else None)


async def fetch_preview(link: PostLink) -> Preview:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=4), follow_redirects=False,
                                     trust_env=False, headers={"User-Agent": "Mozilla/5.0"}) as client:
            if link.platform == "facebook":
                page = await _facebook_page(client, link.url)
            else:
                response = await client.get(link.url)
                response.raise_for_status()
                page = response.text
        return parse_preview(page, link.platform)
    except (httpx.HTTPError, ValueError, TypeError):
        return Preview()


def preview_embeds(link: PostLink, preview: Preview, caption: str | None = None,
                   *, media_attempted: bool = True) -> list[discord.Embed]:
    text = caption or preview.description or ""
    escaped = discord.utils.escape_markdown(text)
    quoted = "\n".join("> " + line for line in escaped.split("\n")) if escaped else ""
    chunks = [quoted[index:index + 3800] for index in range(0, len(quoted), 3800)] or [""]
    embeds = []
    for index, chunk in enumerate(chunks):
        title = preview.title or f"{link.platform.upper()} gönderisi"
        if index:
            title = f"{link.platform.upper()} gönderisi · devam"
        embed = discord.Embed(title=title[:256], url=link.url, description=chunk or None, color=0x5865F2)
        if index == 0 and preview.image:
            embed.set_image(url=preview.image)
        footer = "Medya indirilemedi" if media_attempted else "Gönderi önizlemesi"
        embed.set_footer(text=f"{footer} · Kaynak bağlantısı aşağıda")
        embeds.append(embed)
    return embeds
