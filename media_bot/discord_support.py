"""Discord-facing helpers: formatting, adapters, and UI builders."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import discord

# Discord's highest upload limit. Anything above this is a config mistake.
UPLOAD_CEILING = 500 * 1024 * 1024
VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".m4v"}
COLORS = {"instagram": 0xC13584, "facebook": 0x1877F2, "reddit": 0xFF4500,
          "tiktok": 0x25F4EE, "youtube": 0xFF0033, "x": 0x20262E,
          "threads": 0x20262E, "douyin": 0x25F4EE, "weibo": 0xE6162D,
          "streamable": 0x0F8FFA, "bluesky": 0x1083FE}


def upload_limit(message: object | None, configured: int) -> int:
    """Use the guild's real upload limit, optionally capped by ``configured``."""
    limit = 0
    guild = getattr(message, "guild", None)
    try:
        limit = int(getattr(guild, "filesize_limit", 0) or 0)
    except (TypeError, ValueError):
        limit = 0
    if configured > 0:
        limit = min(limit, configured) if limit else configured
    if limit <= 0:
        limit = configured or UPLOAD_CEILING
    return max(1, min(limit, UPLOAD_CEILING))


def source_button(url: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Kaynak", emoji="🔗", style=discord.ButtonStyle.link, url=url))
    return view


def _reel_caption(caption: str) -> tuple[str, str | None, str | None]:
    """Split Instagram's "<text> - @user on <date>" tail into handle and date."""
    match = re.search(r"\s*-\s*@([\w.]+)\s+on\s+(.+?)\s*$", caption, re.DOTALL)
    if not match:
        return caption.strip(), None, None
    return caption[:match.start()].rstrip(), match.group(1), match.group(2).strip()


_MONTHS_TR = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
              "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")


def _instagram_date(media_id: str) -> str | None:
    """Decode the creation date from an Instagram media id (no request needed)."""
    if not media_id:
        return None
    try:
        moment = datetime.fromtimestamp(((int(media_id) >> 23) + 1314220021721) / 1000)
    except (ValueError, OverflowError, OSError):
        return None
    if not 2011 <= moment.year <= datetime.now().year + 1:
        return None
    return f"{moment.day} {_MONTHS_TR[moment.month - 1]} {moment.year}"


def _instagram_story_meta(url: str) -> str | None:
    """`@handle · date` footer for a story link; captions are never shown."""
    match = re.fullmatch(r"/stories/([^/]+)/(\d*)/?", urlsplit(url).path, re.IGNORECASE)
    if not match or match.group(1).lower() == "highlights":
        return None
    username = match.group(1)
    parts = [f"[@{username}](https://www.instagram.com/{username}/)"]
    posted = _instagram_date(match.group(2))
    if posted:
        parts.append(posted)
    return " · ".join(parts)


def _quote(text: str) -> str:
    """Bold every caption line inside a blockquote."""
    return "\n".join("> **" + line + "**" if line else ">" for line in text.splitlines())


