"""Map technical download failures to short, localized user messages.

Raw provider output stays in the log and owner panels; users get a reason and
a retry button instead of an exception string.
"""

from __future__ import annotations

from . import i18n

_PRIVATE_HINTS = ("private", "login", "authrequired", "sign in required")
_BOT_HINTS = ("not a bot", "confirm you're not a bot", "confirm you’re not a bot", "cookies-from-browser")
_RATE_HINTS = ("rate limit", "too many requests", "429", "quota", "cooldown")
_TIMEOUT_HINTS = ("timeout", "timed out", "zaman aşımı", "zaman asimi")
_NO_MEDIA_HINTS = ("no usable media", "no uploadable media", "no media")
_NOT_FOUND_HINTS = ("not found", "404", "unavailable", "removed", "deleted")
_LARGE_HINTS = ("too large", "413", "entity too large", "size limit", "oversize", "exceeds")
_DISK_HINTS = ("disk", "no space", "not enough space", "boş alan", "yer yok")
_UNSUPPORTED_HINTS = ("unsupported", "not supported", "unknown platform")


def classify(detail: str | None) -> str:
    text = (detail or "").casefold()
    if any(hint in text for hint in _NO_MEDIA_HINTS):
        return "error_no_media"
    if any(hint in text for hint in _PRIVATE_HINTS):
        return "error_private"
    if any(hint in text for hint in _BOT_HINTS):
        return "error_bot_check"
    if any(hint in text for hint in _RATE_HINTS):
        return "error_rate_limit"
    if any(hint in text for hint in _TIMEOUT_HINTS):
        return "error_timeout"
    if any(hint in text for hint in _LARGE_HINTS):
        return "error_too_large"
    if any(hint in text for hint in _DISK_HINTS):
        return "error_disk_space"
    if any(hint in text for hint in _NOT_FOUND_HINTS):
        return "error_not_found"
    if any(hint in text for hint in _UNSUPPORTED_HINTS):
        return "error_unsupported"
    return "error_generic"


def _detail_suffix(detail: str | None) -> str:
    text = (detail or "").strip()
    if not text:
        return ""
    lower = text.lower()
    idx = lower.find("denenen:")
    if idx != -1:
        tried = text[idx + len("denenen:"):].strip(" .)")
        tried = tried[:180]
        return f"\n-# Denenen sağlayıcılar: {tried}" if tried else ""
    # Keep short useful context for generic failures without dumping raw traces.
    if len(text) <= 180 and "\n" not in text and "traceback" not in lower:
        return f"\n-# {text}"
    return ""


def user_message(detail: str | None, locale: str = "tr", *, platform: str = "") -> str:
    key = classify(detail)
    if key == "error_no_media":
        if not platform:
            key = "error_not_found"
            base = i18n.t(key, locale)
        else:
            base = i18n.t(key, locale, platform=platform)
    else:
        base = i18n.t(key, locale)
    suffix = _detail_suffix(detail)
    if suffix and suffix.strip().lower() not in base.lower():
        return base + suffix
    return base
