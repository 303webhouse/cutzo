"""The New Heretics' episode title graphic: up to three lines of text under the show's name.

Measured in JW Ghost Stories at 1080p (Oct 15, 2026), where Nick's Premiere graphic read
"HALLOWEEN SPECIAL:" / "JW GHOST STORIES" / "ft. Two exJWs":

    line 1, the label    Montserrat ExtraBold, 54.6 px, capitals, baseline at y 512
    line 2, the title    Montserrat SemiBold, 53.1 px, capitals, baseline at y 576
    line 3, the guests   Montserrat SemiBold, 37.1 px, baseline at y 634, right-aligned with the
                         wider of lines 1 and 2

(Fonts as named in Nick's Premiere project; sizes fitted to the widths and stroke thickness of the
lines in the finished frame, since his text was set slightly tighter than the font's own spacing.)

Lines 1 and 2 start at x 958 (just left of the middle of the frame), in cream. "THE NEW HERETICS"
above them is part of the background video. Montserrat is a free font (SIL Open Font License); it
ships with Cutzo in cutzo/assets/fonts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

FONTS = Path(__file__).resolve().parent.parent / "assets" / "fonts"
BOLD = FONTS / "Montserrat-ExtraBold.ttf"
SEMIBOLD = FONTS / "Montserrat-SemiBold.ttf"


@dataclass(frozen=True)
class TitleStyle:
    colour: tuple[int, int, int] = (236, 220, 198)
    size: float = 54.6  # line 1, pixels at 1080p
    title_size: float = 53.1
    guests_size: float = 37.1
    left: float = 958.0
    baselines: tuple[float, float, float] = (512.0, 576.0, 634.0)
    right_limit: float = 1872.0  # long titles shrink to stay inside the frame


def lines_for(ep: dict[str, Any]) -> dict[str, str]:
    """The three lines for an episode: what Nick set, or a guess from the episode's title
    ("Halloween Special: JW Ghost Stories" -> label "HALLOWEEN SPECIAL:", title "JW GHOST STORIES")."""
    saved = ep.get("video_title") or {}
    if saved:
        return {"label": saved.get("label", ""), "title": saved.get("title", ""), "guests": saved.get("guests", "")}
    title = " ".join(str(ep.get("title") or "").split())
    if ":" in title:
        label, rest = title.split(":", 1)
        return {"label": label.strip() + ":", "title": rest.strip(), "guests": ""}
    return {"label": "", "title": title, "guests": ""}


def render(lines: dict[str, str], dst: Path, *, width: int = 1920, height: int = 1080,
           style: TitleStyle = TitleStyle()) -> tuple[int, int, int, int] | None:
    """Draw the graphic as a see-through PNG cropped to the text. Returns (x, y, w, h) of the PNG in
    the frame, or None when there's no text."""
    from PIL import Image, ImageDraw, ImageFont

    label = (lines.get("label") or "").strip().upper()
    title = (lines.get("title") or "").strip().upper()
    guests = (lines.get("guests") or "").strip()
    if not (label or title or guests):
        return None
    k = height / 1080.0  # sizes were measured at 1080p
    size, mid, small = style.size * k, style.title_size * k, style.guests_size * k
    left = style.left * k
    b1, b2, b3 = (b * k for b in style.baselines)

    def font(path: Path, px: float) -> Any:
        return ImageFont.truetype(str(path), size=max(1, int(round(px))))

    # Shrink everything together if the longest line would run off the right of the frame.
    def advance(text: str, path: Path, px: float) -> float:
        return font(path, px).getlength(text) if text else 0.0

    title_size = mid if label else size  # a title on its own takes the label's place and size
    widest = max(advance(label, BOLD, size), advance(title, SEMIBOLD, title_size))
    room = style.right_limit * k - left
    shrink = min(1.0, room / widest) if widest > 0 else 1.0
    if shrink < 1.0:
        size, mid, small, title_size = size * shrink, mid * shrink, small * shrink, title_size * shrink
        widest *= shrink
        top = b1 - size * 0.72
        b1, b2, b3 = top + (b1 - top) * shrink, top + (b2 - top) * shrink, top + (b3 - top) * shrink
    rows: list[tuple[str, Path, float, float, str]] = []  # text, font, size, baseline, anchor
    if label and title:
        rows += [(label, BOLD, size, b1, "ls"), (title, SEMIBOLD, mid, b2, "ls")]
        guest_base = b3
    elif label or title:  # one line: it takes the first line's place, the guests move up
        rows.append((label, BOLD, size, b1, "ls") if label else (title, SEMIBOLD, title_size, b1, "ls"))
        guest_base = b1 + (b3 - b2)
    else:
        guest_base = b1
    right = left + (widest if widest > 0 else advance(guests, SEMIBOLD, small))
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    fill = (*style.colour, 255)
    for text, path, px, base, anchor in rows:
        draw.text((left, base), text, font=font(path, px), fill=fill, anchor=anchor)
    if guests:
        draw.text((right, guest_base), guests, font=font(SEMIBOLD, small), fill=fill, anchor="rs")
    box = canvas.getbbox()
    if not box:
        return None
    x0, y0, x1, y1 = box
    x0, y0 = max(0, x0 - 2) & ~1, max(0, y0 - 2) & ~1  # even positions and sizes suit FFmpeg
    x1, y1 = min(width, x1 + 2), min(height, y1 + 2)
    w, h = (x1 - x0) + ((x1 - x0) & 1), (y1 - y0) + ((y1 - y0) & 1)
    canvas.crop((x0, y0, x0 + w, y0 + h)).save(dst)
    return x0, y0, w, h
