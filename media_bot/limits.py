"""Limits for one post; Discord uploads are split into smaller messages."""

from pathlib import Path

MAX_MEDIA_ITEMS = 100
FILES_PER_MESSAGE = 10


def attachment_batches(files: list[Path]) -> list[list[Path]]:
    return [files[start:start + FILES_PER_MESSAGE]
            for start in range(0, len(files), FILES_PER_MESSAGE)]
