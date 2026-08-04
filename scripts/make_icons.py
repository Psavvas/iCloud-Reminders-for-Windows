"""
Generate the app icon set with no image-library dependency.

Draws a Reminders-style mark: a rounded white card holding a short list --
coloured bullets down the left, grey bars beside them -- then writes PNGs plus a
multi-resolution .ico. Windows needs the .ico for the exe resource, the
installer, the taskbar and the tray.

Run from the repo root:  python scripts/make_icons.py

Two things this has to get right that one drawing does not:

**Every size Windows asks for.** The shell picks from 16/20/24/32/40/48/64/96/
128/256 depending on where the icon appears and the display scaling, and
silently resamples the nearest match when one is missing. A 24px tray icon
squeezed out of the 32px art is the mush this used to produce.

**Legible features at 16px.** Scaling everything proportionally puts the bullets
and bars under a pixel across down there. Feature sizes are floored in absolute
pixels instead and the row count drops to three, so the small art is redrawn
rather than shrunk.

Shapes are signed distance fields. Exact distance to an edge gives clean
one-pixel antialiasing at any size, and a soft drop shadow falls out of the same
number -- which is what keeps a white card from vanishing on a white taskbar.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "src-tauri" / "icons"

# Windows shell icon sizes. 20/24/40 are what display scaling asks for, and the
# ones most often missing.
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]

# Apple system colours for the bullets, top to bottom.
BULLETS = [
    (255, 59, 48),    # red
    (255, 149, 0),    # orange
    (0, 122, 255),    # blue
    (52, 199, 89),    # green
]
BAR = (196, 196, 202)
FACE_TOP = (255, 255, 255)
FACE_BOTTOM = (240, 240, 244)
EDGE = (201, 201, 209)


def _blend(dst, src, a):
    return tuple(round(d + (s - d) * a) for d, s in zip(dst, src))


def _clamp01(v):
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def rounded_rect_sdf(cx, cy, hx, hy, r):
    """
    Signed distance to a rounded rectangle: negative inside, zero on the edge.

    Exact rather than sampled, so a half-covered pixel gets half coverage at
    16px as reliably as at 256px.
    """
    r = min(r, hx, hy)

    def sdf(x, y):
        qx = abs(x - cx) - (hx - r)
        qy = abs(y - cy) - (hy - r)
        return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r

    return sdf


def circle_sdf(cx, cy, r):
    def sdf(x, y):
        return math.hypot(x - cx, y - cy) - r

    return sdf


def _cover(d):
    """Distance -> coverage, antialiased across one pixel."""
    return _clamp01(0.5 - d)


def render(size: int) -> bytes:
    """Return PNG-ready RGBA scanlines for one icon size."""
    s = float(size)

    # Four rows need about 28px to stay apart; below that it is three.
    rows = 4 if size >= 28 else 3
    bullet_r = max(s * 0.062, 1.5)
    bar_h = max(s * 0.058, 2.0)

    inset = max(s * 0.045, 0.5)
    half = (s - 2 * inset) / 2.0
    cx = cy = s / 2.0
    card = rounded_rect_sdf(cx, cy, half, half, (s - 2 * inset) * 0.225)

    # A hairline that stays a hairline: about one device pixel at every size, so
    # the card keeps a defined edge on white without gaining a visible frame.
    edge_w = max(s * 0.008, 0.9)

    # Cast from just below, the way an app icon sits on a surface.
    shadow_dy = max(s * 0.022, 0.5)
    shadow_blur = max(s * 0.06, 1.0)

    bullet_x = inset + max(s * 0.115, bullet_r + 1.0)
    bar_x0 = bullet_x + bullet_r + max(s * 0.055, 2.0)
    bar_x1 = s - inset - max(s * 0.085, 2.0)
    if bar_x1 - bar_x0 < bar_h * 1.6:  # never leave a bar shorter than a stub
        bar_x1 = bar_x0 + bar_h * 1.6

    # Spread rows through the middle of the card, keeping a whole pixel of
    # daylight between them so they never merge into a block.
    span = max(bullet_r * 2, bar_h)
    pitch = max(span + max(s * 0.028, 1.0), span * 1.35)
    top = cy - pitch * (rows - 1) / 2.0
    centres = [top + i * pitch for i in range(rows)]

    bullets = [
        (circle_sdf(bullet_x, y, bullet_r), BULLETS[i % len(BULLETS)])
        for i, y in enumerate(centres)
    ]
    bars = [
        rounded_rect_sdf(
            (bar_x0 + bar_x1) / 2.0, y, (bar_x1 - bar_x0) / 2.0, bar_h / 2.0, bar_h / 2.0
        )
        for y in centres
    ]

    out = bytearray()
    for py in range(size):
        out.append(0)  # PNG filter type: none
        y = py + 0.5
        for px in range(size):
            x = px + 0.5

            d = card(x, y)
            a = _cover(d)

            # The shadow only shows where the card does not cover the pixel.
            shade = 0.0
            if a < 0.999:
                sd = card(x, y - shadow_dy)
                shade = _clamp01(0.5 - sd / shadow_blur) * 0.22 * (1.0 - a)

            alpha = a + shade * (1.0 - a)
            if alpha <= 0.002:
                out += bytes((0, 0, 0, 0))
                continue
            if a <= 0.002:
                out += bytes((0, 0, 0, round(alpha * 255)))
                continue

            colour = _blend(FACE_TOP, FACE_BOTTOM, y / s)

            # Ring: inside the card, within edge_w of the boundary, fading in
            # towards the edge.
            if -edge_w < d < 0:
                colour = _blend(colour, EDGE, (1.0 + d / edge_w) * 0.85)

            for sdf, tint in bullets:
                c = _cover(sdf(x, y))
                if c > 0:
                    colour = _blend(colour, tint, c)
            for sdf in bars:
                c = _cover(sdf(x, y))
                if c > 0:
                    colour = _blend(colour, BAR, c)

            # Where the shadow shows through a partly-covered edge pixel, the
            # card colour is composited over black rather than replacing it.
            if shade > 0:
                colour = _blend((0, 0, 0), colour, a / alpha)
            out += bytes((*colour, round(alpha * 255)))
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
    pngs = {s: png_bytes(s, render(s)) for s in ICO_SIZES}

    (OUT / "icon.ico").write_bytes(ico_bytes(pngs))
    for s in (32, 128, 256):
        (OUT / f"{s}x{s}.png").write_bytes(pngs[s])
    # Tauri's name for the 2x asset; the same pixels as 256.
    (OUT / "128x128@2x.png").write_bytes(pngs[256])
    (OUT / "icon.png").write_bytes(pngs[256])

    for f in sorted(OUT.iterdir()):
        print(f"  {f.name:18} {f.stat().st_size:>7} bytes")


if __name__ == "__main__":
    main()
