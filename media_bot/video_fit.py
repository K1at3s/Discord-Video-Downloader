"""Fit an oversized downloaded video into one Discord attachment."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

VIDEO_SLOTS = asyncio.Semaphore(2)


async def _command(*args: str, timeout: int) -> tuple[int, bytes]:
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout)
    except (TimeoutError, asyncio.CancelledError):
        process.kill()
        await process.communicate()
        raise
    return process.returncode or 0, stdout


async def fit_video(source: Path, target: Path, max_bytes: int, *,
                    force: bool = False, max_height: int = 720) -> bool:
    """Reencode only when needed; keep the result below the upload limit.

    ``force`` is used after Discord rejects an upload: even an apparently small
    file is reencoded at a lower bitrate so the retry has a real chance.
    """
    if not source.is_file() or source.stat().st_size == 0:
        return False
    if not force and source.stat().st_size <= max_bytes:
        source.replace(target)
        return True
    async with VIDEO_SLOTS:
        try:
            code, output = await _command(
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "json", str(source), timeout=20,
            )
            if code or not output:
                return False
            duration = float(json.loads(output)["format"]["duration"])
            if duration <= 0:
                return False
            audio_rate = 96_000
            # Leave room for container overhead and variable rate peaks.
            video_rate = int(max_bytes * 8 * 0.78 / duration) - audio_rate
            if video_rate < 150_000:
                return False
            for factor in (1.0, 0.72):
                target.unlink(missing_ok=True)
                code, _ = await _command(
                    "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
                    "-i", str(source), "-map", "0:v:0", "-map", "0:a?",
                    "-vf", f"scale=w='min({max_height},iw)':h=-2",
                    "-c:v", "libx264", "-preset", "veryfast", "-b:v", str(int(video_rate * factor)),
                    "-c:a", "aac", "-b:a", str(audio_rate),
                    "-movflags", "+faststart", str(target), timeout=180,
                )
                if code == 0 and target.is_file() and 0 < target.stat().st_size <= max_bytes:
                    return True
        except (TimeoutError, OSError, ValueError, KeyError, TypeError):
            return False
        finally:
            if not target.is_file() or target.stat().st_size > max_bytes:
                target.unlink(missing_ok=True)
    return False
