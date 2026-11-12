"""Dark, story-style image of an X post and its quoted posts."""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .x_card import _count, _font, _wrap

WIDTH = 1080
TEXT = "#f2f5f7"
MUTED = "#9aa8b3"
BLUE = "#27a7e7"


def _date_label(value: object) -> str:
    raw = str(value or "")
    try:
        date = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if date.tzinfo:
            date = date.astimezone(timezone(timedelta(hours=3)))
        return date.strftime("%d.%m.%Y · %H:%M")
    except ValueError:
        return raw[:28]


def _emoji_font(size: int) -> ImageFont.FreeTypeFont | None:
    try:
        return ImageFont.truetype(r"C:\Windows\Fonts\seguiemj.ttf", size)
    except OSError:
        return None


def _pieces(value: str) -> list[tuple[str, str]]:
    """Split text so regional flags and emoji do not use the text font."""
    pieces: list[tuple[str, str]] = []
    plain = ""
    index = 0
    while index < len(value):
        char = value[index]
        code = ord(char)
        if 0x1F1E6 <= code <= 0x1F1FF and index + 1 < len(value) and 0x1F1E6 <= ord(value[index + 1]) <= 0x1F1FF:
            if plain:
                pieces.append(("text", plain))
                plain = ""
            pieces.append(("flag", value[index:index + 2]))
            index += 2
            continue
        if code >= 0x1F300 or char in "ℹ❤♥⭐✅☑":
            if plain:
                pieces.append(("text", plain))
                plain = ""
            end = index + 1
            while end < len(value) and (ord(value[end]) in {0xFE0F, 0x200D} or 0x1F3FB <= ord(value[end]) <= 0x1F3FF):
                end += 1
                if value[end - 1] == "\u200d" and end < len(value):
                    end += 1
            pieces.append(("emoji", value[index:end]))
            index = end
            continue
        plain += char
        index += 1
    if plain:
        pieces.append(("text", plain))
    return pieces


