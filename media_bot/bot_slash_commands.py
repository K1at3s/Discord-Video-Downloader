"""Slash command registration for the Discord bot."""

from __future__ import annotations

import logging

import discord

from . import i18n
from .discord_support import InteractionMessage
from .guild_settings import SETTINGS

LOG = logging.getLogger(__name__)

loc = discord.app_commands.locale_str

# Keys are the canonical Turkish strings; values are shown when Discord asks
# for an English locale. Command names must stay lowercase and hyphenated.
_COMMAND_TRANSLATIONS = {
    # Command names
    "yardim": "help",
    "cachetemizle": "clear-cache",
    "hatalar": "errors",
    "bakim": "maintenance",
    "ayar": "settings",
    "logsil": "log-delete",
    "logtemizle": "log-clear",
    # Command descriptions
    "Bot komutlarını ve desteklenen platformları göster": "Show bot commands and supported platforms",
    "Sağlayıcı ve highlight metriklerini göster": "Show provider and highlight metrics",
    "İşlem istatistikleri (yalnızca sahip)": "Request statistics (owner only)",
    "Kendi işlem özetin": "Your request summary",
    "Medya ve highlight önbelleği durumu": "Media, card and highlight cache status",
    "Önbellekleri temizle (yalnızca sahip)": "Clear caches (owner only)",
    "Son hatalar (yalnızca sahip)": "Recent errors (owner only)",
    "Bakım modu (yalnızca sahip)": "Maintenance mode (owner only)",
    "Sunucu ayarlarını göster veya değiştir": "View or change server settings",
    "Platform bazlı işlem detayı (yalnızca sahip)": "Per-platform request details (owner only)",
    "Kendi işlem kayıtlarını sil": "Delete your request history",
    "Tüm işlem kayıtlarını sil (yalnızca sahip)": "Delete all request history (owner only)",
    # Option descriptions
    "24s, 7g veya 30g": "24h, 7d or 30d",
    "Başka bir kullanıcı (yalnızca sahip)": "Another user (owner only)",
    "ac veya kapat; boş bırakılırsa durumu gösterir": "on or off; leave empty to show the status",
    "Bağlantılar için otomatik thread açılsın mı?": "Auto-create a thread for media links",
    "YouTube varsayılan kalite": "Default YouTube quality",
    "Bot yanıtları mesaja reply olarak gönderilsin mi?": "Send bot replies as a reply to the message",
    "Botun bu sunucudaki dili": "Bot language in this server",
    "Kullanıcı (boş = kendin; başkası için sahip)": "User (empty = yourself; owner for others)",
    "Silmeyi onayla": "Confirm deletion",
    "Açılıp kapatılacak platform (örn. instagram)": "Platform to enable/disable (e.g. instagram)",
    "Seçili platform bu sunucuda açık mı?": "Whether the selected platform is enabled in this server",
    "başarılı, başarısız, bekleyen, atlanan": "success, failure, pending, skipped",
    # Choice names
    "Her zaman sor": "Always ask",
    "MP3 (ses)": "MP3 (audio)",
    "Otomatik": "Auto",
    "Türkçe": "Turkish",
}


