from __future__ import annotations

import argparse
import struct
import zlib
from pathlib import Path


COLORS = {
    "bg": (9, 13, 20, 255),
    "panel": (21, 31, 45, 255),
    "blue": (99, 179, 255, 255),
    "green": (73, 209, 141, 255),
    "ink": (248, 251, 255, 255),
}


def _canvas(size: int) -> bytearray:
    return bytearray(COLORS["bg"] * (size * size))


def _rect(pixels: bytearray, size: int, x0: float, y0: float, x1: float, y1: float, color: tuple[int, ...]) -> None:
    left, top = max(0, int(x0 * size)), max(0, int(y0 * size))
    right, bottom = min(size, int(x1 * size)), min(size, int(y1 * size))
    for y in range(top, bottom):
        row = (y * size + left) * 4
        for _x in range(left, right):
            pixels[row:row + 4] = bytes(color)
            row += 4


def render_mark(size: int) -> bytes:
    pixels = _canvas(size)
    # Film-frame rails and timecode ticks.
    _rect(pixels, size, .12, .12, .88, .18, COLORS["blue"])
    _rect(pixels, size, .12, .82, .88, .88, COLORS["blue"])
    for x in (.18, .32, .46, .60, .74):
        _rect(pixels, size, x, .12, x + .035, .24, COLORS["bg"])
        _rect(pixels, size, x, .76, x + .035, .88, COLORS["bg"])
    # A cinematic C aperture.
    _rect(pixels, size, .20, .26, .30, .72, COLORS["ink"])
    _rect(pixels, size, .20, .26, .66, .35, COLORS["ink"])
    _rect(pixels, size, .20, .63, .66, .72, COLORS["ink"])
    # Subtitle lines form the forward edge of the mark.
    _rect(pixels, size, .42, .43, .80, .50, COLORS["green"])
    _rect(pixels, size, .50, .54, .74, .61, COLORS["blue"])
    return _encode_png(size, size, pixels)


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def _encode_png(width: int, height: int, pixels: bytes) -> bytes:
    rows = b"".join(b"\x00" + pixels[y * width * 4:(y + 1) * width * 4] for y in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(rows, 9))
        + _chunk(b"IEND", b"")
    )


def _encode_ico(images: list[tuple[int, bytes]]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + len(images) * 16
    entries = bytearray()
    payload = bytearray()
    for size, png in images:
        encoded_size = 0 if size == 256 else size
        entries += struct.pack("<BBBBHHII", encoded_size, encoded_size, 0, 0, 1, 32, len(png), offset)
        payload += png
        offset += len(png)
    return header + entries + payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate CineSub Studio brand assets without external libraries.")
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.project_root.resolve()
    desktop_build = root / "desktop" / "build"
    web_assets = root / "web" / "assets"
    desktop_build.mkdir(parents=True, exist_ok=True)
    web_assets.mkdir(parents=True, exist_ok=True)

    images = [(size, render_mark(size)) for size in (16, 32, 48, 64, 128, 256)]
    (desktop_build / "icon.ico").write_bytes(_encode_ico(images))
    (desktop_build / "icon.png").write_bytes(render_mark(512))
    (web_assets / "brand-mark.png").write_bytes(render_mark(128))
    print(f"Generated brand assets under {desktop_build} and {web_assets}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
