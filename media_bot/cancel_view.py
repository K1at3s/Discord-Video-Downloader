"""Cancel button attached to a long-running download's status message."""

from __future__ import annotations

import asyncio

import discord

from . import i18n


class CancelButton(discord.ui.Button):
    def __init__(self, locale: str) -> None:
        super().__init__(
            label=i18n.t("cancel_button", locale),
            emoji="⏹️",
            style=discord.ButtonStyle.secondary,
            custom_id="cancel-download",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, CancelView):
            await view.cancel(interaction)


class CancelView(discord.ui.View):
    def __init__(self, *, task: asyncio.Task | None, owner_id: int | None,
                 bot_owner_id: int | None = None, locale: str = "tr"):
        super().__init__(timeout=1800)
        self.task = task
        self.owner_id = owner_id
        self.bot_owner_id = bot_owner_id
        self.locale = locale
        self.cancelled = False
        self.add_item(CancelButton(locale))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        allowed = {self.owner_id, self.bot_owner_id}
        if interaction.user.id not in allowed:
            await interaction.response.send_message(
                i18n.t("cancel_not_owner", self.locale), ephemeral=True
            )
            return False
        return True

    def _disable(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

    async def cancel(self, interaction: discord.Interaction) -> None:
        if self.task is None or self.task.done():
            await interaction.response.send_message(
                i18n.t("cancel_too_late", self.locale), ephemeral=True
            )
            return
        await interaction.response.defer()
        self.cancelled = True
        self._disable()
        try:
            await interaction.message.edit(
                content=i18n.t("cancelled", self.locale), view=self
            )
        except (discord.HTTPException, AttributeError, TypeError):
            pass
        self.task.cancel()
