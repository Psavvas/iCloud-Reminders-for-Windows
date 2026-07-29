"""
Generate the app icon set with no image-library dependency.

Draws a rounded square in the app's accent blue with a white check mark, then
writes PNGs plus a multi-resolution .ico (Windows needs the .ico for the exe
resource, the installer, and the tray).

Run from the repo root:  python scripts/make_icons.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "src-tauri" / "icons"

ACCENT = (15, 108, 189)      # #0F6CBD, matches the UI accent
ACCENT_DARK = (10, 84, 150)
WHITE = (255, 255, 255)


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


def render(size: int) -> bytes:
    """Return RGBA pixel rows for one icon size."""
    s = float(size)
    radius = s * 0.22
    inset = s * 0.06
    lo, hi = inset, s - inset

    def in_rounded_rect(x, y):
        if x < lo or x > hi or y < lo or y > hi:
            return False
        # Corner circles
        for cx, cy in (
            (lo + radius, lo + radius),
            (hi - radius, lo + radius),
            (lo + radius, hi - radius),
            (hi - radius, hi - radius),
        ):
            if (x < lo + radius or x > hi - radius) and (
                y < lo + radius or y > hi - radius
            ):
                near_x = cx
                near_y = cy
                if abs(x - near_x) > radius or abs(y - near_y) > radius:
                    continue
                return (x - near_x) ** 2 + (y - near_y) ** 2 <= radius * radius
        return True

    # Check mark as two thick segments.
    thickness = max(s * 0.085, 1.2)
    p0 = (s * 0.30, s * 0.52)
    p1 = (s * 0.44, s * 0.66)
    p2 = (s * 0.72, s * 0.36)

    def near_segment(x, y, a, b, t):
        ax, ay = a
        bx, by = b
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        if L2 == 0:
            return False
        u = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / L2))
        px, py = ax + u * dx, ay + u * dy
        return (x - px) ** 2 + (y - py) ** 2 <= (t / 2) ** 2

    def in_check(x, y):
        return near_segment(x, y, p0, p1, thickness) or near_segment(
            x, y, p1, p2, thickness
        )

    rows = bytearray()
    for y in range(size):
        rows.append(0)  # PNG filter type: none
        for x in range(size):
            bg_a = _coverage(x, y, in_rounded_rect)
            if bg_a <= 0.001:
                rows += bytes((0, 0, 0, 0))
                continue
            # Vertical gradient for a bit of depth.
            t = y / s
            base = _blend(ACCENT, ACCENT_DARK, t * 0.55)
            ck_a = _coverage(x, y, in_check)
            colour = _blend(base, WHITE, ck_a) if ck_a > 0 else base
            rows += bytes((*colour, round(bg_a * 255)))
    return bytes(rows)


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
