"""Draw complete Reddit text posts as dark, paginated PNG cards."""

from __future__ import annotations

import re
from datetime import timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .reddit_post import RedditPost

WIDTH = 1080
MAX_HEIGHT = 1900
MARGIN = 56
BODY_BOTTOM = 1670
BACKGROUND = "#050505"
WHITE = "#f3f4f6"
MUTED = "#858b93"
RULE = "#292d32"
MONTHS = ("Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara")


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = ([r"C:\Windows\Fonts\segoeuib.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
                  if bold else [r"C:\Windows\Fonts\segoeui.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"])
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont,
          max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        while draw.textlength(word, font=font) > max_width:
            cut = len(word)
            while cut > 1 and draw.textlength(word[:cut], font=font) > max_width:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        current = word
    if current:
        lines.append(current)
    return lines


def _body_lines(draw: ImageDraw.ImageDraw, body: str, font: ImageFont.ImageFont) -> list[tuple[str, int]]:
    lines: list[tuple[str, int]] = []
    for paragraph in re.split(r"\n\s*\n", body.strip()):
        if not paragraph:
            continue
        first = True
        for raw_line in paragraph.splitlines():
            for line in _wrap(draw, raw_line, font, WIDTH - 2 * MARGIN):
                lines.append((line, 38 if lines and first else 0))
                first = False
    return lines


def _date(post: RedditPost) -> str:
    if not post.published:
        return ""
    date = post.published.astimezone(timezone(timedelta(hours=3)))
    return f"{date:%H:%M} · {date.day} {MONTHS[date.month - 1]} {date.year}"


def _header(draw: ImageDraw.ImageDraw, post: RedditPost, page: int, total: int,
            footer: str) -> None:
    draw.ellipse((MARGIN, 54, MARGIN + 96, 150), fill="#eeeeee")
    draw.rectangle((MARGIN + 24, 75, MARGIN + 72, 99), fill="#111111")
    draw.rectangle((MARGIN + 24, 99, MARGIN + 72, 128), fill="#505050")
    draw.text((174, 59), f"r/{post.subreddit}", font=_font(40, bold=True), fill=WHITE)
    if post.author:
        draw.text((174, 111), f"u/{post.author}", font=_font(29), fill=MUTED)
    watermark = footer[:40]
    watermark_font = _font(24, bold=True)
    draw.text((WIDTH - MARGIN - draw.textlength(watermark, font=watermark_font), 28),
              watermark, font=watermark_font, fill="#41454a")
    count = f"{page}/{total}"
    draw.text((WIDTH - MARGIN - draw.textlength(count, font=_font(30)), 105),
              count, font=_font(30), fill=MUTED)


def render_reddit_cards(post: RedditPost, folder: Path,
                        footer: str = "Discord Video Bot", *, preview_only: bool = False) -> list[Path]:
    """Return ordered PNG pages; no post text is cut off."""
    folder.mkdir(parents=True, exist_ok=True)
    measure = ImageDraw.Draw(Image.new("RGB", (WIDTH, 8)))
    body_font = _font(39)
    title_font = _font(45, bold=True)
    body_lines = _body_lines(measure, post.body, body_font)
    title_lines = _wrap(measure, post.title, title_font, WIDTH - 2 * MARGIN)

    page_specs: list[tuple[bool, list[tuple[str, int]]]] = []
    cursor = 0
    while cursor < len(body_lines) or not page_specs:
        first_page = not page_specs
        y = 191 + (len(title_lines) * 60 + 30 if first_page else 0)
        current: list[tuple[str, int]] = []
        while cursor < len(body_lines):
            line, gap = body_lines[cursor]
            if y + gap + 55 > BODY_BOTTOM and current:
                break
            current.append((line, gap))
            y += gap + 55
            cursor += 1
        page_specs.append((first_page, current))

    paths: list[Path] = []
    for index, (first_page, lines) in enumerate(page_specs, 1):
        canvas = Image.new("RGB", (WIDTH, MAX_HEIGHT), BACKGROUND)
        draw = ImageDraw.Draw(canvas)
        _header(draw, post, index, len(page_specs), footer)
        y = 191
        if first_page:
            for title_line in title_lines:
                draw.text((MARGIN, y), title_line, font=title_font, fill=WHITE)
                y += 60
            y += 30
        for line, gap in lines:
            y += gap
            draw.text((MARGIN, y), line, font=body_font, fill=WHITE)
            y += 55
        rule_y = max(y + 32, 355)
        draw.line((MARGIN, rule_y, WIDTH - MARGIN, rule_y), fill=RULE, width=2)
        date = _date(post)
        if date:
            draw.text((MARGIN, rule_y + 23), date, font=_font(27), fill=MUTED)
        stats = []
        if post.score is not None:
            stats.append(f"{post.score:,} Puan".replace(",", "."))
        if post.comments is not None:
            stats.append(f"{post.comments:,} Yorum".replace(",", "."))
        if stats:
            draw.text((MARGIN, rule_y + (67 if date else 23)), "   ".join(stats),
                      font=_font(29, bold=True), fill=WHITE)
        if preview_only:
            draw.text((MARGIN, rule_y + (110 if stats else 65 if date else 23)),
                      "Önizleme · Tam metin alınamadı", font=_font(25), fill=MUTED)
        bottom = min(MAX_HEIGHT, max(480, rule_y + (160 if stats else 125 if preview_only else 85)))
        path = folder / f"reddit_{index:02d}.png"
        canvas.crop((0, 0, WIDTH, bottom)).save(path, format="PNG", optimize=True)
        paths.append(path)
    return paths
