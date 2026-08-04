"""
The README, checked rather than trusted.

Documentation rots quietly: a screenshot gets renamed, a section heading changes
and an anchor stops resolving, a count goes stale. None of that fails a build,
and none of it is visible unless someone re-reads the whole file. These are the
claims cheap enough to verify automatically.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
README = ROOT / "README.md"


@pytest.fixture(scope="module")
def readme() -> str:
    return README.read_text(encoding="utf-8")


def _heading_slugs(md: str) -> set[str]:
    """GitHub's anchor rule: lowercase, drop punctuation, spaces to hyphens."""
    out = set()
    for line in re.findall(r"^#+\s+(.*)$", md, re.M):
        text = re.sub(r"<[^>]+>", "", line)          # strip inline html
        text = re.sub(r"[^\w\- ]", "", text.lower())  # strip punctuation
        out.add(text.strip().replace(" ", "-"))
    return out


def _targets(md: str) -> list[tuple[str, str]]:
    """(kind, target) for every markdown link/image and every <img src>."""
    found = [("link", t) for _, t in re.findall(r"(?<!!)\[([^\]]*)\]\(([^)\s]+)\)", md)]
    found += [("image", t) for t in re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", md)]
    found += [("img tag", t) for t in re.findall(r'<img[^>]+src="([^"]+)"', md)]
    return found


# GitHub resolves these against the repository rather than the file tree: a
# README served from /owner/repo/blob/branch/ climbs two levels to the repo
# root. They are not paths on disk and must not be checked as though they were.
GITHUB_RELATIVE = {
    "../../releases",
    "../../issues",
    "../../pulls",
    "../../actions",
    "../../wiki",
}


def test_every_local_link_and_image_resolves(readme):
    slugs = _heading_slugs(readme)
    broken = []
    for kind, target in _targets(readme):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        if target.startswith("#"):
            if target[1:] not in slugs:
                broken.append(f"{kind} anchor {target}")
        elif target.rstrip("/") in GITHUB_RELATIVE:
            continue
        elif target.startswith("../"):
            broken.append(f"{kind} {target} (escapes the repo, and is not a known GitHub link)")
        elif not (ROOT / target).is_file():
            broken.append(f"{kind} {target}")
    assert not broken, "README points at things that do not exist: " + ", ".join(broken)


def test_the_readme_shows_a_logo(readme):
    """The header image is the app's own icon, so it can never drift from it."""
    srcs = [t for _, t in _targets(readme)]
    assert any("icons/icon.png" in s or "icons/128x128" in s for s in srcs), (
        "the header no longer shows the app icon"
    )


def test_collapsible_sections_are_balanced(readme):
    """An unclosed <details> swallows the rest of the page on GitHub."""
    assert readme.count("<details") == readme.count("</details>")
    assert readme.count("<summary") == readme.count("</summary>")
    # Raw markdown inside <details> only renders after a blank line.
    for block in re.findall(r"</summary>\n(.)", readme):
        assert block == "\n", "put a blank line after </summary> or the markdown is literal"


def test_html_tables_are_balanced(readme):
    assert readme.count("<table") == readme.count("</table>")
    assert readme.count("<tr") == readme.count("</tr>")
    assert readme.count("<td") == readme.count("</td>")


def test_mermaid_blocks_are_not_empty(readme):
    """Syntax is validated by rendering; this only catches an empty fence."""
    blocks = re.findall(r"```mermaid\n(.*?)```", readme, re.S)
    assert blocks, "the architecture diagram is gone"
    for b in blocks:
        assert b.strip(), "empty mermaid block renders as an error box on GitHub"


def test_the_stated_test_count_is_current(request, readme):
    """
    The README claims a number in two places -- a badge and a sentence. Both go
    stale the moment a test is added, and nothing else would ever notice.
    """
    invoked = [a for a in request.config.invocation_params.args if not a.startswith("-")]
    if invoked:
        pytest.skip("partial run; the total only means something for the whole suite")

    total = len(request.session.items)
    claims = {int(n) for n in re.findall(r"(\d+)(?:%20| )(?:tests?|passing)", readme)}
    claims |= {int(n) for n in re.findall(r"\b(\d+) tests\b", readme)}
    assert claims, "the README no longer states a test count"
    assert claims == {total}, (
        f"README says {sorted(claims)}, suite collects {total}. "
        f"Update the badge and the Tests section."
    )


# ------------------------------------------------------------- release wiring ---
#
# The version appears in two files and in the git tag. A tag that disagrees with
# the config ships an installer claiming the wrong version, and later tells the
# updater that a release it already has is newer. CI checks the tag; these check
# the files agree with each other before it gets that far.

import json


def test_the_app_version_is_stated_once_and_agrees():
    tauri = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text())["version"]
    pkg = json.loads((ROOT / "package.json").read_text())["version"]
    assert tauri == pkg, (
        f"tauri.conf.json says {tauri}, package.json says {pkg}. "
        "Windows shows the former; the updater compares it too."
    )


def test_the_version_is_semver():
    """The updater compares versions with semver rules; anything else is a coin toss."""
    v = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text())["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-+].+)?", v), f"{v!r} is not semver"


def test_the_build_workflow_exists_and_covers_windows():
    """
    Every build breakage this project has had was Windows-only and invisible
    anywhere else. The workflow that catches them is worth asserting exists.
    """
    wf = ROOT / ".github" / "workflows" / "build.yml"
    assert wf.is_file(), "no Windows build workflow"
    text = wf.read_text()
    assert "windows-latest" in text
    assert "build-sidecar.ps1" in text, "CI must freeze the sidecar, not just the app"
    assert "reminders-sidecar.exe" in text, (
        "CI must verify the sidecar reached the bundle -- it has shipped without it"
    )
    assert "7z" in text and "Get-FileHash" in text, (
        "the sidecar check must unpack the installers and match by content. It is "
        "a bundled resource, so it lives inside the .msi and .exe; globbing the "
        "bundle directory only ever finds the installers themselves."
    )


def test_no_npm_script_drives_the_frontend_with_a_prefix_flag():
    """
    `npm --prefix src-react <cmd>` from a root npm script recursed on Windows:
    the nested npm re-entered the root package, firing postinstall again about
    twenty times until PATH outgrew the Windows limit. It does not reproduce on
    Linux, so only CI caught it. scripts/ui.mjs is the safe way in.
    """
    scripts = json.loads((ROOT / "package.json").read_text())["scripts"]
    offenders = {k: v for k, v in scripts.items() if "--prefix" in v}
    assert not offenders, (
        f"{offenders} shell out to npm with --prefix; use scripts/ui.mjs instead"
    )

    before_dev = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text())
    before_dev = before_dev["build"].get("beforeDevCommand", "")
    assert "--prefix" not in before_dev, (
        f"beforeDevCommand {before_dev!r} has the same recursion hazard"
    )
