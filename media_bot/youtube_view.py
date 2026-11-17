"""Discord Components V2 YouTube format prompt."""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from pathlib import Path

import discord

from .captions import display_chunks, fetch_caption
from .download import DownloadError
from .links import PostLink
from .request_log import REQUESTS
from .youtube import download_youtube, fetch_youtube_meta, song_filename, video_height

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


async def deliver_youtube(bot, source_message, link: PostLink, choice: str,
                          upload_limit: int, *, request_id: int | None = None,
                          started: float | None = None) -> tuple[bool, list[Path], str]:
    """Download and send one YouTube choice; used by the prompt and by guild defaults."""
    started = time.monotonic() if started is None else started
    delivered_label = LABELS[choice]
    caption_task = asyncio.create_task(fetch_caption(link, bot.cookie_file))
    meta_task = asyncio.create_task(fetch_youtube_meta(link.url)) if choice == "mp3" else None
    files: list[Path] = []
    try:
        with tempfile.TemporaryDirectory(prefix="discord-youtube-") as temporary:
            file, provider = await download_youtube(
                link, Path(temporary), choice, upload_limit,
                bot.cookie_file, bot.cobalt_url, bot.cobalt_key,
            )
            if choice == "mp3":
                title, author = await meta_task if meta_task else (None, None)
                file = file.rename(file.with_name(
                    song_filename(author, title, fallback=file.stem)))
                delivered_label = f"MP3 · {file.stem}"
                from .bot import source_button
                # Delivered as a normal attachment, not inside a media card.
                await bot._reply(source_message, files=[file],
                                 view=source_button(link.url))
            else:
                height = await video_height(file)
                if height:
                    delivered_label = f"{height}p video (en fazla {choice}p seçildi)"
                    file = file.rename(file.with_name(f"youtube_{height}p.mp4"))
                    if height < int(choice):
                        LOG.info("YouTube %sp requested, %sp delivered via %s within upload limit",
                                 choice, height, provider)
                caption = await caption_task
                from .bot import media_gallery_view
                chunks = display_chunks(caption)
                await bot._reply(source_message, files=[file],
                                 view=media_gallery_view("youtube", [file],
                                                         chunks[0] if chunks else None,
                                                         link.url))
                for index, text_chunk in enumerate(chunks[1:], 2):
                    await bot._reply(source_message,
                                     view=media_gallery_view("youtube", [], text_chunk,
                                                             link.url, part=index))
            files = [file]
            REQUESTS.update(
                request_id,
                outcome="success",
                detail=f"youtube:{choice}:{provider}",
                elapsed_ms=int((time.monotonic() - started) * 1000),
                file_count=1,
                bytes=file.stat().st_size if file.is_file() else 0,
            )
            LOG.info("YouTube choice %s delivered via %s", choice, provider)
            return True, files, delivered_label
    except (TimeoutError, DownloadError, discord.HTTPException, OSError) as exc:
        LOG.warning("YouTube choice %s failed: %s", choice, exc)
        REQUESTS.update(
            request_id,
            outcome="failure",
            detail=f"youtube:{choice}:{str(exc)[:180]}",
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        return False, [], str(exc)
    finally:
        for task in (caption_task, meta_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (caption_task, meta_task) if task is not None),
            return_exceptions=True,
        )


class FormatButton(discord.ui.Button):
    def __init__(self, choice: str):
        super().__init__(label=LABELS[choice], style=discord.ButtonStyle.secondary, custom_id=f"youtube:{choice}")
        self.choice = choice

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, YoutubeChoiceView):
            await view.choose(interaction, self.choice)


class YoutubeChoiceView(discord.ui.LayoutView):
    def __init__(self, *, bot, source_message: discord.Message, link: PostLink, title: str,
                 request_id: int | None = None, upload_limit: int | None = None):
        super().__init__(timeout=300)
        self.bot = bot
        self.source_message = source_message
        self.link = link
        self.title = title
        self.request_id = request_id
        self.upload_limit = upload_limit or getattr(bot, "upload_bytes", 0)
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

    async def choose(self, interaction: discord.Interaction, choice: str) -> bool:
        if self.selected:
            await interaction.response.send_message("Bir indirme zaten başlatıldı.", ephemeral=True)
            return False
        self.selected = True
        await interaction.response.defer()
        self.stop()
        started = time.monotonic()
        prompt = interaction.message
        if prompt:
            try:
                await prompt.edit(view=status_view(self.title, f"⏳ **{LABELS[choice]}** hazırlanıyor…"))
            except discord.HTTPException as exc:
                LOG.warning("Could not update YouTube prompt: %s", exc)

        async def on_wait() -> None:
            if prompt:
                try:
                    await prompt.edit(view=status_view(self.title, f"⏳ **{LABELS[choice]}** sırada…"))
                except discord.HTTPException as exc:
                    LOG.warning("Could not mark YouTube choice queued: %s", exc)

        async with self.bot.job_slots.slot(self.source_message.author.id, on_wait):
            if prompt:
                try:
                    await prompt.edit(view=status_view(self.title, f"⏳ **{LABELS[choice]}** hazırlanıyor…"))
                except discord.HTTPException as exc:
                    LOG.warning("Could not update YouTube prompt: %s", exc)
            ok, _files, detail = await deliver_youtube(
                self.bot, self.source_message, self.link, choice, self.upload_limit,
                request_id=self.request_id, started=started,
            )
        if ok:
            if prompt:
                try:
                    await prompt.edit(view=status_view(self.title, f"✅ **{detail}** gönderildi."))
                except discord.HTTPException as exc:
                    LOG.warning("Could not mark YouTube prompt complete: %s", exc)
            return True
        if prompt:
            retry = YoutubeChoiceView(bot=self.bot, source_message=self.source_message,
                                      link=self.link, title=self.title,
                                      request_id=self.request_id)
            retry.prompt_message = prompt
            try:
                await prompt.edit(view=retry)
            except discord.HTTPException as edit_exc:
                LOG.warning("Could not restore YouTube prompt: %s", edit_exc)
        try:
            await interaction.followup.send(f"{LABELS[choice]} alınamadı: {detail[:500]}", ephemeral=True)
        except discord.HTTPException as followup_exc:
            LOG.warning("Could not send YouTube error: %s", followup_exc)
        return False

    async def on_timeout(self) -> None:
        if self.prompt_message and not self.selected:
            try:
                await self.prompt_message.edit(view=status_view(self.title, "⌛ Seçim süresi doldu. Bağlantıyı yeniden paylaşabilirsin."))
            except discord.HTTPException:
                pass
