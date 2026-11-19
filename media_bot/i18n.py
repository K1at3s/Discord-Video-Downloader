"""Lightweight user-locale helpers for Discord interactions."""

from __future__ import annotations

from typing import Any

import discord

from .guild_settings import SETTINGS, GuildSettings


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


def locale_for_message(message: Any) -> str:
    """Guild setting wins; otherwise use Turkish (the bot's default language)."""
    guild = getattr(message, "guild", None)
    if guild is not None:
        try:
            configured = SETTINGS.get(getattr(guild, "id", 0)).locale
        except Exception:
            configured = "tr"
        if configured in {"tr", "en"}:
            return configured
    return "tr"


def wait_text(seconds: int | float, locale: str) -> str:
    minutes = max(1, int((float(seconds) + 59) // 60))
    if minutes < 60:
        return f"{minutes} min" if locale == "en" else f"{minutes} dakika"
    hours, remainder = divmod(minutes, 60)
    if locale == "en":
        return f"{hours} hr {remainder} min" if remainder else f"{hours} hr"
    return f"{hours} saat {remainder} dakika" if remainder else f"{hours} saat"


def quota_message(decision: Any, locale: str) -> str:
    key = "quota_daily" if getattr(decision, "kind", "") == "daily" else "quota_hourly"
    return t(key, locale, limit=getattr(decision, "limit", 0),
             wait=wait_text(getattr(decision, "retry_after", 0), locale))


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
    "reply_messages": {
        "tr": "Mesaja yanıt (reply)",
        "en": "Reply to message",
    },
    "reply_messages_desc": {
        "tr": "Bot yanıtlarını kullanıcının mesajına Discord reply referansı ile gönderir.",
        "en": "Send bot responses as Discord replies to the user's message.",
    },
    "on": {"tr": "açık", "en": "on"},
    "off": {"tr": "kapalı", "en": "off"},
    "settings_hint": {
        "tr": "İpucu: `/ayar` içinde `otomatik_thread`, `youtube` veya `yanitla` seçeneğini vererek ayarı değiştirebilirsin.",
        "en": "Tip: provide `otomatik_thread`, `youtube`, or `yanitla` in `/ayar` to change a setting.",
    },
    "invalid_quality": {
        "tr": "Geçersiz YouTube kalite seçimi.",
        "en": "Invalid YouTube quality selection.",
    },
    "info_other_owner_only": {
        "tr": "Başka bir kullanıcının istatistiğini yalnızca bot sahibi görebilir.",
        "en": "Only the bot owner can view another user's stats.",
    },
    "language": {"tr": "Dil", "en": "Language"},
    "locale_auto": {"tr": "Otomatik (Türkçe)", "en": "Auto (Turkish)"},
    "locale_tr": {"tr": "Türkçe", "en": "Turkish"},
    "locale_en": {"tr": "İngilizce", "en": "English"},
    "settings_usage": {
        "tr": "Kullanım: `!ayar` · `!ayar thread ac|kapat` · `!ayar yanit ac|kapat` · "
              "`!ayar youtube sor|mp3|360|720|1080` · `!ayar dil tr|en|auto` · `!ayar sifirla`",
        "en": "Usage: `!ayar` · `!ayar thread on|off` · `!ayar reply on|off` · "
              "`!ayar youtube ask|mp3|360|720|1080` · `!ayar lang tr|en|auto` · `!ayar reset`",
    },
    "queued": {
        "tr": "⏳ Sıradasın; bağlantın işlenecek.",
        "en": "⏳ You are in the queue; your link will be processed.",
    },
    "preparing": {
        "tr": "⏳ Bağlantı hazırlanıyor…",
        "en": "⏳ Preparing your link…",
    },
    "resolving": {
        "tr": "⏳ Bağlantı çözümleniyor…",
        "en": "⏳ Resolving the link…",
    },
    "parallel_processing": {
        "tr": "⏳ {count} bağlantı paralel işleniyor…",
        "en": "⏳ Processing {count} links in parallel…",
    },
    "downloading": {
        "tr": "⏳ Medya indiriliyor…",
        "en": "⏳ Downloading media…",
    },
    "uploading": {
        "tr": "📤 Gönderiliyor…",
        "en": "📤 Uploading…",
    },
    "files_sent": {
        "tr": "✅ {count} dosya gönderildi.",
        "en": "✅ Sent {count} file(s).",
    },
    "cancelled": {
        "tr": "⏹️ İptal edildi.",
        "en": "⏹️ Cancelled.",
    },
    "cancel_button": {"tr": "İptal", "en": "Cancel"},
    "cancel_not_owner": {
        "tr": "Bu işlemi yalnızca bağlantıyı paylaşan kişi iptal edebilir.",
        "en": "Only the person who shared the link can cancel this job.",
    },
    "cancel_too_late": {
        "tr": "İşlem zaten bitmiş görünüyor.",
        "en": "The job has already finished.",
    },
    "retry_hint": {
        "tr": "↩️ İşlem başarısız oldu. Tekrar denemek için:",
        "en": "↩️ The job failed. To try again:",
    },
    "retry_not_owner": {
        "tr": "Bu bağlantıyı yalnızca paylaşan kişi yeniden deneyebilir.",
        "en": "Only the person who shared this link can retry it.",
    },
    "progress_downloading": {
        "tr": "📥 İndiriliyor… {files} dosya · {size}",
        "en": "📥 Downloading… {files} file(s) · {size}",
    },
    "progress_estimate": {
        "tr": " / ≈ {size}",
        "en": " / ≈ {size}",
    },
    "progress_uploading": {
        "tr": "📤 Yükleniyor… {files} dosya · {size}",
        "en": "📤 Uploading… {files} file(s) · {size}",
    },
    "progress_uploading_part": {
        "tr": "📤 {part}/{total} grup gönderiliyor…",
        "en": "📤 Sending group {part}/{total}…",
    },
    "progress_uploading_group": {
        "tr": "📤 {count}. grup gönderiliyor ({files} dosya)…",
        "en": "📤 Sending group {count} ({files} files)…",
    },
    "youtube_title": {"tr": "YouTube videosu", "en": "YouTube video"},
    "youtube_preparing": {
        "tr": "⏳ **{quality}** hazırlanıyor…",
        "en": "⏳ Preparing **{quality}**…",
    },
    "youtube_sent": {
        "tr": "✅ **{detail}** gönderildi.",
        "en": "✅ **{detail}** sent.",
    },
    "quota_hourly": {
        "tr": "⏳ Saatlik bağlantı hakkın doldu ({limit}). Yaklaşık {wait} sonra tekrar dene.",
        "en": "⏳ Your hourly link limit is used up ({limit}). Try again in about {wait}.",
    },
    "quota_daily": {
        "tr": "⏳ Günlük bağlantı hakkın doldu ({limit}). Yaklaşık {wait} sonra tekrar dene.",
        "en": "⏳ Your daily link limit is used up ({limit}). Try again in about {wait}.",
    },
    "error_private": {
        "tr": "🔒 Bu gönderi herkese açık değil (giriş/özel hesap).",
        "en": "🔒 This post is not public (login/private account).",
    },
    "error_bot_check": {
        "tr": "🤖 Kaynak site bot doğrulaması istedi; çerez dosyasını yenilemek gerekebilir.",
        "en": "🤖 The source asked for bot verification; refreshing the cookie file may help.",
    },
    "error_rate_limit": {
        "tr": "⏳ Kaynak site geçici olarak sınırlıyor; biraz sonra tekrar dene.",
        "en": "⏳ The source is rate-limiting right now; try again shortly.",
    },
    "error_timeout": {
        "tr": "⌛ İşlem zaman aşımına uğradı; tekrar denemek işe yarayabilir.",
        "en": "⌛ The request timed out; retrying may help.",
    },
    "error_too_large": {
        "tr": "📦 Medya Discord yükleme sınırını aşıyor.",
        "en": "📦 The media exceeds the Discord upload limit.",
    },
    "error_not_found": {
        "tr": "🔍 Gönderi bulunamadı, kaldırılmış veya erişime kapalı.",
        "en": "🔍 The post was not found, removed, or is unavailable.",
    },
    "error_no_media": {
        "tr": "🎞️ {platform}: bu gönderide indirilecek medya bulunamadı.",
        "en": "🎞️ {platform}: no downloadable media was found in this post.",
    },
    "error_unsupported": {
        "tr": "🚫 Bu bağlantı türü desteklenmiyor.",
        "en": "🚫 This link type is not supported.",
    },
    "error_forbidden": {
        "tr": "🚫 Bu kanalda mesaj veya dosya gönderme iznim yok.",
        "en": "🚫 I do not have permission to send messages or files in this channel.",
    },
    "error_unsafe_url": {
        "tr": "🛡️ Güvenlik nedeniyle bu bağlantı işlenmedi.",
        "en": "🛡️ This link was not processed for security reasons.",
    },
    "error_generic": {
        "tr": "⚠️ Bu bağlantı işlenirken bir sorun oluştu. Tekrar deneyebilirsin.",
        "en": "⚠️ Something went wrong while processing this link. You can try again.",
    },
    "discord_413": {
        "tr": "📦 Dosya Discord yükleme sınırını aşıyor (sunucu/kanal limiti).",
        "en": "📦 The file exceeds the Discord upload limit (server/channel limit).",
    },
    "discord_upload_failed": {
        "tr": "📤 Dosya Discord'a yüklenemedi. Kanal izinlerini ve yükleme sınırını kontrol edin.",
        "en": "📤 The file could not be uploaded to Discord. Check channel permissions and the upload limit.",
    },
    "unexpected_error": {
        "tr": "⚠️ Bu bağlantı işlenirken beklenmeyen bir hata oluştu.",
        "en": "⚠️ An unexpected error occurred while processing this link.",
    },
    "bluesky_rate_limit": {
        "tr": "Bluesky hız sınırına ulaştı; kısa süre sonra tekrar dene.",
        "en": "Bluesky rate limit reached; try again shortly.",
    },
    "bluesky_not_found": {
        "tr": "Bluesky gönderisi bulunamadı veya erişim engellendi.",
        "en": "Bluesky post was not found or access was denied.",
    },
    "bluesky_unavailable": {
        "tr": "Bluesky gönderisi şu anda alınamadı.",
        "en": "Bluesky post is currently unavailable.",
    },
    "x_post": {"tr": "**X gönderisi**", "en": "**X post**"},
    "x_media": {"tr": "**X medyası**", "en": "**X media**"},
    "x_media_more": {"tr": "**X medyası** · devam", "en": "**X media** · continued"},
    "reddit_no_info": {
        "tr": "Reddit gönderi bilgisi alınamadı.",
        "en": "Reddit post details could not be retrieved.",
    },
    "threads_unresolved": {
        "tr": "Threads paylaşım bağlantısı çözülemedi; Threads gönderiyi bot için giriş sayfasına yönlendirmiş olabilir.",
        "en": "The Threads share link could not be resolved; Threads may have redirected the bot to a login page.",
    },
    "media_not_downloaded": {
        "tr": "Medya indirilemedi.",
        "en": "The media could not be downloaded.",
    },
    "adult_channel_only": {
        "tr": "+18 içerik yalnızca NSFW olarak işaretlenmiş kanallarda kullanılabilir.",
        "en": "+18 content can only be used in channels marked as NSFW.",
    },
    "adult_daily_limit": {
        "tr": "Günlük +18 indirme hakkını kullandın. Yeni hak Türkiye saatiyle gece yarısında açılır.",
        "en": "You used today's +18 download allowance. It resets at midnight Turkey time.",
    },
    "adult_unverified": {
        "tr": "Reddit gönderisinin +18 durumu doğrulanamadı; güvenlik nedeniyle işlenmedi.",
        "en": "The Reddit post's +18 status could not be verified; it was not processed for safety.",
    },
    "spotify_failed": {
        "tr": "Spotify parçası indirilemedi: {error}",
        "en": "The Spotify track could not be downloaded: {error}",
    },
    "retry_started": {
        "tr": "🔁 Yeniden deneme başlatıldı; devam etmek için yeni mesajdaki seçenekleri kullanın.",
        "en": "🔁 Retry started; use the options in the new message to continue.",
    },
    "retry_success": {
        "tr": "✅ Yeniden deneme başarılı.",
        "en": "✅ Retry succeeded.",
    },
    "retry_failed": {
        "tr": "❌ Yeniden deneme de başarısız oldu.",
        "en": "❌ The retry also failed.",
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
    reply = t("on", locale) if getattr(settings, "reply_messages", True) else t("off", locale)
    configured = getattr(settings, "locale", "tr")
    language = t(f"locale_{configured}", locale) if configured in {"auto", "tr", "en"} else t("locale_tr", locale)
    return (
        f"**{t('auto_thread', locale)}:** {thread}\n"
        f"-# {t('auto_thread_desc', locale)}\n\n"
        f"**{t('youtube_default', locale)}:** {quality}\n"
        f"-# {t('youtube_default_desc', locale)}\n\n"
        f"**{t('reply_messages', locale)}:** {reply}\n"
        f"-# {t('reply_messages_desc', locale)}\n\n"
        f"**{t('language', locale)}:** {language}"
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
            "`/ayar` · server settings: auto thread, default YouTube quality & language (Manage Server)\n"
            "`/info [user]` · your request summary\n"
            "`/cache` · media and card cache status\n"
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
        "`/ayar` · sunucu ayarları: otomatik thread, YouTube varsayılan kalitesi ve dil (Sunucuyu Yönet)\n"
        "`/info [kullanıcı]` · işlem özetin\n"
        "`/cache` · medya ve kart önbelleği durumu\n"
        "`/metrics` · sağlayıcı metrikleri\n"
        "`/stats [24s|7g|30g] [platform]` · işlem paneli (sahip)\n"
        "`/hatalar [aralık]` · son hatalar (sahip)\n"
        "`/bakim [ac|kapat]` · bakım modu (sahip)\n\n"
        "Eski `!` komutları da çalışır (`!ayar`, `!ayar thread ac`)."
    )
