"""Recognise individual public post links, not profiles or arbitrary URLs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

URL_RE = re.compile(r"https?://[^\s<>\[\]()]+", re.IGNORECASE)


@dataclass(frozen=True)
class PostLink:
    platform: str
    url: str
    post_id: str | None = None


def _host(hostname: str, domain: str) -> bool:
    return hostname == domain or hostname.endswith("." + domain)


def parse_link(raw: str) -> PostLink | None:
    raw = raw.rstrip(".,!?;:>\"'）]")
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower().rstrip(".")
        path = parts.path
        if parts.scheme not in {"http", "https"} or parts.username or parts.password or parts.port:
            return None
    except ValueError:
        return None

    # Strip tracking query strings. Keep the original path for short-link redirects.
    clean = urlunsplit(("https", host, path, "", ""))
    if host in {"x.com", "twitter.com", "www.x.com", "www.twitter.com", "mobile.twitter.com"}:
        match = re.fullmatch(r"/(?:i|[^/]+)/status/(\d+)(?:/.*)?", path, re.I)
        if match:
            return PostLink("x", clean, match.group(1))
    elif _host(host, "instagram.com"):
        if re.fullmatch(r"/(?:p|reel|tv)/[\w-]+/?", path, re.I):
            return PostLink("instagram", clean)
    elif _host(host, "tiktok.com"):
        if re.fullmatch(r"/@[^/]+/(?:video|photo)/\d+/?", path, re.I) or (host in {"vm.tiktok.com", "vt.tiktok.com"} and re.fullmatch(r"/[\w-]+/?", path)):
            return PostLink("tiktok", clean)
    elif _host(host, "facebook.com") or host in {"fb.watch", "www.fb.watch"}:
        if host.endswith("fb.watch") or re.search(r"/(?:reel|videos|watch|share/[vpr]|posts|photos)/", path, re.I) or path == "/photo.php" or (path == "/watch/" and "v=" in parts.query):
            # watch?v needs the query to identify the video.
            if path == "/watch/":
                match = re.search(r"(?:^|&)v=(\d+)(?:&|$)", parts.query)
                if not match:
                    return None
                clean += "?v=" + match.group(1)
            elif path == "/photo.php":
                match = re.search(r"(?:^|&)fbid=(\d+)(?:&|$)", parts.query)
                if not match:
                    return None
                clean += "?fbid=" + match.group(1)
            return PostLink("facebook", clean)
    elif _host(host, "reddit.com") or host in {"redd.it", "www.redd.it"}:
        match = re.search(r"/comments/([a-z0-9]+)", path, re.I) or re.fullmatch(r"/gallery/([a-z0-9]+)/?", path, re.I)
        short = re.fullmatch(r"/r/[^/]+/s/([A-Za-z0-9]+)/?", path, re.I)
        if match or short or (host.endswith("redd.it") and re.fullmatch(r"/[a-z0-9]+/?", path, re.I)):
            return PostLink("reddit", clean, match.group(1) if match else None)
    elif _host(host, "threads.com") or _host(host, "threads.net"):
        if re.fullmatch(r"/@[^/]+/post/[A-Za-z0-9_-]+(?:/media)?/?", path) or re.fullmatch(r"/(?:t|share)/[A-Za-z0-9_-]+/?", path):
            return PostLink("threads", clean)
    elif _host(host, "douyin.com"):
        if re.fullmatch(r"/(?:video|note)/\d+/?", path) or (host.startswith("v.") and re.fullmatch(r"/[A-Za-z0-9]+/?", path)):
            return PostLink("douyin", clean)
    elif _host(host, "weibo.com") or _host(host, "weibo.cn"):
        if re.fullmatch(r"/(?:\d+|[A-Za-z0-9_.-]+)/[A-Za-z0-9]+/?", path) or re.fullmatch(r"/status/[A-Za-z0-9]+/?", path):
            return PostLink("weibo", clean)
    elif _host(host, "streamable.com"):
        if re.fullmatch(r"/[A-Za-z0-9]+/?", path):
            return PostLink("streamable", clean, path.strip("/"))
    elif host == "open.spotify.com":
        match = re.fullmatch(
            r"/(?:intl-[A-Za-z]{2}(?:-[A-Za-z]{2})?/)?(track|album|episode|show|artist|playlist)/([A-Za-z0-9]+)/?",
            path,
            re.I,
        )
        if match:
            return PostLink("spotify", f"https://open.spotify.com/{match.group(1).lower()}/{match.group(2)}")
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}:
        video_id = None
        if host.endswith("youtu.be"):
            match = re.fullmatch(r"/([A-Za-z0-9_-]{11})/?", path)
            video_id = match.group(1) if match else None
        elif path == "/watch":
            match = re.search(r"(?:^|&)v=([A-Za-z0-9_-]{11})(?:&|$)", parts.query)
            video_id = match.group(1) if match else None
        else:
            match = re.fullmatch(r"/(?:shorts|live)/([A-Za-z0-9_-]{11})/?", path)
            video_id = match.group(1) if match else None
        if video_id:
            return PostLink("youtube", f"https://www.youtube.com/watch?v={video_id}", video_id)
    return None


def find_links(text: str, limit: int = 3) -> list[PostLink]:
    found: list[PostLink] = []
    seen: set[str] = set()
    for raw in URL_RE.findall(text):
        link = parse_link(raw)
        if link and link.url not in seen:
            found.append(link)
            seen.add(link.url)
        if len(found) >= limit:
            break
    return found