def _flag(draw: ImageDraw.ImageDraw, x: int, y: int, symbol: str, size: int) -> None:
    country = "".join(chr(ord(char) - 0x1F1E6 + 65) for char in symbol)
    width = int(size * 1.12)
    height = int(size * 0.72)
    top = y + int(size * 0.18)
    draw.rounded_rectangle((x, top, x + width, top + height), radius=4, fill="#c4212f" if country == "TR" else "#45627c")
    if country == "TR":
        cx, cy, radius = x + width * 0.41, top + height * 0.5, height * 0.27
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill="white")
        draw.ellipse((cx - radius * 0.55, cy - radius, cx + radius * 1.45, cy + radius), fill="#c4212f")
        points = []
        for point in range(10):
            angle = -math.pi / 2 + point * math.pi / 5
            reach = height * (0.16 if point % 2 == 0 else 0.07)
            points.append((x + width * 0.69 + math.cos(angle) * reach,
                           cy + math.sin(angle) * reach))
        draw.polygon(points, fill="white")
    else:
        draw.text((x + 4, top - 1), country, font=_font(max(12, size // 2), True), fill="white")


def _mixed_width(draw: ImageDraw.ImageDraw, value: str, font: ImageFont.ImageFont) -> int:
    width = 0.0
    emoji = _emoji_font(getattr(font, "size", 30))
    for kind, piece in _pieces(value):
        if kind == "flag":
            width += getattr(font, "size", 30) * 1.18
        else:
            selected = emoji if kind == "emoji" and emoji else font
            width += draw.textlength(piece, font=selected)
    return int(width)


def _fit(draw: ImageDraw.ImageDraw, value: str, font: ImageFont.ImageFont, max_width: int) -> str:
    if _mixed_width(draw, value, font) <= max_width:
        return value
    while value and _mixed_width(draw, value + "…", font) > max_width:
        value = value[:-1]
    return value + "…"


def _draw_mixed(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: str,
                font: ImageFont.ImageFont, fill: str) -> int:
    x, y = xy
    emoji = _emoji_font(getattr(font, "size", 30))
    for kind, piece in _pieces(value):
        if kind == "flag":
            _flag(draw, x, y, piece, getattr(font, "size", 30))
            x += int(getattr(font, "size", 30) * 1.18)
        elif kind == "emoji" and emoji:
            draw.text((x, y), piece, font=emoji, embedded_color=True)
            x += int(draw.textlength(piece, font=emoji))
        else:
            draw.text((x, y), piece, font=font, fill=fill)
            x += int(draw.textlength(piece, font=font))
    return x


def _badge(draw: ImageDraw.ImageDraw, x: int, y: int, size: int) -> None:
    center = size / 2
    points = []
    for index in range(16):
        angle = index * math.pi / 8 - math.pi / 2
        radius = size * (0.49 if index % 2 == 0 else 0.42)
        points.append((x + center + math.cos(angle) * radius,
                       y + center + math.sin(angle) * radius))
    draw.polygon(points, fill=BLUE)
    draw.line((x + size * 0.27, y + size * 0.51,
               x + size * 0.43, y + size * 0.66,
               x + size * 0.74, y + size * 0.34), fill="white", width=max(2, size // 9), joint="curve")


def _avatar(draw: ImageDraw.ImageDraw, image: Image.Image, avatar: Image.Image | None,
            x: int, y: int, size: int) -> None:
    if avatar:
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        image.paste(avatar.resize((size, size)), (x, y), mask)
    else:
        draw.ellipse((x, y, x + size, y + size), fill="#344452")
        draw.text((x + size * 0.31, y + size * 0.17), "X", font=_font(int(size * 0.55), True), fill=TEXT)


def _clean_text(value: str, has_quote: bool) -> str:
    value = value.strip()
    if has_quote:
        value = re.sub(r"\s*https://t\.co/[A-Za-z0-9]+\s*$", "", value)
    return value


def _quote_lines(draw: ImageDraw.ImageDraw, node: dict, level: int, width: int) -> list[str]:
    nested = isinstance(node.get("quote"), dict)
    value = _clean_text(str(node.get("text") or "")[:1600], nested)
    return _wrap(draw, value, _font(29 if level == 1 else 25), width - 48, 10 if level == 1 else 6)


def _quote_height(draw: ImageDraw.ImageDraw, node: dict, level: int, width: int) -> int:
    lines = _quote_lines(draw, node, level, width)
    height = (82 if level == 1 else 70) + len(lines) * (43 if level == 1 else 36) + 22
    nested = node.get("quote")
    if level < 2 and isinstance(nested, dict) and nested.get("text"):
        height += 16 + _quote_height(draw, nested, level + 1, width - 38)
    return height


def _draw_quote(draw: ImageDraw.ImageDraw, image: Image.Image, node: dict,
                avatars: list[Image.Image | None], level: int, x: int, y: int, width: int) -> int:
    height = _quote_height(draw, node, level, width)
    draw.rounded_rectangle((x, y, x + width, y + height), radius=22,
                           fill="#111a23" if level == 1 else "#0b1219", outline="#3d4a55", width=2)
    avatar_size = 56 if level == 1 else 43
    _avatar(draw, image, avatars[level] if len(avatars) > level else None, x + 20, y + 19, avatar_size)
    author = node.get("author") or {}
    name = str(author.get("name") or "X kullanıcısı")[:70]
    handle = str(author.get("screen_name") or "")[:60]
    name_font = _font(28 if level == 1 else 24, True)
    handle_font = _font(23 if level == 1 else 21)
    name_x = x + 20 + avatar_size + 12
    name = _fit(draw, name, name_font, width - avatar_size - 110)
    name_end = _draw_mixed(draw, (name_x, y + 15), name, name_font, TEXT)
    if author.get("verified") is True:
        _badge(draw, name_end + 6, y + 20, 24 if level == 1 else 20)
    draw.text((name_x, y + (47 if level == 1 else 40)), "@" + handle if handle else "X",
              font=handle_font, fill=MUTED)
    lines = _quote_lines(draw, node, level, width)
    text_y = y + (82 if level == 1 else 70)
    font = _font(29 if level == 1 else 25)
    step = 43 if level == 1 else 36
    for index, line in enumerate(lines):
        _draw_mixed(draw, (x + 22, text_y + index * step), line, font, TEXT)
    nested = node.get("quote")
    if level < 2 and isinstance(nested, dict) and nested.get("text"):
        nested_y = text_y + len(lines) * step + 14
        _draw_quote(draw, image, nested, avatars, level + 1, x + 19, nested_y, width - 38)
    return height


def _draw_stats(draw: ImageDraw.ImageDraw, post: dict, y: int) -> None:
    count_font = _font(28)
    # Draw the icons as shapes so Discord receives pixels, not missing emoji glyphs.
    draw.ellipse((92, y + 3, 124, y + 31), outline="#8cc7eb", width=3)
    draw.polygon(((98, y + 28), (96, y + 38), (109, y + 29)), fill="#8cc7eb")
    for dot in (100, 108, 116):
        draw.ellipse((dot, y + 16, dot + 3, y + 19), fill="#8cc7eb")
    draw.text((135, y - 2), _count(post.get("replies")), font=count_font, fill=MUTED)

    draw.rounded_rectangle((230, y + 3, 262, y + 34), radius=5, fill="#268fca")
    draw.line((236, y + 13, 255, y + 13, 251, y + 9), fill="white", width=3)
    draw.line((256, y + 23, 237, y + 23, 241, y + 27), fill="white", width=3)
    draw.text((272, y - 2), _count(post.get("reposts")), font=count_font, fill=MUTED)

    draw.ellipse((365, y + 7, 383, y + 25), fill="#ed3454")
    draw.ellipse((379, y + 7, 397, y + 25), fill="#ed3454")
    draw.polygon(((365, y + 18), (397, y + 18), (381, y + 37)), fill="#ed3454")
    draw.text((407, y - 2), _count(post.get("likes")), font=count_font, fill=MUTED)

    draw.ellipse((526, y + 9, 563, y + 30), outline="#c7a483", width=3)
    draw.ellipse((539, y + 11, 551, y + 27), fill="#8c5a38")
    draw.ellipse((543, y + 15, 547, y + 21), fill="#111820")
    draw.text((572, y - 2), _count(post.get("views")), font=count_font, fill=MUTED)


def render_story(post: dict, avatars: list[Image.Image | None], output: Path,
                 footer: str = "Discord Video Bot") -> None:
    author = post.get("author") or {}
    name = str(author.get("name") or author.get("display_name") or "X kullanıcısı")[:80]
    handle = str(author.get("screen_name") or author.get("username") or "")[:80]
    quote = post.get("quote") if isinstance(post.get("quote"), dict) and post["quote"].get("text") else None
    body = _clean_text(str(post.get("text") or "")[:3000], quote is not None)
    measure = ImageDraw.Draw(Image.new("RGB", (WIDTH, 100)))
    body_font = _font(36)
    body_lines = _wrap(measure, body, body_font, 896, 20)
    body_end = 190 + len(body_lines) * 51
    quote_top = body_end + 14
    quote_height = _quote_height(measure, quote, 1, 896) if quote else 0
    divider_y = quote_top + quote_height + 28 if quote else body_end + 30
    card_height = divider_y + 76 + 91
    height = max(1286, card_height + 140)
    card_top = 40 + max(0, (1150 - card_height) // 2)
    card_bottom = card_top + card_height

    image = Image.new("RGBA", (WIDTH, height), "#15212c")
    background = ImageDraw.Draw(image)
    for y in range(height):
        mix = y / max(height - 1, 1)
        background.line((0, y, WIDTH, y), fill=(int(49 - 34 * mix), int(63 - 45 * mix), int(78 - 56 * mix), 255))
    shadow_mask = Image.new("L", (WIDTH, height), 0)
    ImageDraw.Draw(shadow_mask).rounded_rectangle((44, card_top + 12, 1036, card_bottom + 15), radius=36, fill=135)
    shadow = Image.new("RGBA", (WIDTH, height), (0, 0, 0, 0))
    shadow.putalpha(shadow_mask.filter(ImageFilter.GaussianBlur(25)))
    image = Image.alpha_composite(image, shadow)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((40, card_top, 1040, card_bottom), radius=36, fill="#090e14", outline="#273541", width=2)

    _avatar(draw, image, avatars[0] if avatars else None, 92, card_top + 48, 96)
    name_font = _font(37, True)
    name = _fit(draw, name, name_font, 650)
    name_end = _draw_mixed(draw, (216, card_top + 58), name, name_font, TEXT)
    if author.get("verified") is True:
        _badge(draw, name_end + 9, card_top + 65, 28)
    draw.text((216, card_top + 108), "@" + handle if handle else "X", font=_font(29), fill=MUTED)
    draw.ellipse((925, card_top + 62, 987, card_top + 114), fill="#f0f3f5")
    draw.text((944, card_top + 65), "X", font=_font(29, True), fill="#090e14")

    for index, line in enumerate(body_lines):
        _draw_mixed(draw, (92, card_top + 190 + index * 51), line, body_font, TEXT)
    if quote:
        _draw_quote(draw, image, quote, avatars, 1, 92, card_top + quote_top, 896)

    divider = card_top + divider_y
    draw.line((92, divider, 988, divider), fill="#35424d", width=2)
    draw.text((92, divider + 23), _date_label(post.get("created_at")), font=_font(27), fill=MUTED)
    _draw_stats(draw, post, divider + 75)

    footer_font = _font(28, True)
    footer = _fit(draw, footer[:80], footer_font, 650)
    footer_width = _mixed_width(draw, footer, footer_font)
    footer_x = (WIDTH - footer_width - 55) // 2
    footer_y = height - 66
    draw.ellipse((footer_x, footer_y, footer_x + 39, footer_y + 39), fill="white")
    draw.line((footer_x + 19, footer_y + 7, footer_x + 19, footer_y + 25), fill="#101820", width=4)
    draw.polygon(((footer_x + 10, footer_y + 21), (footer_x + 28, footer_y + 21),
                  (footer_x + 19, footer_y + 31)), fill="#101820")
    draw.text((footer_x + 55, footer_y - 2), footer, font=footer_font, fill="white")
    image.convert("RGB").save(output, "PNG", optimize=True)
