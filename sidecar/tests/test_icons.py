"""
The icon set, checked as a build artifact rather than by eye.

Windows resamples the nearest available size when the one it wants is missing,
and the result at 20 or 24px is a smear nobody notices until it ships. These
tests pin the sizes and check the small art is still legible -- that the
bullets survive as distinguishable colour and the bars have not merged into the
card.
"""

from __future__ import annotations

import importlib.util
import json
import struct
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
ICONS = ROOT / "src-tauri" / "icons"


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "make_icons", ROOT / "scripts" / "make_icons.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


make_icons = _load_generator()


def decode_png(data: bytes) -> tuple[int, int, list[tuple[int, int, int, int]]]:
    """Minimal RGBA PNG reader -- enough for what this script writes."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, width, height, idat = 8, 0, 0, b""
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        tag = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        if tag == b"IHDR":
            width, height, depth, colour = struct.unpack(">IIBB", body[:10])
            assert (depth, colour) == (8, 6), "expected 8-bit RGBA"
        elif tag == b"IDAT":
            idat += body
        pos += 12 + length

    raw = zlib.decompress(idat)
    stride = width * 4
    pixels = []
    for y in range(height):
        start = y * (stride + 1)
        assert raw[start] == 0, "only filter type 0 is written"
        row = raw[start + 1 : start + 1 + stride]
        pixels += [tuple(row[i : i + 4]) for i in range(0, stride, 4)]
    return width, height, pixels


def ico_entries(data: bytes) -> dict[int, bytes]:
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1)
    out = {}
    for i in range(count):
        off = 6 + 16 * i
        w, h, _colours, _res, _planes, _bpp, size, offset = struct.unpack(
            "<BBBBHHII", data[off : off + 16]
        )
        out[w or 256] = data[offset : offset + size]
    return out


@pytest.fixture(scope="module")
def entries():
    return ico_entries((ICONS / "icon.ico").read_bytes())


def test_the_ico_carries_every_windows_shell_size(entries):
    """
    20, 24 and 40 are what display scaling asks for. Leaving them out is why the
    tray icon looked resampled: it was.
    """
    assert set(entries) >= {16, 20, 24, 32, 40, 48, 64, 96, 128, 256}


def test_every_entry_is_a_png_of_the_size_it_claims(entries):
    for declared, blob in entries.items():
        w, h, _ = decode_png(blob)
        assert (w, h) == (declared, declared)


@pytest.mark.parametrize("size", [16, 20, 24, 32, 48])
def test_small_sizes_keep_all_their_bullets(size):
    """
    Each bullet has to survive as its own colour. When features fall below a
    pixel they blend into the card and the mark reads as a grey smudge.
    """
    _w, _h, pixels = decode_png(make_icons.png_bytes(size, make_icons.render(size)))

    def present(target):
        return any(
            a > 200 and max(abs(p - t) for p, t in zip((r, g, b), target)) < 70
            for r, g, b, a in pixels
        )

    rows = 4 if size >= 28 else 3
    for colour in make_icons.BULLETS[:rows]:
        assert present(colour), f"{size}px lost the {colour} bullet"


@pytest.mark.parametrize("size", [16, 20, 24, 32])
def test_small_sizes_keep_daylight_between_rows(size):
    """
    The bullets must not touch vertically. Adjacent rows merging into a bar is
    the specific way this art used to fail at 16px.
    """
    _w, _h, pixels = decode_png(make_icons.png_bytes(size, make_icons.render(size)))

    def is_bullet(px):
        r, g, b, a = px
        if a < 200:
            return False
        return any(
            max(abs(c - t) for c, t in zip((r, g, b), colour)) < 70
            for colour in make_icons.BULLETS
        )

    # Look down the bullet column: it must alternate bullet / gap, never run.
    column = [
        any(is_bullet(pixels[y * size + x]) for x in range(size // 2))
        for y in range(size)
    ]
    runs = []
    for filled in column:
        if runs and runs[-1][0] == filled:
            runs[-1][1] += 1
        else:
            runs.append([filled, 1])
    gaps = [n for filled, n in runs if not filled]
    rows = 4 if size >= 28 else 3
    # Leading and trailing gaps plus one between each pair of rows.
    assert len(gaps) >= rows + 1, f"{size}px: bullets merged"


def test_the_card_stays_visible_against_white():
    """
    A white card on a white taskbar needs an edge or a shadow. Without either
    the icon is four floating dots.
    """
    size = 32
    _w, _h, pixels = decode_png(make_icons.png_bytes(size, make_icons.render(size)))
    # Just outside the card's left edge, mid-height: shadow or ring, not nothing.
    row = size // 2
    edge = [pixels[row * size + x] for x in range(0, 4)]
    assert any(a > 8 for *_rgb, a in edge), "no shadow or edge outside the card"


def test_the_bundle_lists_icons_that_exist():
    cfg = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text())
    listed = cfg["bundle"]["icon"]
    assert "icons/icon.ico" in listed, "Windows needs the .ico for the exe resource"
    for rel in listed:
        assert (ROOT / "src-tauri" / rel).is_file(), f"{rel} is listed but missing"


def test_the_checked_in_icons_match_the_generator():
    """A hand-edited PNG would silently drift from the script that draws it."""
    for name, size in (("32x32.png", 32), ("128x128.png", 128), ("icon.png", 256)):
        on_disk = (ICONS / name).read_bytes()
        assert on_disk == make_icons.png_bytes(size, make_icons.render(size)), (
            f"{name} is stale -- re-run python scripts/make_icons.py"
        )


# ---------------------------------------------------------- inline bootstrap ---
#
# index.html carries one inline script: it reads the saved theme and sets it
# before the first paint, so a dark-theme launch is not preceded by a white
# flash. Under the app's CSP an inline script only runs if its hash is
# allow-listed, and a blocked script fails silently -- the flash simply comes
# back. The hash is therefore pinned here rather than trusted.


def _inline_scripts(html: str) -> list[str]:
    import re

    return [
        m.group(1)
        for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    ]


def _csp() -> str:
    cfg = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text())
    return cfg["app"]["security"]["csp"]


def _sha256_csp(body: str) -> str:
    import base64
    import hashlib

    digest = hashlib.sha256(body.encode()).digest()
    return "sha256-" + base64.b64encode(digest).decode()


@pytest.mark.parametrize("source", ["src-react/index.html", "dist/index.html"])
def test_inline_scripts_are_allowed_by_the_csp(source):
    """
    Checks the built copy as well as the source: Vite rewrites index.html, and
    a single character of difference changes the hash and silently kills the
    script.
    """
    path = ROOT / source
    if not path.is_file():
        pytest.skip(f"{source} not built")
    csp = _csp()
    for body in _inline_scripts(path.read_text()):
        want = _sha256_csp(body)
        assert want in csp, (
            f"{source}: inline script is not allow-listed.\n"
            f"Add {want} to script-src in src-tauri/tauri.conf.json"
        )


def test_the_csp_still_restricts_scripts():
    """The point of hashing is to avoid reaching for 'unsafe-inline'."""
    csp = _csp()
    assert "script-src" in csp, "script-src must be explicit once a hash is used"
    assert "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
