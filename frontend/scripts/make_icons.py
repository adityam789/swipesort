"""Render the PWA icons (public/icon-*.png) with Pillow.

Run from the repo root: `cd backend && uv run python ../frontend/scripts/make_icons.py`.
Design mirrors public/icon.svg: two stacked cards on emerald, the top one tilted mid-fling.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

PUBLIC = Path(__file__).resolve().parents[1] / "public"
BG = (5, 150, 105, 255)  # emerald-600
BACK = (167, 243, 208, 255)  # emerald-200
FRONT = (255, 255, 255, 255)
SS = 4  # supersampling


def card(size: int, box: tuple[float, float, float, float], fill: tuple[int, ...],
         angle: float) -> Image.Image:
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle(box, radius=size * 0.05, fill=fill)
    return layer.rotate(angle, resample=Image.BICUBIC, center=(size / 2, size / 2))


def render(px: int, maskable: bool) -> Image.Image:
    n = px * SS
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if maskable:  # full bleed; content stays inside the 80% safe zone
        d.rectangle((0, 0, n, n), fill=BG)
        scale = 0.72
    else:
        d.rounded_rectangle((0, 0, n - 1, n - 1), radius=n * 0.22, fill=BG)
        scale = 0.9
    c = n / 2
    w, h = n * 0.36 * scale, n * 0.48 * scale
    box = (c - w / 2, c - h / 2, c + w / 2, c + h / 2)
    img.alpha_composite(card(n, box, BACK, 10))
    shifted = (box[0] + n * 0.06 * scale, box[1] - n * 0.02 * scale,
               box[2] + n * 0.06 * scale, box[3] - n * 0.02 * scale)
    img.alpha_composite(card(n, shifted, FRONT, -12))
    return img.resize((px, px), Image.LANCZOS)


def main() -> None:
    for name, px, maskable in [
        ("icon-192.png", 192, False), ("icon-512.png", 512, False),
        ("icon-maskable-512.png", 512, True), ("apple-touch-icon.png", 180, True),
    ]:
        render(px, maskable).save(PUBLIC / name, optimize=True)
        print("wrote", PUBLIC / name)


if __name__ == "__main__":
    main()
