"""Extracted admin/command helper methods for MediaBot."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import discord

from . import i18n
from .card_cache import CARD_CACHE
from .circuit_breaker import BREAKERS
from .guild_settings import SETTINGS
from .highlight_state import HIGHLIGHTS, format_wait
from .maintenance import MAINTENANCE
from .media_cache import MEDIA_CACHE
from .provider_metrics import METRICS
from .request_log import REQUESTS, _human_bytes, parse_range, resolve_platform

LOG = logging.getLogger("media_bot.bot")


def _is_admin(message) -> bool:
    permissions = getattr(getattr(message, "author", None), "guild_permissions", None)
    return bool(getattr(permissions, "administrator", False) or getattr(permissions, "manage_guild", False))


class AdminCommandsMixin:
    async def _reply_metrics(self, message: discord.Message) -> None:
        parts = [
            METRICS.format_text(24),
            "",
            METRICS.format_text(168, limit=8),
        ]
        breakers = BREAKERS.snapshot()
        if breakers.get("open"):
            parts.append("")
            items = ", ".join(f"{name} (~{sec}s)" for name, sec in list(breakers["open"].items())[:8])
            parts.append(f"Devre kesici açık: {items}")
        if HIGHLIGHTS.enabled and HIGHLIGHTS.bucket:
            remaining = HIGHLIGHTS.bucket.remaining()
            wait = HIGHLIGHTS.bucket.wait_seconds()
            parts.append("")
            parts.append(
                f"Highlight bucket (indown.io, lokal tahmin): kalan={remaining} "
                f"bekleme={format_wait(wait) if wait > 0 else '0 dk'} "
                f"health={'açık' if HIGHLIGHTS.health_check_enabled else 'kapalı'}"
            )
        cards = CARD_CACHE.stats()
        if cards.get("enabled"):
            lookups = cards.get("hits", 0) + cards.get("misses", 0)
            rate = f"{(cards.get('hits', 0) / lookups) * 100:.0f}%" if lookups else "—"
            parts.append("")
            parts.append(
                f"Kart önbelleği: {cards['entries']} kayıt · {cards['files']} dosya · "
                f"{_human_bytes(cards['bytes'])} · isabet {rate}"
            )
        quota = getattr(self, "user_quota", None)
        if quota is not None and quota.enabled:
            try:
                summary = quota.summary()
            except Exception:
                LOG.exception("Kota özeti okunamadı")
            else:
                parts.append("")
                parts.append(
                    f"Kota (bu saat/gün): {summary['hourly']}/"
                    f"{summary['hourly_limit'] or '∞'} saatlik · {summary['daily']}/"
                    f"{summary['daily_limit'] or '∞'} günlük · {summary['users']} kullanıcı"
                )
        text = "\n".join(parts).strip()
        await self._reply(message, text[:1900])

    async def _reply_request_stats(self, message: discord.Message, argument: str = "") -> None:
        hours = 24
        platform: str | None = None
        for token in argument.split():
            parsed = parse_range(token)
            if parsed is not None:
                hours = parsed
                continue
            key = resolve_platform(token)
            if key:
                platform = key
                continue
            await self._reply(
                message,
                f"Bilinmeyen argüman: `{token}`\n"
                "Kullanım: `!stats` · `!stats 7g` · `!stats 30g` · `!stats 7g instagram`",
            )
            return
        if platform:
            text = REQUESTS.format_platform(platform, hours)
        else:
            role = "Yönetici" if _is_admin(message) else "Kullanıcı"
            text = REQUESTS.format_panel(hours, role=role, maintenance=MAINTENANCE.active)
        await self._reply(message, text[:1900])

    async def _reply_maintenance(self, message: discord.Message, argument: str = "") -> None:
        action = argument.strip().lower()
        if not action:
            state = "Açık" if MAINTENANCE.active else "Kapalı"
            await self._reply(
                message,
                f"🧰 Bakım modu: **{state}**\n"
                "Açmak için `!bakim ac`, kapatmak için `!bakim kapat`.",
            )
            return
        if action in {"ac", "aç", "on", "1", "açık", "acik"}:
            MAINTENANCE.set(True)
            await self._reply(message, f"🧰 Bakım modu açıldı. {MAINTENANCE.message}")
            return
        if action in {"kapat", "off", "0", "kapalı", "kapali"}:
            MAINTENANCE.set(False)
            await self._reply(message, "✅ Bakım modu kapatıldı; yeni indirmeler yeniden açık.")
            return
        await self._reply(message, "Kullanım: `!bakim` · `!bakim ac` · `!bakim kapat`")

    def _can_manage_guild(self, author) -> bool:
        permissions = getattr(author, "guild_permissions", None)
        return bool((permissions and permissions.manage_guild) or getattr(author, "id", 0) == self.owner_id)

    async def _reply_settings(self, message: discord.Message, argument: str = "") -> None:
        guild = getattr(message, "guild", None)
        if guild is None:
            await self._reply(message, "Bu ayar yalnızca sunucularda kullanılır.")
            return
        if not self._can_manage_guild(getattr(message, "author", None)):
            await self._reply(message, "Bu komut için **Sunucuyu yönet** izni gerekir.")
            return
        locale = i18n.locale_for_message(message)
        usage = i18n.t("settings_usage", locale)
        tokens = argument.split()
        changes: dict = {}
        index = 0
        while index < len(tokens):
            token = tokens[index].lower()
            if token in {"thread", "otomatik_thread", "otothread"}:
                if index + 1 >= len(tokens):
                    await self._reply(message, usage)
                    return
                value = tokens[index + 1].lower()
                if value in {"ac", "aç", "on", "1", "evet"}:
                    changes["auto_thread"] = True
                elif value in {"kapat", "off", "0", "hayir", "hayır"}:
                    changes["auto_thread"] = False
                else:
                    await self._reply(message, usage)
                    return
                index += 2
            elif token in {"youtube", "kalite"}:
                if index + 1 >= len(tokens):
                    await self._reply(message, usage)
                    return
                changes["youtube_quality"] = tokens[index + 1]
                index += 2
            elif token in {"yanit", "yanıt", "yanitla", "reply", "cevapla"}:
                if index + 1 >= len(tokens):
                    await self._reply(message, usage)
                    return
                value = tokens[index + 1].lower()
                if value in {"ac", "aç", "on", "1", "evet"}:
                    changes["reply_messages"] = True
                elif value in {"kapat", "off", "0", "hayir", "hayır"}:
                    changes["reply_messages"] = False
                else:
                    await self._reply(message, usage)
                    return
                index += 2
            elif token in {"dil", "lang", "language", "locale"}:
                if index + 1 >= len(tokens):
                    await self._reply(message, usage)
                    return
                value = tokens[index + 1].lower()
                mapping = {
                    "tr": "tr", "turkce": "tr", "türkçe": "tr", "turkish": "tr",
                    "en": "en", "english": "en", "ingilizce": "en", "auto": "auto",
                    "otomatik": "auto", "varsayilan": "auto", "varsayılan": "auto",
                }
                if value not in mapping:
                    await self._reply(message, usage)
                    return
                changes["locale"] = mapping[value]
                index += 2
            elif token in {"sifirla", "sıfırla", "reset"}:
                changes.update(auto_thread=False, youtube_quality="sor",
                               reply_messages=True, locale="tr")
                index += 1
            else:
                await self._reply(message, usage)
                return
        try:
            settings = SETTINGS.update(guild.id, **changes)
        except ValueError as exc:
            await self._reply(message, f"{exc}\nKalite: `sor`, `mp3`, `360`, `720`, `1080`")
            return
        shown_locale = settings.locale if settings.locale in {"tr", "en"} else locale
        await self._reply(message, i18n.settings_message(settings, shown_locale, updated=bool(changes)))

    async def _reply_log_clear(self, message: discord.Message, argument: str) -> None:
        confirmed = "onay" in {token.lower() for token in argument.split()}
        if not confirmed:
            total = REQUESTS.count()
            await self._reply(
                message,
                f"⚠️ Tüm işlem kayıtları silinecek ({total} satır).\n"
                "Onaylamak için `!logtemizle onay` yazın.",
            )
            return
        removed = REQUESTS.clear()
        await self._reply(message, f"🧹 {removed} işlem kaydı silindi.")

    async def _reply_log_delete_user(self, message: discord.Message, argument: str) -> None:
        owner = message.author.id == getattr(self, "owner_id", 0)
        tokens = argument.split()
        confirmed = any(token.lower() in {"onay", "confirm"} for token in tokens)
        target = message.author
        target_name = getattr(target, "display_name", None) or getattr(target, "name", None) or str(target)
        if message.mentions:
            target = message.mentions[0]
            target_name = getattr(target, "display_name", None) or getattr(target, "name", None) or str(target)
        else:
            for token in tokens:
                if token.isdigit():
                    target = SimpleNamespace(id=int(token), display_name=token, name=token)
                    target_name = token
                    break
        is_other = getattr(target, "id", None) != message.author.id
        if is_other and not owner:
            await self._reply(message, "Başka bir kullanıcının kayıtlarını yalnızca bot sahibi silebilir.")
            return
        if not confirmed:
            count = REQUESTS.count(user_id=getattr(target, "id", 0))
            hint = "!logsil @kullanıcı onay" if is_other else "!logsil onay"
            await self._reply(
                message,
                f"⚠️ {target_name} için {count} kayıt silinecek.\nOnaylamak için `{hint}` yazın.",
            )
            return
        removed = REQUESTS.clear(user_id=getattr(target, "id", None))
        await self._reply(message, f"🧹 {target_name} için {removed} kayıt silindi.")

    async def _reply_errors(self, message: discord.Message, argument: str = "") -> None:
        hours = 24
        for token in argument.split():
            parsed = parse_range(token)
            if parsed is not None:
                hours = parsed
        await self._reply(message, REQUESTS.format_errors(hours)[:1900])

    async def _reply_cache(self, message: discord.Message) -> None:
        def ttl_text(seconds: float) -> str:
            if seconds >= 3600:
                return f"{seconds / 3600:g} saat"
            return f"{seconds / 60:g} dk"

        media = MEDIA_CACHE.stats()
        hits = media.get("hits", 0)
        misses = media.get("misses", 0)
        stores = media.get("stores", 0)
        lookups = hits + misses
        hit_rate = (f"{(hits / lookups) * 100:.0f}%" if lookups else "—")
        lines = [
            "📦 Önbellek durumu",
            f"Genel medya: {'açık' if media['enabled'] else 'kapalı'} · "
            f"{media['entries']} kayıt · {media['files']} dosya · {_human_bytes(media['bytes'])} "
            f"(sınır {_human_bytes(media['max_bytes'])}, TTL {ttl_text(media['ttl'])})",
            f"İsabet/kaçırma: {hits}/{misses} (oran {hit_rate}) · kayıt ekleme: {stores}",
        ]
        if media["stale"]:
            lines.append(f"♻️ Temizlenmeyi bekleyen eski kayıt: {media['stale']}")
        cards = CARD_CACHE.stats()
        if cards["enabled"]:
            card_lookups = cards.get("hits", 0) + cards.get("misses", 0)
            card_rate = (f"{(cards.get('hits', 0) / card_lookups) * 100:.0f}%"
                         if card_lookups else "—")
            lines.append(
                f"X/Reddit kart: {cards['entries']} kayıt · {cards['files']} dosya · "
                f"{_human_bytes(cards['bytes'])} (sınır {_human_bytes(cards['max_bytes'])}, "
                f"TTL {ttl_text(cards['ttl'])}) · isabet {card_rate}"
            )
        highlight = HIGHLIGHTS.cache.stats() if HIGHLIGHTS.cache else None
        if highlight is not None:
            lines.append(
                f"Highlight: {highlight['entries']} kayıt · {highlight['files']} dosya · "
                f"{_human_bytes(highlight['bytes'])} (sınır {_human_bytes(highlight['max_bytes'])}, "
                f"TTL {ttl_text(highlight['ttl'])})"
            )
            if highlight["stale"]:
                lines.append(f"♻️ Eski highlight kaydı: {highlight['stale']}")
        lines.append("")
        lines.append("Temizlemek için (sahip): `!cachetemizle`")
        await self._reply(message, "\n".join(lines)[:1900])

    async def _reply_cache_clear(self, message: discord.Message) -> None:
        media = MEDIA_CACHE.clear() if MEDIA_CACHE.enabled else 0
        cards = CARD_CACHE.clear() if CARD_CACHE.enabled else 0
        highlight = HIGHLIGHTS.cache.clear() if HIGHLIGHTS.cache else 0
        await self._reply(
            message,
            f"🧹 Önbellek temizlendi · genel medya: {media} kayıt · "
            f"X/Reddit kart: {cards} kayıt · highlight: {highlight} kayıt",
        )

    async def _reply_user_info(self, message: discord.Message, argument: str = "") -> None:
        hours = 24
        id_token = ""
        for token in argument.split():
            if token.startswith("<@") or token.startswith("<@!") or token.isdigit():
                id_token = token
                continue
            parsed = parse_range(token)
            if parsed is not None:
                hours = parsed
        target = message.author
        target_name = getattr(target, "display_name", None) or getattr(target, "name", None) or str(target)
        if message.mentions:
            target = message.mentions[0]
            target_name = getattr(target, "display_name", None) or getattr(target, "name", None) or str(target)
        elif id_token.isdigit():
            target_id = int(id_token)
            target = SimpleNamespace(id=target_id, display_name=str(target_id), name=str(target_id))
            target_name = str(target_id)
        if getattr(target, "id", None) != message.author.id and message.author.id != getattr(self, "owner_id", 0):
            await self._reply(message, "Başka bir kullanıcının istatistiğini yalnızca bot sahibi görebilir.")
            return
        text = REQUESTS.format_user(getattr(target, "id", 0), hours, display_name=target_name)
        await self._reply(message, text[:1900])

