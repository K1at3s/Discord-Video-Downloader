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
        if code >= 0x1F300 or 0x2600 <= code <= 0x27BF or char in "ℹ❤♥⭐✅☑":
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
    # Twitter/X-like blue verified badge: 8-lobed scalloped seal with white check.
    badge_blue = "#1D9BF0"
    cx = x + size / 2
    cy = y + size / 2
    points = []
    lobes = 8
    for i in range(lobes * 2):
        angle = (i * math.pi / lobes) - math.pi / 2
        radius = size * (0.49 if i % 2 == 0 else 0.40)
        points.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
    draw.polygon(points, fill=badge_blue)
    stroke = max(2, round(size * 0.17))
    draw.line((x + size * 0.28, y + size * 0.52,
               x + size * 0.44, y + size * 0.66,
               x + size * 0.73, y + size * 0.36), fill="white", width=stroke, joint="curve")


def _badge_for_text(draw: ImageDraw.ImageDraw, x: int, text_y: int, font: ImageFont.ImageFont,
                    scale: float = 0.62, y_offset: int = 0) -> None:
    bbox = draw.textbbox((0, 0), "Ag", font=font)
    text_h = bbox[3] - bbox[1]
    size = max(16, int(getattr(font, "size", 28) * scale))
    badge_y = int(text_y + (text_h - size) / 2 + bbox[1] + y_offset)
    _badge(draw, int(x), badge_y, size)


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
    draw.rounded_rectangle((x, y, x + width, y + height), radius=24,
                           fill="#172635" if level == 1 else "#101d2b", outline="#3b5a6c", width=2)
    draw.rounded_rectangle((x + 14, y + (86 if level == 1 else 73),
                            x + 18, y + height - 20), radius=2,
                           fill="#61c8c4" if level == 1 else "#7d8ddb")
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
        _badge_for_text(draw, name_end + 8, y + 15, name_font, scale=0.80 if level == 1 else 0.74, y_offset=-1)
    draw.text((name_x, y + (47 if level == 1 else 40)), "@" + handle if handle else "X",
              font=handle_font, fill=MUTED)
    lines = _quote_lines(draw, node, level, width)
    text_y = y + (82 if level == 1 else 70)
    font = _font(29 if level == 1 else 25)
    step = 43 if level == 1 else 36
    for index, line in enumerate(lines):
        _draw_mixed(draw, (x + 30, text_y + index * step), line, font, TEXT)
    nested = node.get("quote")
    if level < 2 and isinstance(nested, dict) and nested.get("text"):
        nested_y = text_y + len(lines) * step + 14
        _draw_quote(draw, image, nested, avatars, level + 1, x + 19, nested_y, width - 38)
    return height


def _draw_stat_count(draw: ImageDraw.ImageDraw, text: str, x: int, center_y: int,
                     font: ImageFont.ImageFont, fill: str = MUTED) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    text_h = bbox[3] - bbox[1]
    text_w = bbox[2] - bbox[0]
    draw.text((x, int(center_y - text_h / 2 - bbox[1])), text, font=font, fill=fill)
    return int(x + text_w)


def _draw_stats(draw: ImageDraw.ImageDraw, post: dict, y: int) -> None:
    count_font = _font(28)
    # Keep icons and counters on a shared vertical center with proportional spacing.
    center_y = y + 20
    x = 92
    gap_icon_text = 10
    gap_group = 54

    # replies (speech bubble)
    draw.ellipse((x, center_y - 15, x + 32, center_y + 13), outline="#8cc7eb", width=3)
    draw.polygon(((x + 6, center_y + 10), (x + 4, center_y + 20), (x + 17, center_y + 11)), fill="#8cc7eb")
    for dot in (x + 8, x + 16, x + 24):
        draw.ellipse((dot, center_y - 2, dot + 3, center_y + 1), fill="#8cc7eb")
    x = _draw_stat_count(draw, _count(post.get("replies")), x + 32 + gap_icon_text, center_y, count_font) + gap_group

    # reposts
    draw.rounded_rectangle((x, center_y - 15, x + 32, center_y + 16), radius=5, fill="#268fca")
    draw.line((x + 6, center_y - 5, x + 25, center_y - 5, x + 21, center_y - 9), fill="white", width=3)
    draw.line((x + 26, center_y + 5, x + 7, center_y + 5, x + 11, center_y + 9), fill="white", width=3)
    x = _draw_stat_count(draw, _count(post.get("reposts")), x + 32 + gap_icon_text, center_y, count_font) + gap_group

    # likes (heart)
    draw.ellipse((x, center_y - 11, x + 18, center_y + 7), fill="#ed3454")
    draw.ellipse((x + 14, center_y - 11, x + 32, center_y + 7), fill="#ed3454")
    draw.polygon(((x, center_y), (x + 32, center_y), (x + 16, center_y + 19)), fill="#ed3454")
    x = _draw_stat_count(draw, _count(post.get("likes")), x + 32 + gap_icon_text, center_y, count_font) + gap_group

    # views (eye)
    draw.ellipse((x, center_y - 10, x + 37, center_y + 11), outline="#c7a483", width=3)
    draw.ellipse((x + 13, center_y - 8, x + 25, center_y + 8), fill="#8c5a38")
    draw.ellipse((x + 17, center_y - 4, x + 21, center_y + 2), fill="#111820")
    _draw_stat_count(draw, _count(post.get("views")), x + 37 + gap_icon_text, center_y, count_font)


