"""Crop and magnify a region of a render probe so a detail can be inspected.

A 28px control in a 1024px-wide screenshot is a few pixels of guesswork;
cropping it and scaling it up is the only honest way to tell "the glyph is
missing" from "the glyph is faint".

Usage:
    python tools/crop_render.py <png> <x> <y> <w> <h> [--scale N] [--out out.png]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("png", type=Path)
    ap.add_argument("x", type=int)
    ap.add_argument("y", type=int)
    ap.add_argument("w", type=int)
    ap.add_argument("h", type=int)
    ap.add_argument("--scale", type=int, default=8)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    image = Image.open(args.png).convert("RGB")
    crop = image.crop((args.x, args.y, args.x + args.w, args.y + args.h))
    crop = crop.resize((crop.width * args.scale, crop.height * args.scale), Image.NEAREST)
    out = args.out or args.png.with_name(f"{args.png.stem}_crop{args.x}x{args.y}.png")
    crop.save(out)
    print(f"{args.png.name} [{args.x},{args.y} {args.w}x{args.h}] x{args.scale} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())