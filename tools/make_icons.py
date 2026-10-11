"""Build the browser and home-screen icons in app/static/icons/.

A maple leaf (Noto Color Emoji, Apache-2.0) on a rounded square in the
kid screens' accent colour. Run it again after changing the design:

    python tools/make_icons.py [path/to/NotoColorEmoji.ttf]
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ACCENT = (31, 122, 112, 255)  # --accent in app/static/kid/style.css
LEAF = "\U0001F341"
EMOJI_SIZE = 109  # the only size the Noto colour bitmaps come in
OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "icons"
DEFAULT_FONT = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"


def leaf(font_path: str) -> Image.Image:
    font = ImageFont.truetype(font_path, EMOJI_SIZE)
    canvas = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
    ImageDraw.Draw(canvas).text((80, 80), LEAF, font=font, embedded_color=True, anchor="mm")
    return canvas.crop(canvas.getbbox())


def tile(size: int, glyph: Image.Image) -> Image.Image:
    big = size * 4  # draw large, then shrink, for smooth edges
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle((0, 0, big - 1, big - 1), radius=big // 5, fill=ACCENT)
    scale = big * 0.74 / max(glyph.size)
    g = glyph.resize((round(glyph.width * scale), round(glyph.height * scale)), Image.Resampling.LANCZOS)
    img.alpha_composite(g, ((big - g.width) // 2, (big - g.height) // 2))
    return img.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    glyph = leaf(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_FONT)
    OUT.mkdir(parents=True, exist_ok=True)
    tile(64, glyph).save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    tile(180, glyph).convert("RGB").save(OUT / "apple-touch-icon.png", optimize=True)
    tile(192, glyph).save(OUT / "icon-192.png", optimize=True)
    print("Wrote", ", ".join(sorted(p.name for p in OUT.iterdir())))


if __name__ == "__main__":
    main()
