"""Build scripts/assets/vayren_desktop.ico: the Desktop-shortcut candlestick icon.

Deterministic vector-style drawing (no randomness, no font, no text). Three
candlesticks on a dark rounded tile, drawn at a supersampled master size and
downscaled per icon size. A bolder stroke weight is used for the small sizes
where the fine wicks would disappear.

The application's own icon (``vayren.ico``) is a different asset and is not
touched here — this file only feeds the Desktop shortcut.

Run: ``python scripts/assets/make_desktop_icon.py``
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from PIL import IcoImagePlugin, Image, ImageDraw

OUT = Path(__file__).resolve().parent / "vayren_desktop.ico"
MASTER = 2048

TILE_TOP = (21, 26, 36)
TILE_BOTTOM = (10, 13, 18)
UP = (62, 207, 142)
DOWN = (246, 82, 108)

# Candlesticks as fractions of the tile: (centre x, body top, body bottom,
# wick above the body, wick below the body). Up = mint, down = rose; the close
# rises left to right.
CANDLES: tuple[tuple[float, float, float, float, float, tuple[int, int, int]], ...] = (
    (0.300, 0.450, 0.700, 0.150, 0.080, UP),
    (0.500, 0.250, 0.680, 0.050, 0.080, DOWN),
    (0.700, 0.270, 0.500, 0.050, 0.160, UP),
)

SMALL_SIZES = (16, 24, 32)
LARGE_SIZES = (48, 64, 128, 256)
ICON_SIZES = SMALL_SIZES + LARGE_SIZES


# The small frames get their own weight table: a 16 px tile cannot carry a
# 0.115 body and a hairline wick, so the bold pass widens both and caps how far
# a wick may stick out.
@dataclass(frozen=True)
class Weight:
    """Proportions of one rendering pass, as fractions of the tile."""

    body: float
    wick: float
    corner: float
    cap: float | None


FINE = Weight(body=0.115, wick=0.030, corner=0.24, cap=None)
BOLD = Weight(body=0.150, wick=0.055, corner=0.16, cap=0.055)


def tile(size: int) -> Image.Image:
    """Rounded dark tile with a top-to-bottom shade, no border, no gloss."""
    image = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(image)
    for y in range(size):
        step = y / max(size - 1, 1)
        shade = tuple(int(TILE_TOP[c] + (TILE_BOTTOM[c] - TILE_TOP[c]) * step) for c in range(3))
        draw.line((0, y, size, y), fill=(*shade, 255))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=int(size * 0.215), fill=255
    )
    image.putalpha(mask)
    return image


def draw_candles(image: Image.Image, weight: Weight) -> None:
    """Paint the three candles onto a master-sized image."""
    size = image.width
    draw = ImageDraw.Draw(image)
    wick = max(1.0, size * weight.wick)
    half_body = size * weight.body / 2
    cap = weight.cap if weight.cap is not None else float("inf")
    for centre, body_top, body_bottom, above, below, color in CANDLES:
        x = size * centre
        wick_top = size * (body_top - min(above, cap))
        wick_bottom = size * (body_bottom + min(below, cap))
        draw.line(
            (x, wick_top, x, wick_bottom),
            fill=(*color, 255),
            width=int(round(wick)),
        )
        draw.rounded_rectangle(
            (x - half_body, size * body_top, x + half_body, size * body_bottom),
            radius=int(size * weight.body * weight.corner),
            fill=(*color, 255),
        )


def render(master: int, weight: Weight) -> Image.Image:
    image = tile(master)
    draw_candles(image, weight)
    return image


def build() -> list[Image.Image]:
    """One frame per icon size, largest first."""
    fine = render(MASTER, FINE)
    bold = render(MASTER, BOLD)
    resample = Image.Resampling.LANCZOS
    frames = {size: bold.resize((size, size), resample) for size in SMALL_SIZES}
    frames.update((size, fine.resize((size, size), resample)) for size in LARGE_SIZES)
    if set(frames) != set(ICON_SIZES):
        raise SystemExit(f"icon size table drift: {sorted(frames)}")
    return [frames[size] for size in sorted(frames, reverse=True)]


def main() -> None:
    frames = build()
    frames[0].save(
        OUT,
        format="ICO",
        sizes=tuple((frame.width, frame.height) for frame in frames),
        append_images=frames[1:],
    )
    with Image.open(OUT) as handle:
        saved = cast("IcoImagePlugin.IcoImageFile", handle)
        sizes = sorted(saved.ico.sizes())
    print(f"{OUT.name}: {sizes} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
