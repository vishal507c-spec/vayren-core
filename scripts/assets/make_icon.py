"""Build scripts/assets: generate vayren.ico app icon (V mark on dark slate).

Run:  python scripts/assets/make_icon.py
"""

import struct
import zlib
from pathlib import Path

SIZE = 256
OUT = Path(__file__).resolve().parents[2] / "scripts" / "assets" / "vayren.ico"

BG = (18, 22, 30, 255)
FG = (64, 196, 255, 255)


def render_v(size: int) -> list[list[int]]:
    """Rasterize a chunky V stroke over a rounded square background."""
    img = [[0] * size for _ in range(size)]
    r = size // 5
    for y in range(size):
        for x in range(size):
            cx, cy = x - size // 2, y - size // 2
            corner = max(0, r - _edge_dist(cx, cy, size, r))
            bg = corner == 0
            if bg:
                img[y][x] = 1

    # V stroke: two thick diagonals meeting at bottom center
    t = size / 16  # stroke thickness
    top = size * 0.30
    bot = size * 0.76
    half = size * 0.14
    center = size * 0.52
    for y in range(size):
        for x in range(size):
            f = (y - top) / (bot - top)
            if 0.0 <= f <= 1.0:
                # left arm
                lx = center - half * (1.0 - f)
                if abs(x - lx) <= t and x < center:
                    img[y][x] = 2
                # right arm
                rx = center + half * (1.0 - f)
                if abs(x - rx) <= t and x >= center:
                    img[y][x] = 2
    return img


def _edge_dist(cx: int, cy: int, size: int, r: int) -> int:
    """Distance outside the rounded-rect (0 = inside)."""
    half = size / 2
    dx = max(abs(cx) - (half - r), 0)
    dy = max(abs(cy) - (half - r), 0)
    return round((dx * dx + dy * dy) ** 0.5) - r


def png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))


def encode_png(img: list[list[int]]) -> bytes:
    size = len(img)
    pal = {0: (0, 0, 0, 0), 1: BG, 2: FG}
    raw = b"".join(
        b"\x00" + b"".join(struct.pack("4B", *pal[img[y][x]]) for x in range(size))
        for y in range(size)
    )
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", zlib.compress(raw, 9))
        + png_chunk(b"IEND", b"")
    )


def encode_ico(pngs: dict[int, bytes]) -> bytes:
    count = len(pngs)
    header = struct.pack("<HHH", 0, 1, count)
    entries = b""
    offset = 6 + 16 * count
    for size, data in pngs.items():
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return header + entries + b"".join(pngs.values())


def main() -> None:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    ico = encode_ico({s: encode_png(render_v(s)) for s in sizes})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(ico)
    print(f"icon written: {OUT} ({len(ico)} bytes)")


if __name__ == "__main__":
    main()
