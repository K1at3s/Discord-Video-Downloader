"""Throttled live progress edits for long download jobs."""

from __future__ import annotations

import time
from pathlib import Path

import discord

from . import i18n


def human_bytes(value: int | float | None) -> str:
    if not value or value <= 0:
        return "0 MB"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


class ProgressReporter:
    """Edit one status message at most every ``interval`` seconds."""

    def __init__(self, status_message, locale: str = "tr", *, interval: float = 1.5):
        self.status_message = status_message
        self.locale = locale
        self.interval = interval
        self.files = 0
        self.total_bytes = 0
        self.estimate: int | None = None
        self._last_edit = 0.0
        self._last_text = ""

    @property
    def enabled(self) -> bool:
        return self.status_message is not None and getattr(self.status_message, "edit", None) is not None

    async def _edit(self, text: str) -> None:
        if not self.enabled or text == self._last_text:
            return
        try:
            await self.status_message.edit(content=text)
            self._last_text = text
        except (discord.HTTPException, AttributeError, TypeError):
            pass

    async def downloading(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_edit < self.interval:
            return
        self._last_edit = now
        text = i18n.t(
            "progress_downloading", self.locale,
            files=self.files, size=human_bytes(self.total_bytes),
        )
        if self.estimate:
            text += i18n.t("progress_estimate", self.locale, size=human_bytes(self.estimate))
        await self._edit(text)

    async def on_file(self, paths: list[Path]) -> None:
        self.files += len(paths)
        for path in paths:
            try:
                self.total_bytes += path.stat().st_size
            except OSError:
                pass
        await self.downloading()

    async def set_estimate(self, value: int | None) -> None:
        if value and value > 0:
            self.estimate = value
            await self.downloading(force=True)

    async def uploading(self, *, part: int | None = None, total: int | None = None,
                        force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_edit < self.interval:
            return
        self._last_edit = now
        if part and total:
            text = i18n.t("progress_uploading_part", self.locale, part=part, total=total)
        else:
            text = i18n.t("progress_uploading", self.locale, files=self.files,
                          size=human_bytes(self.total_bytes))
        await self._edit(text)

    async def done(self, count: int) -> None:
        await self._edit(i18n.t("files_sent", self.locale, count=count))
