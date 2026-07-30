"""
PyInstaller entry point.

Freezing `reminders_sidecar/__main__.py` directly does not work: PyInstaller
runs it as a top-level script, so its relative imports (`from .server import`)
have no parent package and fail at startup with

    ImportError: attempted relative import with no known parent package

This module is imported as an ordinary script and reaches the package by
absolute import, which keeps the package context intact for everything below
it. `python -m reminders_sidecar` still works exactly as before; this exists
only so the frozen build has a script that PyInstaller can safely treat as
the entry point.
"""

from __future__ import annotations

import sys

from reminders_sidecar.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
