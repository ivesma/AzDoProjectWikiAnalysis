"""Generate assets/app.ico (multi-resolution) with Pillow.

Run from the repository root:  python installer/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "assets" / "app.ico"
SIZES = [16, 24, 32, 48, 64, 128, 256]
ACCENT = (15, 108, 189, 255)
WHITE = (255, 255, 255, 255)
PALE = (199, 224, 244, 255)


def draw(size: int = 1024) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 256
    d.rounded_rectangle((8 * s, 8 * s, 248 * s, 248 * s), radius=48 * s, fill=ACCENT)

    # Open book
    d.polygon([(40 * s, 70 * s), (124 * s, 84 * s), (124 * s, 196 * s), (40 * s, 182 * s)], fill=WHITE)
    d.polygon([(132 * s, 84 * s), (216 * s, 70 * s), (216 * s, 182 * s), (132 * s, 196 * s)], fill=PALE)
    for i, y in enumerate((104, 124, 144)):
        width = 60 if i < 2 else 44
        d.line([(56 * s, (y - 2) * s), ((56 + width) * s, (y + 6) * s)], fill=ACCENT, width=int(8 * s))

    # Magnifying glass over the right page
    cx, cy, r = 176 * s, 128 * s, 26 * s
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=ACCENT, width=int(10 * s))
    d.line([(cx + r * 0.7, cy + r * 0.7), (cx + r * 1.8, cy + r * 1.8)], fill=ACCENT, width=int(14 * s))
    return img


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    draw().save(TARGET, format="ICO", sizes=[(n, n) for n in SIZES])
    print(f"Wrote {TARGET}")


if __name__ == "__main__":
    main()
