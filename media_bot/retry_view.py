"""Retry button shown after a failed download."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import discord

from .links import PostLink
from .maintenance import MAINTENANCE
from .request_log import REQUESTS

LOG = logging.getLogger(__name__)


class RetryButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Tekrar dene", emoji="🔁", style=discord.ButtonStyle.secondary,
                         custom_id="retry-download")

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, RetryView):
            await view.retry(interaction)


class RetryView(discord.ui.View):
    def __init__(self, *, bot, source_message: discord.Message, link: PostLink):
        super().__init__(timeout=1800)
        self.bot = bot
        self.source_message = source_message
        self.link = link
        self.add_item(RetryButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        owner_id = getattr(self.bot, "owner_id", None)
        if interaction.user.id != self.source_message.author.id and interaction.user.id != owner_id:
            await interaction.response.send_message(
                "Bu bağlantıyı yalnızca paylaşan kişi yeniden deneyebilir.", ephemeral=True
            )
            return False
        if MAINTENANCE.active and interaction.user.id != owner_id:
            await interaction.response.send_message(MAINTENANCE.message, ephemeral=True)
            return False
        return True

    def _set_disabled(self, disabled: bool) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = disabled

    async def retry(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self._set_disabled(True)
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass

        outcome = "failure"
        files: list[Path] = []
        detail = "retry"
        started = time.monotonic()
        try:
            result = await self.bot._handle_link(self.source_message, self.link)
            if isinstance(result, tuple):
                outcome = result[0]
                files = result[1]
            elif result in {"success", "failure", "pending", "skipped"}:
                outcome = result
            else:
                outcome = "success"
        except discord.Forbidden:
            detail = "forbidden"
        except discord.HTTPException as exc:
            detail = f"http:{exc.status}"
        except Exception:
            LOG.exception("Retry failed for %s", self.link.url)
            detail = "exception"
        finally:
            REQUESTS.record(
                guild_id=getattr(self.source_message.guild, "id", None),
                guild_name=getattr(self.source_message.guild, "name", None),
                channel_id=getattr(self.source_message.channel, "id", None),
                user_id=getattr(self.source_message.author, "id", None),
                user_name=str(getattr(self.source_message.author, "name", self.source_message.author)),
                platform=self.link.platform,
                url=self.link.url,
                outcome=outcome,
                detail=detail,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                file_count=len(files),
                bytes=sum(p.stat().st_size for p in files if p.is_file()),
            )

        if outcome == "pending":
            message = "🔁 Yeniden deneme başlatıldı; devam etmek için yeni mesajdaki seçenekleri kullanın."
        elif outcome == "success":
            message = "✅ Yeniden deneme başarılı."
        else:
            message = "❌ Yeniden deneme de başarısız oldu."
            self._set_disabled(False)
            try:
                await interaction.message.edit(view=self)
            except discord.HTTPException:
                pass
        try:
            await interaction.followup.send(message, ephemeral=True)
        except discord.HTTPException:
            pass
