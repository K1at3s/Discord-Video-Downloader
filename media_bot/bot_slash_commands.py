"""Slash command registration for the Discord bot."""

from __future__ import annotations

import logging

import discord

from . import i18n
from .discord_support import InteractionMessage
from .guild_settings import SETTINGS

LOG = logging.getLogger(__name__)


def register_commands(bot) -> None:
    tree = discord.app_commands.CommandTree(bot)

    async def run_existing(interaction: discord.Interaction, method, argument: str = "") -> None:
        locale = i18n.locale_code(interaction)
        if not bot._command_allowed(interaction):
            await interaction.response.send_message(i18n.t("cooldown", locale), ephemeral=True)
            return
        await interaction.response.defer()
        try:
            if argument:
                await method(InteractionMessage(interaction), argument)
            else:
                await method(InteractionMessage(interaction))
        except Exception:
            LOG.exception("Slash komutu başarısız: %s", interaction.command)
            try:
                await interaction.followup.send(i18n.t("command_failed", locale))
            except discord.HTTPException:
                pass

    @tree.command(name="yardim", description="Bot komutlarını ve desteklenen platformları göster / Show bot help")
    async def yardim(interaction: discord.Interaction) -> None:
        locale = i18n.locale_code(interaction)
        await interaction.response.send_message(i18n.help_text(locale), ephemeral=True)

    @tree.command(name="metrics", description="Sağlayıcı ve highlight metriklerini göster")
    async def metrics(interaction: discord.Interaction) -> None:
        await run_existing(interaction, bot._reply_metrics)

    @tree.command(name="stats", description="İşlem istatistikleri (yalnızca sahip)")
    @discord.app_commands.describe(aralik="24s, 7g veya 30g", platform="instagram, youtube, tiktok…")
    async def stats(interaction: discord.Interaction, aralik: str = "", platform: str = "") -> None:
        if not await bot._owner_command(interaction):
            return
        await run_existing(interaction, bot._reply_request_stats, f"{aralik} {platform}".strip())

    @tree.command(name="info", description="Kendi işlem özetin / Your request summary")
    @discord.app_commands.describe(kullanici="Başka bir kullanıcı (yalnızca sahip)", aralik="24s, 7g veya 30g")
    async def info(interaction: discord.Interaction, kullanici: discord.Member | None = None, aralik: str = "") -> None:
        locale = i18n.locale_code(interaction)
        if kullanici and interaction.user.id != bot.owner_id:
            await interaction.response.send_message(i18n.t("info_other_owner_only", locale), ephemeral=True)
            return
        if not bot._command_allowed(interaction):
            await interaction.response.send_message(i18n.t("cooldown", locale), ephemeral=True)
            return
        await interaction.response.defer()
        adapter = InteractionMessage(interaction, mentions=[kullanici] if kullanici else [])
        await bot._reply_user_info(adapter, aralik)

    @tree.command(name="cache", description="Medya ve highlight önbelleği durumu")
    async def cache(interaction: discord.Interaction) -> None:
        await run_existing(interaction, bot._reply_cache)

    @tree.command(name="cachetemizle", description="Önbellekleri temizle (yalnızca sahip)")
    async def cache_clear(interaction: discord.Interaction) -> None:
        if not await bot._owner_command(interaction):
            return
        await run_existing(interaction, bot._reply_cache_clear)

    @tree.command(name="hatalar", description="Son hatalar (yalnızca sahip)")
    @discord.app_commands.describe(aralik="24s, 7g veya 30g")
    async def errors(interaction: discord.Interaction, aralik: str = "") -> None:
        if not await bot._owner_command(interaction):
            return
        await run_existing(interaction, bot._reply_errors, aralik)

    @tree.command(name="bakim", description="Bakım modu (yalnızca sahip)")
    @discord.app_commands.describe(durum="ac veya kapat; boş bırakılırsa durumu gösterir")
    async def maintenance(interaction: discord.Interaction, durum: str = "") -> None:
        if not await bot._owner_command(interaction):
            return
        await run_existing(interaction, bot._reply_maintenance, durum)

    @tree.command(name="ayar", description="Sunucu ayarlarını göster veya değiştir / View or change server settings")
    @discord.app_commands.describe(
        otomatik_thread="Bağlantılar için otomatik thread açılsın mı? / Auto-create thread for media links",
        youtube="YouTube varsayılan kalite / Default YouTube quality",
        yanitla="Bot yanıtları mesaja reply olarak gönderilsin mi? / Reply to triggering message",
        dil="Botun bu sunucudaki dili / Bot language in this server",
    )
    @discord.app_commands.choices(youtube=[
        discord.app_commands.Choice(name="Her zaman sor / Always ask", value="sor"),
        discord.app_commands.Choice(name="MP3 (ses / audio)", value="mp3"),
        discord.app_commands.Choice(name="360p", value="360"),
        discord.app_commands.Choice(name="720p", value="720"),
        discord.app_commands.Choice(name="1080p", value="1080"),
    ], dil=[
        discord.app_commands.Choice(name="Türkçe", value="tr"),
        discord.app_commands.Choice(name="English", value="en"),
        discord.app_commands.Choice(name="Otomatik / Auto", value="auto"),
    ])
    async def ayar(interaction: discord.Interaction,
                   otomatik_thread: bool | None = None,
                   youtube: discord.app_commands.Choice[str] | None = None,
                   yanitla: bool | None = None,
                   dil: discord.app_commands.Choice[str] | None = None) -> None:
        locale = i18n.locale_code(interaction)
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(i18n.t("guild_only", locale), ephemeral=True)
            return
        permissions = getattr(interaction.user, "guild_permissions", None)
        if not (permissions and permissions.manage_guild) and interaction.user.id != bot.owner_id:
            await interaction.response.send_message(i18n.t("manage_guild_required", locale), ephemeral=True)
            return
        changed = (otomatik_thread is not None or youtube is not None
                   or yanitla is not None or dil is not None)
        try:
            if changed:
                settings = SETTINGS.update(
                    guild.id,
                    auto_thread=otomatik_thread,
                    youtube_quality=youtube.value if youtube else None,
                    reply_messages=yanitla,
                    locale=dil.value if dil else None,
                )
            else:
                settings = SETTINGS.get(guild.id)
        except ValueError:
            await interaction.response.send_message(i18n.t("invalid_quality", locale), ephemeral=True)
            return
        shown_locale = settings.locale if settings.locale in {"tr", "en"} else locale
        await interaction.response.send_message(
            i18n.settings_message(settings, shown_locale, updated=changed),
            ephemeral=True,
        )

    bot.tree = tree