class BotTranslator(discord.app_commands.Translator):
    """Provide English slash command names/descriptions at sync time."""

    async def translate(self, string, locale, context):
        if not str(getattr(locale, "value", locale)).lower().startswith("en"):
            return None
        return _COMMAND_TRANSLATIONS.get(string.message)


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

    @tree.command(name=loc("yardim"), description=loc("Bot komutlarını ve desteklenen platformları göster"))
    async def yardim(interaction: discord.Interaction) -> None:
        locale = i18n.locale_code(interaction)
        await interaction.response.send_message(i18n.help_text(locale), ephemeral=True)

    @tree.command(name=loc("metrics"), description=loc("Sağlayıcı ve highlight metriklerini göster"))
    async def metrics(interaction: discord.Interaction) -> None:
        await run_existing(interaction, bot._reply_metrics)

    @tree.command(name=loc("stats"), description=loc("İşlem istatistikleri (yalnızca sahip)"))
    @discord.app_commands.describe(
        aralik=loc("24s, 7g veya 30g"),
        platform=loc("instagram, youtube, tiktok…"),
        sonuc=loc("başarılı, başarısız, bekleyen, atlanan"),
    )
    async def stats(interaction: discord.Interaction, aralik: str = "", platform: str = "", sonuc: str = "") -> None:
        if not await bot._owner_command(interaction):
            return
        filt = f"{aralik} {platform}".strip()
        if sonuc:
            filt = f"{filt} sonuc:{sonuc}".strip()
        await run_existing(interaction, bot._reply_request_stats, filt)

    @tree.command(name=loc("info"), description=loc("Kendi işlem özetin"))
    @discord.app_commands.describe(
        kullanici=loc("Başka bir kullanıcı (yalnızca sahip)"),
        aralik=loc("24s, 7g veya 30g"),
    )
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

    @tree.command(name=loc("cache"), description=loc("Medya ve highlight önbelleği durumu"))
    async def cache(interaction: discord.Interaction) -> None:
        await run_existing(interaction, bot._reply_cache)

    @tree.command(name=loc("cachetemizle"), description=loc("Önbellekleri temizle (yalnızca sahip)"))
    async def cache_clear(interaction: discord.Interaction) -> None:
        if not await bot._owner_command(interaction):
            return
        await run_existing(interaction, bot._reply_cache_clear)

    @tree.command(name=loc("hatalar"), description=loc("Son hatalar (yalnızca sahip)"))
    @discord.app_commands.describe(aralik=loc("24s, 7g veya 30g"))
    async def errors(interaction: discord.Interaction, aralik: str = "") -> None:
        if not await bot._owner_command(interaction):
            return
        await run_existing(interaction, bot._reply_errors, aralik)

    @tree.command(name=loc("bakim"), description=loc("Bakım modu (yalnızca sahip)"))
    @discord.app_commands.describe(durum=loc("ac veya kapat; boş bırakılırsa durumu gösterir"))
    async def maintenance(interaction: discord.Interaction, durum: str = "") -> None:
        if not await bot._owner_command(interaction):
            return
        await run_existing(interaction, bot._reply_maintenance, durum)

    @tree.command(name=loc("platform"), description=loc("Platform bazlı işlem detayı (yalnızca sahip)"))
    @discord.app_commands.describe(
        platform=loc("instagram, youtube, tiktok…"),
        aralik=loc("24s, 7g veya 30g"),
    )
    async def platform(interaction: discord.Interaction, platform: str, aralik: str = "") -> None:
        if not await bot._owner_command(interaction):
            return
        await run_existing(interaction, bot._reply_request_stats, f"{aralik} {platform}".strip())

    @tree.command(name=loc("logsil"), description=loc("Kendi işlem kayıtlarını sil"))
    @discord.app_commands.describe(
        kullanici=loc("Kullanıcı (boş = kendin; başkası için sahip)"),
        onay=loc("Silmeyi onayla"),
    )
    async def log_delete(interaction: discord.Interaction, kullanici: discord.Member | None = None,
                         onay: bool = False) -> None:
        locale = i18n.locale_code(interaction)
        if not bot._command_allowed(interaction):
            await interaction.response.send_message(i18n.t("cooldown", locale), ephemeral=True)
            return
        if kullanici and interaction.user.id != bot.owner_id:
            await interaction.response.send_message(i18n.t("owner_only", locale), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        argument = " ".join(part for part in (
            f"<@{kullanici.id}>" if kullanici else "",
            "onay" if onay else "",
        ) if part)
        adapter = InteractionMessage(interaction, mentions=[kullanici] if kullanici else [])
        await bot._reply_log_delete_user(adapter, argument)

    @tree.command(name=loc("logtemizle"), description=loc("Tüm işlem kayıtlarını sil (yalnızca sahip)"))
    @discord.app_commands.describe(onay=loc("Silmeyi onayla"))
    async def log_clear(interaction: discord.Interaction, onay: bool = False) -> None:
        if not await bot._owner_command(interaction):
            return
        await run_existing(interaction, bot._reply_log_clear, "onay" if onay else "")

    @tree.command(name=loc("ayar"), description=loc("Sunucu ayarlarını göster veya değiştir"))
    @discord.app_commands.describe(
        otomatik_thread=loc("Bağlantılar için otomatik thread açılsın mı?"),
        youtube=loc("YouTube varsayılan kalite"),
        yanitla=loc("Bot yanıtları mesaja reply olarak gönderilsin mi?"),
        dil=loc("Botun bu sunucudaki dili"),
        platform=loc("Açılıp kapatılacak platform (örn. instagram)"),
        platform_acik=loc("Seçili platform bu sunucuda açık mı?"),
    )
    @discord.app_commands.choices(youtube=[
        discord.app_commands.Choice(name=loc("Her zaman sor"), value="sor"),
        discord.app_commands.Choice(name=loc("MP3 (ses)"), value="mp3"),
        discord.app_commands.Choice(name="360p", value="360"),
        discord.app_commands.Choice(name="720p", value="720"),
        discord.app_commands.Choice(name="1080p", value="1080"),
    ], dil=[
        discord.app_commands.Choice(name=loc("Türkçe"), value="tr"),
        discord.app_commands.Choice(name="English", value="en"),
        discord.app_commands.Choice(name=loc("Otomatik"), value="auto"),
    ])
    async def ayar(interaction: discord.Interaction,
                   otomatik_thread: bool | None = None,
                   youtube: discord.app_commands.Choice[str] | None = None,
                   yanitla: bool | None = None,
                   dil: discord.app_commands.Choice[str] | None = None,
                   platform: str | None = None,
                   platform_acik: bool | None = None) -> None:
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
                   or yanitla is not None or dil is not None
                   or (platform is not None and platform_acik is not None))
        try:
            disabled_platforms = None
            if platform is not None and platform_acik is not None:
                current = SETTINGS.get(guild.id)
                disabled = set(current.disabled_platforms)
                key = platform.strip().lower()
                if platform_acik:
                    disabled.discard(key)
                else:
                    disabled.add(key)
                disabled_platforms = tuple(sorted(disabled))
            if changed:
                settings = SETTINGS.update(
                    guild.id,
                    auto_thread=otomatik_thread,
                    youtube_quality=youtube.value if youtube else None,
                    reply_messages=yanitla,
                    locale=dil.value if dil else None,
                    disabled_platforms=disabled_platforms,
                )
            else:
                settings = SETTINGS.get(guild.id)
        except ValueError as exc:
            await interaction.response.send_message(str(exc)[:300], ephemeral=True)
            return
        shown_locale = settings.locale if settings.locale in {"tr", "en"} else locale
        await interaction.response.send_message(
            i18n.settings_message(settings, shown_locale, updated=changed),
            ephemeral=True,
        )

    bot.tree = tree
