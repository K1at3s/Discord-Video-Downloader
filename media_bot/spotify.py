"""Match a Spotify track to a full-length public audio source."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from html.parser import HTMLParser

import httpx

from .download import DownloadError
from .links import PostLink


@dataclass(frozen=True)
class SpotifyTrack:
    title: str
    artists: str
    duration: int | None


class _Meta(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        if tag != "meta":
            return
        attributes = dict(attrs)
        name = attributes.get("property") or attributes.get("name")
        if name and attributes.get("content"):
            self.values[name] = attributes["content"]


def parse_track_page(html: str) -> SpotifyTrack | None:
    parser = _Meta()
    parser.feed(html[:1_000_000])
    title = parser.values.get("og:title", "").strip()
    artists = parser.values.get("music:musician_description", "").strip()
    raw_duration = parser.values.get("music:duration", "")
    duration = int(raw_duration) if raw_duration.isdecimal() else None
    if not title or not artists or len(title) > 200 or len(artists) > 300:
        return None
    return SpotifyTrack(title, artists, duration)


async def fetch_track(link: PostLink) -> SpotifyTrack:
    if not re.fullmatch(r"https://open\.spotify\.com/track/[A-Za-z0-9]+", link.url):
        raise DownloadError("Yalnızca tekil Spotify parça bağlantıları destekleniyor.")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=4), trust_env=False) as client:
            response = await client.get(link.url)
            response.raise_for_status()
        track = parse_track_page(response.text)
    except (httpx.HTTPError, ValueError) as exc:
        raise DownloadError("Spotify parça bilgisi alınamadı.") from exc
    if track is None:
        raise DownloadError("Spotify parça ve sanatçı bilgisi bulunamadı.")
    return track


def _words(value: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", value.casefold(), re.UNICODE))


def choose_match(entries: list[dict], track: SpotifyTrack) -> str | None:
    title_words = _words(track.title)
    artist_names = [name.strip() for name in track.artists.split(",") if name.strip()]
    candidates: list[tuple[float, str]] = []
    for entry in entries[:8]:
        if not isinstance(entry, dict):
            continue
        video_id = entry.get("id")
        video_title = entry.get("title")
        duration = entry.get("duration")
        if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            continue
        if not isinstance(video_title, str) or not title_words <= _words(video_title):
            continue
        if not isinstance(duration, (int, float)) or duration < 45 or duration > 900:
            continue
        if track.duration and abs(duration - track.duration) > max(15, track.duration * 0.1):
            continue
        matched_artists = sum(bool(_words(name) & _words(video_title + " " + str(entry.get("channel") or "")))
                              for name in artist_names)
        if not matched_artists:
            continue
        score = matched_artists * 10 + (5 if track.duration else 0)
        score -= abs(duration - track.duration) / 10 if track.duration else 0
        if entry.get("channel_is_verified") is True:
            score += 3
        if "official" in video_title.casefold() or "topic" in str(entry.get("channel") or "").casefold():
            score += 2
        candidates.append((score, video_id))
    return max(candidates)[1] if candidates else None


async def find_audio_source(track: SpotifyTrack) -> PostLink:
    query = f"{track.artists} {track.title} official audio"[:300]
    command = [sys.executable, "-m", "yt_dlp", "--no-config", "--flat-playlist",
               "--dump-single-json", "--playlist-end", "8", f"ytsearch8:{query}"]
    proc = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.CancelledError:
        proc.kill()
        await proc.communicate()
        raise
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise DownloadError("Ses kaynağı araması zaman aşımına uğradı.")
    try:
        entries = json.loads(stdout).get("entries") or []
    except (ValueError, TypeError, AttributeError):
        entries = []
    video_id = choose_match(entries, track)
    if not video_id:
        raise DownloadError("Parçayla eşleşen tam uzunlukta ses kaydı bulunamadı.")
    return PostLink("youtube", f"https://www.youtube.com/watch?v={video_id}", video_id)
