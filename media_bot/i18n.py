"""Lightweight user-locale helpers for Discord interactions."""

from __future__ import annotations

from typing import Any

import discord

from .guild_settings import GuildSettings


def locale_code(interaction: discord.Interaction | None) -> str:
    """Return 'tr' or 'en' based on the user's (then guild's) Discord locale."""
    if interaction is None:
        return "tr"
    for value in (getattr(interaction, "locale", None), getattr(interaction, "guild_locale", None)):
        text = str(getattr(value, "value", value) or "")
        if text.lower().startswith("tr"):
            return "tr"
        if text.lower().startswith("en"):
            return "en"
    return "tr"


_STRINGS: dict[str, dict[str, str]] = {
    "guild_only": {
        "tr": "Bu komut yalnızca sunucularda kullanılabilir.",
        "en": "This command can only be used in servers.",
    },
    "manage_guild_required": {
        "tr": "Bu komut için **Sunucuyu Yönet** izni gerekir.",
        "en": "You need the **Manage Server** permission to use this command.",
    },
    "owner_only": {
        "tr": "Bu komut yalnızca bot sahibine açık.",
        "en": "This command is only available to the bot owner.",
    },
    "cooldown": {
        "tr": "Çok hızlı; birkaç saniye sonra tekrar dene.",
        "en": "Slow down; try again in a few seconds.",
    },
    "command_failed": {
        "tr": "Komut çalıştırılırken bir hata oluştu.",
        "en": "Something went wrong while running this command.",
    },
    "settings_title": {
        "tr": "⚙️ Sunucu Ayarları",
        "en": "⚙️ Server Settings",
    },
    "settings_current": {
        "tr": "Mevcut ayarlar aşağıda. Değişiklik yapmak için seçenekleri kullanabilirsin.",
        "en": "Current settings are shown below. Use the options to make changes.",
    },
    "settings_updated": {
        "tr": "Ayarlar güncellendi.",
        "en": "Settings updated.",
    },
    "auto_thread": {
        "tr": "Otomatik thread",
        "en": "Auto thread",
    },
    "auto_thread_desc": {
        "tr": "Medya bağlantısı içeren mesajlar için otomatik thread açar.",
        "en": "Automatically opens a thread for messages containing media links.",
    },
    "youtube_default": {
        "tr": "YouTube varsayılanı",
        "en": "YouTube default",
    },
    "youtube_default_desc": {
        "tr": "YouTube bağlantılarında kalite sorulsun mu yoksa varsayılan uygulansın mı.",
        "en": "Whether to ask for quality or use a default for YouTube links.",
    },
    "on": {"tr": "Açık", "en": "On"},
    "off": {"tr": "Kapalı", "en": "Off"},
    "settings_hint": {
        "tr": "İpucu: `/ayar` içinde `otomatik_thread` veya `youtube` seçeneğini vererek ayarı değiştirebilirsin.",
        "en": "Tip: provide `otomatik_thread` or `youtube` in `/ayar` to change a setting.",
    },
    "invalid_quality": {
        "tr": "Geçersiz YouTube kalite seçimi.",
        "en": "Invalid YouTube quality selection.",
    },
    "info_other_owner_only": {
        "tr": "Başka bir kullanıcının istatistiğini yalnızca bot sahibi görebilir.",
        "en": "Only the bot owner can view another user's stats.",
    },
}

_QUALITY_LABELS = {
    "tr": {"sor": "Her zaman sor", "mp3": "MP3 (ses)", "360": "360p", "720": "720p", "1080": "1080p"},
    "en": {"sor": "Always ask", "mp3": "MP3 (audio)", "360": "360p", "720": "720p", "1080": "1080p"},
}


def t(key: str, locale: str, **kwargs: Any) -> str:
    text = _STRINGS.get(key, {}).get(locale) or _STRINGS.get(key, {}).get("tr") or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text


def quality_label(value: str, locale: str) -> str:
    labels = _QUALITY_LABELS.get(locale) or _QUALITY_LABELS["tr"]
    return labels.get(value, value)


def describe_settings(settings: GuildSettings, locale: str) -> str:
    thread = t("on", locale) if settings.auto_thread else t("off", locale)
    quality = quality_label(settings.youtube_quality, locale)
    return (
        f"**{t('auto_thread', locale)}:** {thread}\n"
        f"-# {t('auto_thread_desc', locale)}\n\n"
        f"**{t('youtube_default', locale)}:** {quality}\n"
        f"-# {t('youtube_default_desc', locale)}"
    )


def settings_message(settings: GuildSettings, locale: str, *, updated: bool) -> str:
    heading = t("settings_updated" if updated else "settings_current", locale)
    return (
        f"{t('settings_title', locale)}\n"
        f"{heading}\n\n"
        f"{describe_settings(settings, locale)}\n\n"
        f"-# {t('settings_hint', locale)}"
    )


def help_text(locale: str) -> str:
    if locale == "en":
        return (
            "**Discord Video Bot**\n"
            "Send a link: Instagram, Threads, TikTok, X, Facebook, Reddit, YouTube, Spotify, Bluesky, Douyin, Weibo, Streamable.\n\n"
            "**Commands**\n"
            "`/ayar` · server settings: auto thread & default YouTube quality (Manage Server)\n"
            "`/info [user]` · your request summary\n"
            "`/cache` · media cache status\n"
            "`/metrics` · provider metrics\n"
            "`/stats [24h|7d|30d] [platform]` · request panel (owner)\n"
            "`/hatalar [range]` · recent errors (owner)\n"
            "`/bakim [on|off]` · maintenance mode (owner)\n\n"
            "Legacy `!` commands also work (e.g. `!ayar`, `!ayar thread ac`)."
        )
    return (
        "**Discord Video Bot**\n"
        "Bağlantı gönder: Instagram, Threads, TikTok, X, Facebook, Reddit, YouTube, Spotify, Bluesky, Douyin, Weibo, Streamable.\n\n"
        "**Komutlar**\n"
        "`/ayar` · sunucu ayarları: otomatik thread ve YouTube varsayılan kalitesi (Sunucuyu Yönet)\n"
        "`/info [kullanıcı]` · işlem özetin\n"
        "`/cache` · önbellek durumu\n"
        "`/metrics` · sağlayıcı metrikleri\n"
        "`/stats [24s|7g|30g] [platform]` · işlem paneli (sahip)\n"
        "`/hatalar [aralık]` · son hatalar (sahip)\n"
        "`/bakim [ac|kapat]` · bakım modu (sahip)\n\n"
        "Eski `!` komutları da çalışır (`!ayar`, `!ayar thread ac`)."
    )
