"""
Generate the app icon set with no image-library dependency.

Draws a Reminders-style mark: a light rounded square holding a short list --
coloured bullets down the left, grey lines beside them -- then writes PNGs plus
a multi-resolution .ico (Windows needs the .ico for the exe resource, the
installer, and the tray).

Run from the repo root:  python scripts/make_icons.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "src-tauri" / "icons"

# Apple system colours for the bullets, top to bottom.
BULLETS = [
    (255, 59, 48),    # red
    (255, 149, 0),    # orange
    (0, 122, 255),    # blue
    (52, 199, 89),    # green
]
LINE = (199, 199, 204)      # systemGray3
FACE_TOP = (255, 255, 255)
FACE_BOTTOM = (238, 238, 242)
EDGE = (214, 214, 220)


def _blend(dst, src, alpha):
    return tuple(round(d + (s - d) * alpha) for d, s in zip(dst, src))


def _coverage(px, py, test, samples=4):
    """Box-filter antialiasing: fraction of sub-samples passing `test`."""
    hits = 0
    step = 1.0 / samples
    for i in range(samples):
        for j in range(samples):
            if test(px + (i + 0.5) * step, py + (j + 0.5) * step):
                hits += 1
    return hits / (samples * samples)


def _rounded_rect(lo_x, lo_y, hi_x, hi_y, r):
    """Point test for a rounded rectangle."""

    def test(x, y):
        if x < lo_x or x > hi_x or y < lo_y or y > hi_y:
            return False
        # Clamp to the corner-circle centre nearest this point.
        cx = min(max(x, lo_x + r), hi_x - r)
        cy = min(max(y, lo_y + r), hi_y - r)
        dx, dy = x - cx, y - cy
        return dx * dx + dy * dy <= r * r

    return test


def _circle(cx, cy, r):
    def test(x, y):
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r

    return test


def render(size: int) -> bytes:
    """Return RGBA pixel rows for one icon size."""
    s = float(size)

    # Below 32px four rows turn to mush, so drop one and thicken everything.
    rows = 4 if size >= 32 else 3
    small = size < 32

    inset = s * 0.045
    face = _rounded_rect(inset, inset, s - inset, s - inset, s * 0.225)
    # A hairline inset ring keeps the mark from dissolving on a white taskbar.
    inner = _rounded_rect(
        inset + s * 0.012, inset + s * 0.012,
        s - inset - s * 0.012, s - inset - s * 0.012,
        s * 0.213,
    )

    bullet_r = s * (0.075 if small else 0.062)
    bullet_x = s * 0.29
    line_h = s * (0.075 if small else 0.058)
    line_x0 = s * (0.44 if small else 0.42)
    line_x1 = s * 0.78

    # Distribute rows evenly through the middle of the face.
    top, bottom = s * 0.30, s * 0.70
    step = (bottom - top) / (rows - 1)
    centres = [top + i * step for i in range(rows)]

    bullets = [
        (_circle(bullet_x, cy, bullet_r), BULLETS[i % len(BULLETS)])
        for i, cy in enumerate(centres)
    ]
    lines = [
        _rounded_rect(line_x0, cy - line_h / 2, line_x1, cy + line_h / 2, line_h / 2)
        for cy in centres
    ]

    out = bytearray()
    for y in range(size):
        out.append(0)  # PNG filter type: none
        for x in range(size):
            a = _coverage(x, y, face)
            if a <= 0.001:
                out += bytes((0, 0, 0, 0))
                continue

            # Face: soft vertical gradient, with a faint edge ring.
            colour = _blend(FACE_TOP, FACE_BOTTOM, y / s)
            ring = a - _coverage(x, y, inner)
            if ring > 0.02:
                colour = _blend(colour, EDGE, min(ring, 1.0) * 0.9)

            for test, tint in bullets:
                cov = _coverage(x, y, test)
                if cov > 0:
                    colour = _blend(colour, tint, cov)
            for test in lines:
                cov = _coverage(x, y, test)
                if cov > 0:
                    colour = _blend(colour, LINE, cov)

            out += bytes((*colour, round(a * 255)))
    return bytes(out)


def png_bytes(size: int, raw: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def ico_bytes(pngs: dict[int, bytes]) -> bytes:
    """ICO container holding PNG-compressed entries (supported since Vista)."""
    sizes = sorted(pngs)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    payload = b""
    for size in sizes:
        data = pngs[size]
        entries += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,  # 0 means 256
            0 if size >= 256 else size,
            0,
            0,
            1,
            32,
            len(data),
            offset,
        )
        payload += data
        offset += len(data)
    return header + entries + payload


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sizes = [16, 32, 48, 64, 128, 256]
    pngs = {s: png_bytes(s, render(s)) for s in sizes}

    (OUT / "icon.ico").write_bytes(ico_bytes(pngs))
    for s in (32, 128, 256):
        (OUT / f"{s}x{s}.png").write_bytes(pngs[s])
    (OUT / "icon.png").write_bytes(pngs[256])

    for f in sorted(OUT.iterdir()):
        print(f"  {f.name:16} {f.stat().st_size:>7} bytes")


if __name__ == "__main__":
    main()
