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


def test_build_script_does_not_discard_the_probe_stderr():
    """The traceback on stderr is the whole diagnosis when the exe won't start."""
    script = (ROOT / "scripts" / "build-sidecar.ps1").read_text()
    assert "2>$null" not in script