def _story_background(height: int, card_top: int, card_bottom: int) -> Image.Image:
    """Paint a soft aurora frame around the post, without busy linework."""
    image = Image.new("RGBA", (WIDTH, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        progress = y / max(height - 1, 1)
        draw.line((0, y, WIDTH, y),
                  fill=(int(11 + 8 * progress), int(18 + 10 * progress),
                        int(31 + 12 * progress), 255))

    glows = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glows)
    glow_draw.ellipse((WIDTH - 470, -250, WIDTH + 330, 650), fill=(35, 155, 165, 140))
    glow_draw.ellipse((-490, card_bottom - 430, 500, card_bottom + 480), fill=(95, 65, 157, 140))
    glow_draw.ellipse((230, card_top - 160, 980, card_top + 500), fill=(31, 87, 133, 70))
    return Image.alpha_composite(image, glows.filter(ImageFilter.GaussianBlur(135)))


def _card_surface(image: Image.Image, top: int, bottom: int) -> None:
    """Paint the post on a restrained slate surface with a luminous header."""
    panel_height = bottom - top
    panel = Image.new("RGBA", (1000, panel_height))
    panel_draw = ImageDraw.Draw(panel)
    for y in range(panel_height):
        progress = y / max(panel_height - 1, 1)
        panel_draw.line((0, y, 999, y),
                        fill=(int(20 - 8 * progress), int(31 - 12 * progress),
                              int(45 - 17 * progress), 255))
    for y in range(min(170, panel_height)):
        shade = y / 170
        panel_draw.line((0, y, 999, y),
                        fill=(int(24 - 4 * shade), int(39 - 8 * shade),
                              int(55 - 10 * shade), 255))
    mask = Image.new("L", panel.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 999, panel_height - 1), radius=38, fill=255)
    image.paste(panel, (40, top), mask)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((40, top, 1040, bottom), radius=38,
                           outline="#486777", width=2)
    draw.rounded_rectangle((84, top + 21, 266, top + 27), radius=3, fill="#60d2cb")
    draw.rounded_rectangle((274, top + 21, 318, top + 27), radius=3, fill="#828bd4")
    draw.line((92, top + 160, 988, top + 160), fill="#304959", width=2)


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
    height = max(1286, card_height + 190)
    card_top = 40 + max(0, (1150 - card_height) // 2)
    card_bottom = card_top + card_height

    image = _story_background(height, card_top, card_bottom)
    shadow_mask = Image.new("L", (WIDTH, height), 0)
    ImageDraw.Draw(shadow_mask).rounded_rectangle((44, card_top + 16, 1036, card_bottom + 20), radius=38, fill=175)
    shadow = Image.new("RGBA", (WIDTH, height), (0, 0, 0, 0))
    shadow.putalpha(shadow_mask.filter(ImageFilter.GaussianBlur(37)))
    image = Image.alpha_composite(image, shadow)
    draw = ImageDraw.Draw(image)
    _card_surface(image, card_top, card_bottom)
    draw = ImageDraw.Draw(image)

    _avatar(draw, image, avatars[0] if avatars else None, 92, card_top + 48, 96)
    name_font = _font(37, True)
    handle_font = _font(29)
    name_y = card_top + 56
    handle_y = card_top + 106
    name = _fit(draw, name, name_font, 650)
    name_end = _draw_mixed(draw, (216, name_y), name, name_font, TEXT)
    if author.get("verified") is True:
        _badge_for_text(draw, name_end + 10, name_y, name_font, scale=0.86, y_offset=-2)
    draw.text((216, handle_y), "@" + handle if handle else "X", font=handle_font, fill=MUTED)
    cx1, cy1, cx2, cy2 = 925, card_top + 59, 989, card_top + 123
    draw.ellipse((cx1, cy1, cx2, cy2), fill="#122734", outline="#6cbfc5", width=2)
    x_font = _font(29, True)
    x_text = "X"
    x_bbox = draw.textbbox((0, 0), x_text, font=x_font)
    x_w = x_bbox[2] - x_bbox[0]
    x_h = x_bbox[3] - x_bbox[1]
    x_x = cx1 + (cx2 - cx1 - x_w) / 2 - x_bbox[0]
    x_y = cy1 + (cy2 - cy1 - x_h) / 2 - x_bbox[1]
    draw.text((x_x, x_y), x_text, font=x_font, fill="#e2f5f4")

    for index, line in enumerate(body_lines):
        _draw_mixed(draw, (92, card_top + 190 + index * 51), line, body_font, TEXT)
    if quote:
        _draw_quote(draw, image, quote, avatars, 1, 92, card_top + quote_top, 896)

    divider = card_top + divider_y
    draw.line((92, divider, 988, divider), fill="#35424d", width=2)
    draw.text((92, divider + 23), _date_label(post.get("created_at")), font=_font(27), fill=MUTED)
    _draw_stats(draw, post, divider + 67)

    footer_font = _font(24, True)
    footer = _fit(draw, footer[:80], footer_font, 650)
    footer_width = _mixed_width(draw, footer, footer_font)
    pill_width = footer_width + 100
    footer_x = (WIDTH - pill_width) // 2
    footer_y = height - 91
    draw.rounded_rectangle((footer_x, footer_y, footer_x + pill_width, footer_y + 56),
                           radius=28, fill="#132735", outline="#416b79", width=2)
    draw.ellipse((footer_x + 21, footer_y + 20, footer_x + 37, footer_y + 36), fill="#69d4cb")
    draw.text((footer_x + 55, footer_y + 11), footer, font=footer_font, fill="#e2edef")
    image.convert("RGB").save(output, "PNG", optimize=True)
