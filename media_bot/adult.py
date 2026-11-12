"""Reddit maturity checks and an atomic daily user quota."""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .reddit_post import ARCTIC, PULLPUSH, RedditPost

TIMEOUT = httpx.Timeout(5, connect=3)


def istanbul_day() -> str:
    return datetime.now(timezone(timedelta(hours=3))).date().isoformat()


class AdultQuota:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS adult_uses (user_id INTEGER NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL, PRIMARY KEY(user_id, day))")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def claim(self, user_id: int, day: str | None = None) -> bool:
        day = day or istanbul_day()
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "INSERT INTO adult_uses(user_id, day, count) VALUES(?, ?, 1) "
                "ON CONFLICT(user_id, day) DO UPDATE SET count=count+1 WHERE count < 1",
                (user_id, day),
            ).rowcount
            return changed == 1

    def release(self, user_id: int, day: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM adult_uses WHERE user_id=? AND day=?", (user_id, day))

    async def aclaim(self, user_id: int, day: str) -> bool:
        return await asyncio.to_thread(self.claim, user_id, day)

    async def arelease(self, user_id: int, day: str) -> None:
        await asyncio.to_thread(self.release, user_id, day)


def channel_is_nsfw(channel: object) -> bool:
    for candidate in (channel, getattr(channel, "parent", None)):
        if candidate is None:
            continue
        check = getattr(candidate, "is_nsfw", None)
        if callable(check) and check():
            return True
    return False


async def reddit_adult_status(post: RedditPost | None) -> bool | None:
    """Return None if neither post nor subreddit maturity can be verified."""
    if post is None:
        return None
    if post.over_18 is True:
        return True
    post_id = urlsplit(post.url).path.split("/comments/")[-1].split("/")[0]
    headers = {"User-Agent": "DiscordMediaBot/1.0 (NSFW label check)"}
    async with httpx.AsyncClient(timeout=TIMEOUT, trust_env=False, headers=headers) as client:
        if post.over_18 is None and post_id:
            for url, params in (
                (f"{ARCTIC}/api/posts/ids", {"ids": post_id}),
                (PULLPUSH, {"ids": post_id}),
            ):
                try:
                    response = await client.get(url, params=params)
                    response.raise_for_status()
                    rows = response.json().get("data") or []
                    for row in rows:
                        if (isinstance(row, dict) and row.get("id") == post_id
                                and str(row.get("subreddit", "")).casefold() == post.subreddit.casefold()
                                and isinstance(row.get("over_18"), bool)):
                            if row["over_18"]:
                                return True
                            break
                except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                    pass
        try:
            response = await client.get(f"https://www.reddit.com/r/{post.subreddit}/about.json")
            response.raise_for_status()
            adult = response.json().get("data", {}).get("over18")
            if isinstance(adult, bool):
                return adult or post.over_18 is True
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            pass
    return post.over_18
