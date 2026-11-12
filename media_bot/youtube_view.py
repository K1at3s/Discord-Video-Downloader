"""Discord Components V2 YouTube format prompt."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

import discord

from .captions import display_chunks, fetch_caption
from .download import DownloadError
from .links import PostLink
from .youtube import download_youtube

LOG = logging.getLogger(__name__)
LABELS = {"mp3": "🎵 MP3", "360": "📱 360p", "720": "📺 720p", "1080": "🖥️ 1080p"}


def status_view(title: str, detail: str) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView()
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(f"### 🎬 {discord.utils.escape_markdown(title[:180])}"),
        discord.ui.TextDisplay(detail),
        accent_color=0xEB3C47,
    ))
    return view


class FormatButton(discord.ui.Button):
    def __init__(self, choice: str):
        super().__init__(label=LABELS[choice], style=discord.ButtonStyle.secondary, custom_id=f"youtube:{choice}")
        self.choice = choice

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, YoutubeChoiceView):
            await view.choose(interaction, self.choice)


class YoutubeChoiceView(discord.ui.LayoutView):
    def __init__(self, *, bot, source_message: discord.Message, link: PostLink, title: str):
        super().__init__(timeout=300)
        self.bot = bot
        self.source_message = source_message
        self.link = link
        self.title = title
        self.prompt_message: discord.Message | None = None
        self.selected = False
        escaped = discord.utils.escape_markdown(title[:180])
        self.add_item(discord.ui.Container(
            discord.ui.TextDisplay(f"### 🎬 {escaped}"),
            discord.ui.TextDisplay(f"[YouTube gönderisi]({link.url})\n**Ne indirmek istersin?** Video seçenekleri azami çözünürlüktür."),
            discord.ui.ActionRow(FormatButton("mp3")),
            discord.ui.ActionRow(FormatButton("360"), FormatButton("720"), FormatButton("1080")),
            accent_color=0xEB3C47,
        ))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.source_message.author.id:
            await interaction.response.send_message("Bu seçim bağlantıyı paylaşan kişiye ait.", ephemeral=True)
            return False
        return True

    async def choose(self, interaction: discord.Interaction, choice: str) -> None:
        if self.selected:
            await interaction.response.send_message("Bir indirme zaten başlatıldı.", ephemeral=True)
            return
        self.selected = True
        await interaction.response.defer()
        self.stop()
        prompt = interaction.message
        if prompt:
            try:
                await prompt.edit(view=status_view(self.title, f"⏳ **{LABELS[choice]}** hazırlanıyor…"))
            except discord.HTTPException as exc:
                LOG.warning("Could not update YouTube prompt: %s", exc)
        try:
            async with self.bot.jobs:
                caption_task = asyncio.create_task(fetch_caption(self.link, self.bot.cookie_file))
                with tempfile.TemporaryDirectory(prefix="discord-youtube-") as temporary:
                    try:
                        file, provider = await download_youtube(
                            self.link, Path(temporary), choice, self.bot.upload_bytes,
                            self.bot.cookie_file, self.bot.cobalt_url, self.bot.cobalt_key,
                        )
                        caption = await caption_task
                        from .bot import media_gallery_view
                        chunks = display_chunks(caption)
                        await self.bot._reply(self.source_message, files=[file],
                                              view=media_gallery_view("youtube", [file],
                                                                      chunks[0] if chunks else None,
                                                                      self.link.url))
                        for index, text_chunk in enumerate(chunks[1:], 2):
                            await self.bot._reply(self.source_message,
                                                  view=media_gallery_view("youtube", [], text_chunk,
                                                                          self.link.url, part=index))
                    finally:
                        if not caption_task.done():
                            caption_task.cancel()
                        await asyncio.gather(caption_task, return_exceptions=True)
            if prompt:
                try:
                    await prompt.edit(view=status_view(self.title, f"✅ **{LABELS[choice]}** gönderildi."))
                except discord.HTTPException as exc:
                    LOG.warning("Could not mark YouTube prompt complete: %s", exc)
            LOG.info("YouTube choice %s delivered via %s", choice, provider)
        except (DownloadError, discord.HTTPException, OSError, asyncio.TimeoutError) as exc:
            LOG.warning("YouTube choice %s failed: %s", choice, exc)
            if prompt:
                retry = YoutubeChoiceView(bot=self.bot, source_message=self.source_message,
                                          link=self.link, title=self.title)
                retry.prompt_message = prompt
                try:
                    await prompt.edit(view=retry)
                except discord.HTTPException as edit_exc:
                    LOG.warning("Could not restore YouTube prompt: %s", edit_exc)
            try:
                await interaction.followup.send(f"{LABELS[choice]} alınamadı: {str(exc)[:500]}", ephemeral=True)
            except discord.HTTPException as followup_exc:
                LOG.warning("Could not send YouTube error: %s", followup_exc)

    async def on_timeout(self) -> None:
        if self.prompt_message and not self.selected:
            try:
                await self.prompt_message.edit(view=status_view(self.title, "⌛ Seçim süresi doldu. Bağlantıyı yeniden paylaşabilirsin."))
            except discord.HTTPException:
                pass
