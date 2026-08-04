"""
Guards the frozen-build entry point.

PyInstaller runs its target script as a top-level module. Freezing
`reminders_sidecar/__main__.py` directly therefore strips the package context
its relative imports need, and the exe dies at startup with

    ImportError: attempted relative import with no known parent package

which reaches the user as "the app won't start". These tests pin the shape that
avoids it.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

SIDECAR = Path(__file__).resolve().parent.parent
ROOT = SIDECAR.parent


def test_run_sidecar_entrypoint_exists():
    assert (SIDECAR / "run_sidecar.py").is_file()


def test_entrypoint_uses_absolute_imports_only():
    """A relative import here would reintroduce the exact startup failure."""
    tree = ast.parse((SIDECAR / "run_sidecar.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, (
                f"run_sidecar.py uses a relative import ({'.' * node.level}"
                f"{node.module}); PyInstaller cannot resolve it"
            )


def test_entrypoint_runs_as_a_plain_script():
    """Exactly how PyInstaller invokes it: as a script, not as -m."""
    proc = subprocess.run(
        [sys.executable, str(SIDECAR / "run_sidecar.py"), "--help"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--data-dir" in proc.stdout


def test_module_invocation_still_works():
    """The documented dev path, python -m reminders_sidecar, must keep working."""
    proc = subprocess.run(
        [sys.executable, "-m", "reminders_sidecar", "--help"],
        capture_output=True, text=True, timeout=60, cwd=str(SIDECAR),
    )
    assert proc.returncode == 0, proc.stderr
    assert "--data-dir" in proc.stdout


def test_build_script_freezes_the_entrypoint_not_the_package_main():
    script = (ROOT / "scripts" / "build-sidecar.ps1").read_text()
    assert "run_sidecar.py" in script
    assert "reminders_sidecar\\__main__.py" not in script
    # fido2 ships a data file pyicloud needs at import time.
    assert '"--collect-all", "fido2"' in script


def test_build_script_bundles_the_timezone_database():
    """
    Windows has no system tz database, so zoneinfo depends entirely on the
    tzdata package. Freeze without it and every named zone fails to resolve --
    silently, since the code falls back to a fixed offset, putting due dates
    back out by an hour for half the year.
    """
    script = (ROOT / "scripts" / "build-sidecar.ps1").read_text()
    assert '"--collect-all", "tzdata"' in script
    assert '"--collect-all", "tzlocal"' in script


def test_timezone_dependencies_are_declared():
    pyproject = (SIDECAR / "pyproject.toml").read_text()
    assert "tzlocal" in pyproject
    assert "tzdata" in pyproject


def test_build_script_does_not_discard_the_probe_stderr():
    """The traceback on stderr is the whole diagnosis when the exe won't start."""
    script = (ROOT / "scripts" / "build-sidecar.ps1").read_text()
    assert "2>$null" not in script


# --------------------------------------------------------- build tooling ----
def test_node_scripts_resolve_their_own_path_portably():
    """
    `new URL(import.meta.url).pathname` yields "/D:/src/..." on Windows, and
    path.resolve then produces "D:\\D:\\src\\...". Only fileURLToPath applies
    the platform's rules. The two agree on POSIX, so this cannot be caught by
    running the scripts here -- hence a static check.
    """
    offenders = []
    for js in ROOT.rglob("*.mjs"):
        if "node_modules" in js.parts:
            continue
        text = js.read_text(encoding="utf-8")
        if "import.meta.url" not in text:
            continue
        if ".pathname" in text:
            offenders.append(str(js.relative_to(ROOT)))
        elif "fileURLToPath" not in text:
            offenders.append(str(js.relative_to(ROOT)))
    assert not offenders, (
        "these resolve their own path in a way that breaks on Windows: "
        + ", ".join(offenders)
    )


def test_build_script_runs_the_sidecar_check_first():
    """The bundler's own failure for a missing resource is not actionable."""
    pkg = json.loads((ROOT / "package.json").read_text())
    assert "check-sidecar.mjs" in pkg["scripts"]["build"]


def test_tauri_hook_paths_are_relative_to_the_project_root():
    """
    Tauri runs beforeDev/beforeBuild from the project root, not from
    src-tauri. A "../" prefix there resolves one directory too high and the
    build fails with ENOENT on a path outside the repo.
    """
    conf = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text())
    for key in ("beforeDevCommand", "beforeBuildCommand"):
        cmd = conf.get("build", {}).get(key)
        if not cmd:
            continue
        assert "../" not in cmd and "..\\" not in cmd, (
            f"{key} walks out of the project root: {cmd}"
        )
        # The referenced directory must exist relative to the root.
        for token in cmd.split():
            if token.startswith("src-"):
                assert (ROOT / token).is_dir(), f"{key} points at a missing {token}"


def test_the_ui_is_built_before_the_bundle():
    """
    Vite has to produce dist/ before Tauri bundles it. That step now lives in
    the npm script rather than a Tauri hook, so its working directory is npm's
    and therefore predictable.
    """
    pkg = json.loads((ROOT / "package.json").read_text())
    build = pkg["scripts"]["build"]
    assert "ui.mjs run build" in build, (
        "the frontend build must go through scripts/ui.mjs; calling npm with "
        "--prefix from a root script recursed on Windows"
    )
    assert build.index("ui.mjs run build") < build.index("tauri build")
