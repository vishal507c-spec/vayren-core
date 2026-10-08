"""Convert Slint's PPM render probes into PNGs (and a contact sheet).

The render tests write raw PPM because the Rust side has no image encoder
dependency. This turns them into files a human (or an agent) can actually
look at, which is the whole point: a layout claim is only evidence once the
pixels have been seen.

Usage:
    python tools/ppm_to_png.py <ppm-dir> [--sheet out.png] [--scale N]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover - depends on the local interpreter
    sys.exit("Pillow is required: python -m pip install pillow")


def read_ppm(path: Path) -> Image.Image:
    with path.open("rb") as handle:
        header = handle.readline().strip()
        if header != b"P6":
            raise ValueError(f"{path.name}: not a binary PPM ({header!r})")
        fields: list[int] = []
        while len(fields) < 3:
            line = handle.readline()
            if line.startswith(b"#"):
                continue
            fields += [int(token) for token in line.split()]
        width, height, _maxval = fields
        raw = handle.read(width * height * 3)
        if len(raw) < width * height * 3:
            raise ValueError(f"{path.name}: truncated raster")
    return Image.frombytes("RGB", (width, height), raw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ppm_dir", type=Path)
    parser.add_argument("--sheet", type=Path, help="write a stacked contact sheet here")
    parser.add_argument("--scale", type=float, default=1.0, help="downscale factor (1.0 = as-is)")
    parser.add_argument("--only", nargs="*", help="only convert these stems")
    args = parser.parse_args()

    if not args.ppm_dir.is_dir():
        sys.exit(f"not a directory: {args.ppm_dir}")

    sources = sorted(args.ppm_dir.glob("*.ppm"))
    if args.only:
        wanted = set(args.only)
        sources = [path for path in sources if path.stem in wanted]
    if not sources:
        sys.exit(f"no .ppm files under {args.ppm_dir}")

    written: list[Path] = []
    for path in sources:
        image = read_ppm(path)
        if args.scale != 1.0:
            image = image.resize(
                (max(1, int(image.width * args.scale)), max(1, int(image.height * args.scale))),
                Image.LANCZOS,
            )
        out = path.with_suffix(".png")
        image.save(out)
        written.append(out)
        print(f"{path.name} -> {out.name} ({image.width}x{image.height})")

    if args.sheet:
        # Stack scaled copies vertically, largest first, so the whole desktop
        # size ladder is readable in one glance.
        tiles = []
        for path in sorted(written, key=lambda p: p.stat().st_size):
            tile = Image.open(path)
            if tile.width > 1400:
                tile = tile.resize((1400, int(tile.height * 1400 / tile.width)), Image.LANCZOS)
            tiles.append((path.stem, tile))
        if not tiles:
            sys.exit("nothing to stack")
        gap = 24
        width = max(tile.width for _, tile in tiles)
        height = sum(tile.height for _, tile in tiles) + gap * (len(tiles) - 1)
        sheet = Image.new("RGB", (width, height), (255, 0, 255))
        y = 0
        for _name, tile in tiles:
            sheet.paste(tile, (0, y))
            y += tile.height + gap
        sheet.save(args.sheet)
        print(f"contact sheet -> {args.sheet} ({sheet.width}x{sheet.height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())