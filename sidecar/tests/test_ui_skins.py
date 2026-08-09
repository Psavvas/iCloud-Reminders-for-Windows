"""
The two interfaces, checked rather than trusted.

The app ships two whole interfaces -- Apple's and Fluent -- with separate
stylesheets and separate components for the three panes. That arrangement has
one failure mode nothing else would catch: a component shared by both is styled
in one sheet and not the other, so it renders unstyled for half the users and
looks fine to whoever changed it. These tests are the cheap half of that check.

They read source files rather than run a browser, so they cost nothing and run
everywhere the rest of the suite does.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "src-react" / "src"

APPLE_CSS = SRC / "styles.css"
WINUI_CSS = SRC / "winui.css"


@pytest.fixture(scope="module")
def sheets() -> dict[str, str]:
    return {
        "styles.css": APPLE_CSS.read_text(encoding="utf-8"),
        "winui.css": WINUI_CSS.read_text(encoding="utf-8"),
    }


def test_both_stylesheets_exist():
    assert APPLE_CSS.is_file(), "the Apple stylesheet is gone"
    assert WINUI_CSS.is_file(), "the Fluent stylesheet is gone"


def test_neither_stylesheet_depends_on_the_other(sheets):
    """
    Each is a whole interface, not a patch over the other.

    The moment one @imports the other, switching stops being a swap and starts
    being a cascade -- and a rule left over from the interface you are not in
    is the hardest kind of bug to see, because it only shows up for the other
    half of the users.
    """
    for name, css in sheets.items():
        assert "@import" not in css, f"{name} pulls in another stylesheet"


# Classes rendered by components that both interfaces share -- the gate, the
# dialogs, the settings rows, the notices, the onboarding tour, the composer and
# the print view. Each must be styled in both sheets or it is unstyled in one.
SHARED_CLASSES = [
    # sign-in
    ".gate", ".gate-card", ".gate-mark", ".gate-step", ".gate-sub", ".field",
    ".code-input", ".spinner", ".detail-log",
    # dialogs and settings
    ".sheet", ".sheet-body", ".sheet-actions", ".sheet-grid", ".settings-group",
    ".section-label", ".setting-row", ".switch-row", ".static-row", ".wide-btn",
    # notices
    ".banner", ".auth-notice", ".auth-notice-dot", ".update-notice",
    ".update-notice-dot",
    # sync progress
    ".sync-bar", ".sync-bar-sub", ".sync-track", ".sync-fill",
    # onboarding
    ".onboard", ".onboard-card", ".onboard-steps", ".onboard-step", ".onboard-art",
    ".onboard-list", ".onboard-switch", ".onboard-foot", ".dots", ".dot-pip",
    ".onboard-actions", "kbd",
    # the composer, collapsed and expanded, and its menus
    ".composer-dot", ".composer-input", ".composer-card", ".cc-head", ".cc-fields",
    ".cc-title", ".cc-note", ".cc-info", ".cc-tools", ".cc-add", ".tool",
    ".tool-icon", ".tool-label", ".tool-wrap", ".menu", ".menu-item",
    ".menu-backdrop", ".menu-sep", ".menu-icon", ".menu-swatch", ".menu-scroll",
    ".menu-field", ".composer-menu",
    # print
    ".print-view", ".pv-head", ".pv-item", ".pv-box", ".pv-notes", ".pv-empty",
    # shared primitives
    "button.primary", "button.ghost", "button.danger", "button.linkish",
    ".hint", ".tiny", ".error", ".warn", ".centered", ".strong", ".muted-text",
    ".warn-text",
]


def test_shared_components_are_styled_in_both_interfaces(sheets):
    missing = {
        name: [s for s in SHARED_CLASSES if s not in css]
        for name, css in sheets.items()
    }
    missing = {name: gone for name, gone in missing.items() if gone}
    assert not missing, (
        "these are rendered by components both interfaces share, and are "
        f"unstyled in one of them: {missing}"
    )


def test_each_interface_paints_its_own_ground(sheets):
    """
    A stylesheet that leaves `body` transparent inherits whatever the last one
    painted, which is the one thing swapping them cannot undo.
    """
    for name, css in sheets.items():
        body = re.search(r"html,\s*body\s*\{[^}]*\}", css)
        assert body, f"{name} does not style html, body"
        assert "background:" in body.group(0), f"{name} leaves the body background unset"


def test_only_one_stylesheet_is_ever_attached():
    """
    `?url` is what makes the swap a swap: Vite emits each sheet as its own
    asset and hands over the URL instead of injecting the CSS, so attaching one
    is skin.js's decision. A plain `import "./styles.css"` anywhere would leave
    that sheet live under the other interface, and it would take a screenshot
    to notice.
    """
    skin = (SRC / "skin.js").read_text(encoding="utf-8")
    assert 'from "./styles.css?url"' in skin
    assert 'from "./winui.css?url"' in skin

    for path in SRC.rglob("*.js*"):
        text = path.read_text(encoding="utf-8")
        for hit in re.findall(r'import\s+[^;\n]*["\']([^"\']+\.css)["\']', text):
            assert hit.endswith("?url"), (
                f"{path.relative_to(ROOT)} imports {hit} for its side effect; "
                "that sheet is then live whichever interface is selected"
            )


def test_the_skin_is_attached_as_a_link_not_an_injected_style():
    """
    The one failure this arrangement invites that no amount of rendering will
    catch.

    Tauri appends its own nonces to `style-src` when it compiles the CSP, and a
    directive carrying a nonce makes `'unsafe-inline'` inert for style
    *elements*. So a <style> built at runtime is dropped and the entire app
    renders with no CSS at all -- while inline style attributes, scripts and
    everything else keep working, because a nonce does not apply to attributes.

    It cannot be reproduced outside the packaged app: dist/ loaded in a browser
    has no CSP, so the screenshots look perfect. This shipped exactly once.
    A <link> at a same-origin URL needs only `style-src 'self'`.
    """
    skin = (SRC / "skin.js").read_text(encoding="utf-8")
    assert 'createElement("link")' in skin, "the skin must be attached as a <link>"
    assert 'createElement("style")' not in skin, (
        "an injected <style> is blocked by the CSP Tauri compiles, and fails "
        "silently -- the whole app renders unstyled"
    )
    assert "?inline" not in skin, (
        "?inline means the CSS arrives as a string with nowhere to go but a "
        "<style> element, which the CSP drops"
    )


def test_the_csp_still_allows_style_attributes():
    """
    Tauri's nonce kills `'unsafe-inline'` for style elements but not for style
    attributes, and the UI sets plenty of those -- a list's colour, the sync
    bar's width, a menu's measured position. Dropping `'unsafe-inline'` as
    "ineffective anyway" would take all of those with it.
    """
    csp = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text())
    csp = csp["app"]["security"]["csp"]
    style = [d for d in csp.split(";") if d.strip().startswith("style-src")]
    assert style, "no style-src directive"
    assert "'unsafe-inline'" in style[0], (
        "React writes style attributes throughout; without this they are blocked"
    )
    assert "'self'" in style[0], "the skin stylesheets are same-origin <link>s"


def test_both_stylesheets_reach_the_build_as_separate_assets():
    """
    `?url` emitting a real file is the whole mechanism. If a Vite change ever
    turned it back into an inlined string, the build would still succeed and
    the app would still work in a browser.
    """
    dist = ROOT / "dist" / "assets"
    if not dist.is_dir():
        pytest.skip("dist/ not built")
    names = [p.name for p in dist.glob("*.css")]
    assert any(n.startswith("styles-") for n in names), names
    assert any(n.startswith("winui-") for n in names), names


def test_the_interface_choice_survives_a_restart():
    """
    Settings is the source of truth, and localStorage is the head start the
    inline script in index.html needs to paint the right ground colour before
    the bundle arrives. Both halves have to be there or a Fluent launch flashes
    Apple white.
    """
    skin = (SRC / "skin.js").read_text(encoding="utf-8")
    assert 'localStorage.setItem("ui_style"' in skin
    assert 'localStorage.getItem("ui_style")' in skin

    html = (ROOT / "src-react" / "index.html").read_text(encoding="utf-8")
    assert 'localStorage.getItem("ui_style")' in html, (
        "the first-paint script does not know which interface it is painting for"
    )
    assert '[data-ui="winui"]' in html, (
        "index.html has no Fluent background, so that interface flashes white"
    )


def test_the_composer_offers_nothing_the_api_cannot_write():
    """
    Apple's own inline composer has a tag button. This one must not: the API
    accepts a tag write and the Reminders app never renders it (see the README),
    so the control would do nothing while looking like it worked.
    """
    src = (SRC / "components" / "Composer.jsx").read_text(encoding="utf-8")
    tools = re.findall(r'title="([^"]+)"\s*\n\s*value=', src)
    assert tools, "the composer no longer has any quick actions"
    assert not any("tag" in t.lower() for t in tools), (
        f"the composer offers {tools}, and tags cannot be written through this API"
    )


def test_the_settings_key_is_declared_in_the_sidecar():
    """
    set_settings drops keys it has never seen, so an undeclared setting saves
    silently and reads back as the default on the next launch.
    """
    from reminders_sidecar.db import Cache

    cache = Cache(":memory:")
    try:
        assert cache.get_settings()["ui_style"] == "apple"
        assert cache.set_settings({"ui_style": "winui"})["ui_style"] == "winui"
        assert cache.get_settings()["ui_style"] == "winui"
    finally:
        cache.close()
