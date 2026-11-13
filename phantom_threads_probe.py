"""Phantom Threads extraction probe; no media downloads.

Usage: python phantom_threads_probe.py URL [local_cookie_header_file]
"""
import json
import re
import sys
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlsplit
import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
THREADS_HOSTS = {"threads.com", "www.threads.com", "threads.net", "www.threads.net"}


def trusted_url(url):
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname in THREADS_HOSTS
                and not parsed.username and not parsed.password and parsed.port in (None, 443))
    except ValueError:
        return False

def walk(value, media):
    if isinstance(value, list):
        for child in value:
            walk(child, media)
    elif isinstance(value, dict):
        videos = value.get("video_versions")
        if isinstance(videos, list):
            valid = [v for v in videos if isinstance(v, dict) and isinstance(v.get("url"), str)]
            if valid:
                best = max(valid, key=lambda v: v.get("width") or 0)
                media.append(("video", best["url"], value.get("code")))
        images = value.get("image_versions2")
        if isinstance(images, dict):
            valid = [v for v in images.get("candidates", []) if isinstance(v, dict) and isinstance(v.get("url"), str)]
            if valid:
                best = max(valid, key=lambda v: v.get("width") or 0)
                media.append(("photo", best["url"], value.get("code")))
        for child in value.values():
            walk(child, media)

def probe(client, url):
    if not trusted_url(url):
        raise ValueError("Only HTTPS Threads URLs are allowed")
    print("REQUEST", url)
    try:
        for _ in range(4):
            response = client.get(url)
            if not response.is_redirect:
                break
            destination = urljoin(url, response.headers.get("location", ""))
            if not trusted_url(destination):
                raise ValueError("Threads redirected to an untrusted host")
            print("REDIRECT", response.status_code, destination)
            url = destination
        else:
            raise ValueError("Too many Threads redirects")
    except httpx.HTTPError as exc:
        print("ERROR", type(exc).__name__, exc)
        return
    print("STATUS", response.status_code, "FINAL", response.url)
    markup = response.text
    media = []
    scripts = re.findall(r'<script[^>]*type=["\x27]application/json["\x27][^>]*>(.*?)</script>', markup, re.I | re.S)
    for script in scripts:
        try:
            walk(json.loads(script), media)
        except ValueError:
            pass
    if not media:
        for prop, kind in (("og:video:secure_url", "video"), ("og:video:url", "video"), ("og:video", "video"), ("og:image", "photo")):
            match = re.search(r'<meta[^>]*property=["\x27]' + re.escape(prop) + r'["\x27][^>]*content=["\x27]([^"\x27]+)', markup, re.I)
            if match:
                media.append((kind, unescape(match.group(1)), None))
                break
    print("JSON_SCRIPTS", len(scripts), "MEDIA", len(media))
    for kind, media_url, code in media[:12]:
        print(kind, "code=" + str(code), "host=" + str(urlsplit(media_url).hostname))

if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit("Usage: python phantom_threads_probe.py URL [local_cookie_header_file]")
    url = sys.argv[1]
    if not trusted_url(url):
        raise SystemExit("Only HTTPS Threads URLs are allowed")
    headers = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
    if len(sys.argv) == 3:
        cookie_path = Path(sys.argv[2])
        cookie = cookie_path.read_text(encoding="utf-8-sig").strip()
        if cookie.lower().startswith("cookie:"):
            cookie = cookie.split(":", 1)[1].strip()
        if not cookie or "\n" in cookie or "\r" in cookie:
            raise SystemExit("Cookie file must contain exactly one Cookie header value")
        headers["Cookie"] = cookie
    with httpx.Client(timeout=httpx.Timeout(12, connect=5), follow_redirects=False, trust_env=False,
                      headers=headers) as client:
        probe(client, url)
        probe(client, url.split("?")[0].rstrip("/") + "/embed")
