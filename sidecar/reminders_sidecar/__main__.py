"""Sidecar entry point. Started by the Tauri shell, speaks JSON over stdio."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path


def default_data_dir() -> Path:
    """%LOCALAPPDATA%\\RemindersSync on Windows, XDG-ish elsewhere."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / "RemindersSync"
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "reminders-sync"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="reminders-sidecar")
    p.add_argument("--apple-id", default=os.environ.get("REMINDERS_APPLE_ID"))
    p.add_argument("--data-dir", default=os.environ.get("REMINDERS_DATA_DIR"))
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args(argv)

    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    # stderr only: stdout carries the protocol.
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from .server import Server

    server = Server(
        db_path=str(data_dir / "cache.db"),
        apple_id=args.apple_id,
        cookie_dir=str(data_dir / "cookies"),
    )
    return server.serve()


if __name__ == "__main__":
    sys.exit(main())