def media_gallery_view(platform: str, files: list[Path], caption: str | None,
                       source_url: str, *, part: int = 1,
                       show_missing_caption: bool = True,
                       descriptions: list[str] | None = None) -> discord.ui.LayoutView:
    label = {"instagram": "Instagram", "facebook": "Facebook", "reddit": "Reddit",
             "tiktok": "TikTok", "youtube": "YouTube", "x": "X", "threads": "Threads",
             "douyin": "Douyin", "weibo": "Weibo", "streamable": "Streamable",
             "bluesky": "Bluesky"}[platform]
    heading = f"### {label} gönderisi" if part == 1 else f"### {label} gönderisi · {part}. bölüm"
    children: list[discord.ui.Item] = [discord.ui.TextDisplay(heading)]
    visual = [path for path in files if path.suffix.lower() != ".mp3"]
    audio = [path for path in files if path.suffix.lower() == ".mp3"]
    if (platform == "instagram" and part == 1 and len(visual) == 1
            and visual[0].suffix.lower() in {".mp4", ".mov", ".webm", ".m4v"}
            and "/reel/" in urlsplit(source_url).path.lower()):
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(discord.ui.MediaGallery(
            discord.MediaGalleryItem(f"attachment://{visual[0].name}")
        ))
        details: list[discord.ui.Item] = [
            discord.ui.TextDisplay("## Instagram Reel"),
            discord.ui.Separator(),
        ]
        body, username, posted = _reel_caption(caption) if caption else ("", None, None)
        if body:
            details.append(discord.ui.TextDisplay(_quote(body[:3600])))
        elif show_missing_caption:
            details.append(discord.ui.TextDisplay("-# Gönderi açıklamasına erişilemiyor."))
        meta = " · ".join(part for part in (
            f"[@{username}](https://www.instagram.com/{username}/)" if username else "",
            posted or "",
        ) if part)
        source = discord.ui.Button(label="Kaynak", emoji="🔗",
                                   style=discord.ButtonStyle.link, url=source_url)
        details.append(discord.ui.Section(discord.ui.TextDisplay("-# " + meta), accessory=source)
                       if meta else discord.ui.ActionRow(source))
        view.add_item(discord.ui.Container(*details, accent_color=COLORS[platform]))
        return view
    if visual:
        children.append(discord.ui.MediaGallery(*(
            discord.MediaGalleryItem(
                f"attachment://{path.name}",
                description=(descriptions[index][:1024] or None) if descriptions and index < len(descriptions) else None,
            ) for index, path in enumerate(visual)
        )))
    for path in audio:
        children.append(discord.ui.File(f"attachment://{path.name}"))
    story_meta = _instagram_story_meta(source_url) if platform == "instagram" else None
    if caption:
        children.append(discord.ui.Separator())
        if platform == "facebook" and caption.rstrip().endswith(("...", "…")):
            children.append(discord.ui.TextDisplay("-# Açıklama önizlemesi"))
        children.append(discord.ui.TextDisplay(_quote(caption[:3600])))
    elif part == 1 and not story_meta and show_missing_caption:
        children.append(discord.ui.TextDisplay("-# Gönderi açıklamasına erişilemiyor."))
    source = discord.ui.Button(label="Kaynak", emoji="🔗", style=discord.ButtonStyle.link, url=source_url)
    if story_meta:
        children.append(discord.ui.Section(discord.ui.TextDisplay("-# " + story_meta), accessory=source))
    else:
        children.append(discord.ui.ActionRow(source))
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*children, accent_color=COLORS[platform]))
    return view


def reddit_embed_preview(link, embeds: list[discord.Embed]):
    """Use Discord's Reddit unfurl when Reddit blocks anonymous requests."""
    from .metadata_fallback import Preview

    subreddit = re.search(r"/r/([^/]+)", urlsplit(link.url).path, re.IGNORECASE)
    for embed in embeds:
        data = embed.to_dict()
        provider = (data.get("provider") or {}).get("name", "").lower()
        embed_url = data.get("url") or ""
        host = (urlsplit(embed_url).hostname or "").lower()
        if provider != "reddit" and host not in {"reddit.com", "www.reddit.com", "old.reddit.com"}:
            continue
        title = data.get("title") or None
        if subreddit and title:
            title = f"r/{subreddit.group(1)} · {title}"
        elif subreddit:
            title = f"r/{subreddit.group(1)}"
        description = data.get("description") or None
        image = (data.get("image") or {}).get("url") or (data.get("thumbnail") or {}).get("url")
        return Preview(title=title, description=description, image=image)
    return Preview()


class InteractionMessage:
    """Message-like adapter so prefix handlers can serve slash commands too."""

    def __init__(self, interaction: discord.Interaction, *, mentions: list | None = None):
        self.interaction = interaction
        self.author = interaction.user
        self.guild = interaction.guild
        self.channel = interaction.channel
        self.id = interaction.id
        self.mentions = mentions or []

    async def reply(self, content: str = "", **kwargs):
        # Interaction responses do not support message-only options.
        kwargs.pop("mention_author", None)
        kwargs.pop("suppress_embeds", None)
        if self.interaction.response.is_done():
            return await self.interaction.followup.send(content=content or None, **kwargs)
        return await self.interaction.response.send_message(content=content or None, **kwargs)


class ThreadMessage:
    """Message-like adapter that sends every reply into an auto-created thread."""

    def __init__(self, original, thread):
        self.original = original
        self.thread = thread

    def __getattr__(self, name):
        return getattr(self.original, name)

    @property
    def channel(self):
        return self.thread

    async def reply(self, content: str = "", *, files=None, embed=None, view=None,
                    mention_author: bool = False, suppress_embeds: bool = False, **kwargs):
        return await self.thread.send(
            content=(content or None), files=files or [], embed=embed, view=view,
            suppress_embeds=suppress_embeds, **kwargs,
        )
