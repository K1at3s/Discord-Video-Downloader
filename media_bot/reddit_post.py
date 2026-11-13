"""Full public Reddit post text with RSS and archive fallbacks."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx

from .links import PostLink, parse_link
from .race import first_success

ATOM = "{http://www.w3.org/2005/Atom}"
ARCTIC = "https://arctic-shift.photon-reddit.com"
PULLPUSH = "https://api.pullpush.io/reddit/search/submission/"
TIMEOUT = httpx.Timeout(8, connect=4)
USER_AGENT = "DiscordMediaBot/1.0 (public Reddit post text)"


@dataclass(frozen=True)
class RedditPost:
    title: str
    body: str
    subreddit: str
    author: str
    url: str
    published: datetime | None = None
    score: int | None = None
    comments: int | None = None
    is_self: bool | None = None
    over_18: bool | None = None

    @property
    def local_time(self) -> str | None:
        if self.published is None:
            return None
        return self.published.astimezone(timezone(timedelta(hours=3))).strftime("%d.%m.%Y %H:%M")


class _Body(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "div":
            if self.depth:
                self.depth += 1
            elif "md" in (dict(attrs).get("class") or "").split():
                self.depth = 1
        elif self.depth and tag in {"p", "ul", "ol", "blockquote", "pre"}:
            self.parts.append("\n\n")
        elif self.depth and tag == "br":
            self.parts.append("\n")
        elif self.depth and tag == "li":
            self.parts.append("\n• ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self.depth:
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.parts.append(data)

    def text(self) -> str:
        text = "".join(self.parts).replace("\r", "")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _subreddit(link: PostLink) -> str | None:
    match = re.search(r"^/r/([A-Za-z0-9_]+)(?:/|$)", urlsplit(link.url).path, re.I)
    return match.group(1) if match else None


def _rss_post(xml: str, link: PostLink, title_hint: str | None) -> RedditPost | None:
    subreddit = _subreddit(link)
    if not subreddit:
        return None
    try:
        root = ET.fromstring(xml)
        if root.tag != f"{ATOM}feed":
            return None
        entries = root.findall(f"{ATOM}entry")
    except ET.ParseError:
        return None
    title_key = " ".join(title_hint.split()).casefold() if title_hint else None
    for entry in entries:
        post_id = (entry.findtext(f"{ATOM}id") or "").removeprefix("t3_")
        title = (entry.findtext(f"{ATOM}title") or "").strip()
        if link.post_id:
            if post_id != link.post_id:
                continue
        elif not title_key or " ".join(title.split()).casefold() != title_key:
            continue
        link_element = entry.find(f"{ATOM}link")
        href = link_element.get("href") if link_element is not None else ""
        canonical = parse_link(href)
        if not canonical or canonical.platform != "reddit" or canonical.post_id != post_id:
            continue
        if (_subreddit(canonical) or "").casefold() != subreddit.casefold():
            continue
        content = entry.findtext(f"{ATOM}content") or ""
        parser = _Body()
        parser.feed(content)
        submitted_link = re.search(r'<a\s+[^>]*href="([^"]+)"[^>]*>\s*\[link\]\s*</a>', content, re.I)
        target = unescape(submitted_link.group(1)) if submitted_link else ""
        target_path = urlsplit(target).path
        is_self = (bool(re.search(rf"/comments/{re.escape(post_id)}(?:/|$)", target_path, re.I))
                   and not re.search(r"<img\b|<video\b", content, re.I)) if target else None
        author = (entry.findtext(f"{ATOM}author/{ATOM}name") or "").removeprefix("/u/")
        published_raw = entry.findtext(f"{ATOM}published") or entry.findtext(f"{ATOM}updated")
        try:
            published = datetime.fromisoformat(published_raw) if published_raw else None
        except ValueError:
            published = None
        return RedditPost(title, parser.text(), subreddit, author, canonical.url, published,
                          is_self=is_self)
    return None


def _archive_post(data: object, subreddit: str, post_id: str) -> RedditPost | None:
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict) or row.get("id") != post_id:
            continue
        if str(row.get("subreddit", "")).casefold() != subreddit.casefold():
            continue
        title = row.get("title")
        body = row.get("selftext") or ""
        if not isinstance(title, str) or not isinstance(body, str):
            continue
        if body in {"[removed]", "[deleted]"}:
            body = ""
        raw_url = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/"
        timestamp = row.get("created_utc")
        published = datetime.fromtimestamp(timestamp, timezone.utc) if isinstance(timestamp, (int, float)) else None
        score = row.get("score") if isinstance(row.get("score"), int) else None
        comments = row.get("num_comments") if isinstance(row.get("num_comments"), int) else None
        is_self = row.get("is_self") if isinstance(row.get("is_self"), bool) else None
        if is_self is None and isinstance(row.get("url"), str):
            target_path = urlsplit(row["url"]).path
            is_self = bool(re.search(rf"/comments/{re.escape(post_id)}(?:/|$)", target_path, re.I))
        over_18 = row.get("over_18") if isinstance(row.get("over_18"), bool) else None
        return RedditPost(title, body.strip(), subreddit, str(row.get("author") or ""),
                          raw_url, published, score, comments, is_self, over_18)
    return None


async def fetch_reddit_post(link: PostLink, title_hint: str | None = None) -> RedditPost | None:
    subreddit = _subreddit(link)
    if not subreddit:
        return None
    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False,
                                 trust_env=False, headers=headers) as client:
        try:
            feed_url = (f"https://www.reddit.com/r/{subreddit}/comments/{link.post_id}/.rss"
                        if link.post_id else f"https://www.reddit.com/r/{subreddit}/new/.rss")
            response = await client.get(feed_url, params={} if link.post_id else {"limit": "100"})
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type == "application/atom+xml" and len(response.content) <= 2_000_000:
                post = _rss_post(response.text, link, title_hint)
                if post:
                    return post
        except (httpx.HTTPError, ValueError):
            pass

        async def archive(route: str) -> RedditPost | None:
            try:
                if route == "arctic":
                    post_id = link.post_id
                    if not post_id:
                        response = await client.get(f"{ARCTIC}/api/short_links",
                                                    params={"paths": urlsplit(link.url).path})
                        response.raise_for_status()
                        for row in response.json().get("data") or []:
                            resolved = row.get("resolved_path") if isinstance(row, dict) else None
                            candidate = parse_link(f"https://www.reddit.com{resolved}") if isinstance(resolved, str) else None
                            if candidate and (_subreddit(candidate) or "").casefold() == subreddit.casefold():
                                post_id = candidate.post_id
                                break
                    if not post_id:
                        return None
                    response = await client.get(f"{ARCTIC}/api/posts/ids", params={"ids": post_id})
                    response.raise_for_status()
                    return _archive_post(response.json(), subreddit, post_id)

                if not link.post_id and not title_hint:
                    return None
                params = {"ids": link.post_id} if link.post_id else {
                    "q": title_hint, "subreddit": subreddit, "size": "20"}
                response = await client.get(PULLPUSH, params=params)
                response.raise_for_status()
                rows = response.json().get("data") or []
                expected = " ".join(title_hint.split()).casefold() if title_hint else None
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    if expected and " ".join(str(row.get("title") or "").split()).casefold() != expected:
                        continue
                    post_id = row.get("id")
                    if isinstance(post_id, str):
                        post = _archive_post({"data": [row]}, subreddit, post_id)
                        if post and (not link.post_id or post_id == link.post_id):
                            return post
            except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                return None
            return None

        return await first_success(("arctic", "pullpush"), archive, stagger=0, parallel=2)
