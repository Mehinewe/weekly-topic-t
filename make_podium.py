"""Kahoot-style winners' podium image (Pillow).

    build_podium([("Amina", 6), ("Tom", 4), ("Lea", 3)], "Week of 2026-10-05", out_path)

Shows the top three (1st centre and tallest, 2nd left, 3rd right); with fewer
entries only those places appear. Names are first names only. Characters the
font can't draw (emoji, symbols) are dropped from the image; the caption that
goes with it keeps the real name and mention.
"""

import unicodedata

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1280, 800
BG_TOP, BG_BOTTOM = (94, 36, 168), (52, 16, 110)
GOLD, SILVER, BRONZE = (250, 189, 27), (186, 194, 206), (205, 127, 50)
WHITE, INK = (255, 255, 255), (48, 16, 96)

SLOTS = {1: (640, 330, GOLD), 2: (320, 230, SILVER), 3: (960, 190, BRONZE)}
BLOCK_WIDTH = 300
BASE_Y = 720


def _font(size):
    for name in ("arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def clean_name(name, limit=14):
    kept = "".join(ch for ch in name if unicodedata.category(ch)[0] in "LNPZM"
                   and ord(ch) < 0x2000 or ch == " ").strip()
    kept = " ".join(kept.split()) or "Player"
    return kept if len(kept) <= limit else kept[:limit - 1] + "…"


def _star(draw, cx, cy, radius, fill):
    import math
    points = []
    for i in range(10):
        r = radius if i % 2 == 0 else radius * 0.45
        angle = -math.pi / 2 + i * math.pi / 5
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    draw.polygon(points, fill=fill)


def build_podium(places, subtitle, out_path):
    """`places`: [(name, votes), ...] in rank order (only the first three are drawn)."""
    img = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        t = y / HEIGHT
        draw.line([(0, y), (WIDTH, y)],
                  fill=tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)))

    draw.text((WIDTH // 2, 62), "SHADOWING PODIUM", font=_font(64), fill=WHITE, anchor="mm")
    draw.text((WIDTH // 2, 118), subtitle, font=_font(30), fill=(214, 190, 255), anchor="mm")

    for place, (name, votes) in enumerate(places[:3], start=1):
        cx, height, colour = SLOTS[place]
        left, right = cx - BLOCK_WIDTH // 2, cx + BLOCK_WIDTH // 2
        top = BASE_Y - height
        draw.rounded_rectangle([left + 8, top + 8, right + 8, BASE_Y + 8], radius=18, fill=(30, 8, 66))
        draw.rounded_rectangle([left, top, right, BASE_Y], radius=18, fill=colour)
        draw.text((cx, top + 72), str(place), font=_font(100), fill=INK, anchor="mm")
        word = "vote" if votes == 1 else "votes"
        draw.text((cx, BASE_Y - 40), f"{votes} {word}", font=_font(34), fill=INK, anchor="mm")
        name_y = top - 44
        draw.text((cx, name_y), clean_name(name), font=_font(52 if place == 1 else 44),
                  fill=WHITE, anchor="mm")
        if place == 1:
            _star(draw, cx, name_y - 66, 34, GOLD)

    draw.rectangle([0, BASE_Y, WIDTH, HEIGHT], fill=(30, 8, 66))
    img.save(out_path)
    return out_path


if __name__ == "__main__":
    build_podium([("Amina", 6), ("Tom", 4), ("Lea", 3)], "Week of 2026-10-05", "podium_preview.png")
    print("saved podium_preview.png")
