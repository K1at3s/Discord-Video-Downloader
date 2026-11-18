"""Runtime/config helpers for the Discord bot entrypoint."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

import discord


def _configure_logging() -> None:
    """Console output plus a rotating file so long runs do not fill the disk."""
    from logging.handlers import RotatingFileHandler

    level_name = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    level = getattr(logging, level_name, logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    raw = os.getenv("LOG_FILE", "data/bot.log").strip()
    if raw.lower() in {"", "0", "off", "false"}:
        return
    try:
        path = Path(raw).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(path, maxBytes=10 * 1024 * 1024,
                                           backupCount=7, encoding="utf-8")
    except OSError as exc:
        logging.getLogger(__name__).warning("Log dosyası açılamadı: %s", exc)
        return
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def _prepare_temp_dir(root: Path) -> None:
    """Keep temporary downloads under the project so Defender exclusions apply."""
    root.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - 24 * 3600
    for child in root.iterdir():
        try:
            if child.stat().st_mtime >= cutoff:
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        except OSError:
            continue
    tempfile.tempdir = str(root)


def _upload_too_large(exc: discord.HTTPException) -> bool:
    text = str(exc).lower()
    return bool(getattr(exc, "status", None) == 413 or "entity too large" in text
                or "larger than" in text or "40005" in text)


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    value = int(os.getenv(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} {minimum} ile {maximum} arasında olmalı")
    return value


def _positive_int(name: str, default: int, maximum: int) -> int:
    value = int(os.getenv(name, str(default)))
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} 1 ile {maximum} arasında olmalı")
    return value
