"""
Standalone process used to prove session persistence across a restart.

Run by `run_spike.py` as a *separate* subprocess. It must authenticate with no
human in the loop: no password typed, no 2FA code. If that succeeds, the
session genuinely survived the process boundary.

Prints a single JSON object to stdout.
"""

from __future__ import annotations

import json
import os
import sys


def main() -> int:
    out: dict[str, object] = {"ok": False}
    try:
        from pyicloud import PyiCloudService

        apple_id = os.environ.get("ICLOUD_APPLE_ID")
        if not apple_id:
            out["error"] = "ICLOUD_APPLE_ID not set in subprocess env"
            print(json.dumps(out))
            return 1

        # No password argument: this must come from the stored session/keyring.
        api = PyiCloudService(apple_id)

        out["requires_2fa"] = bool(api.requires_2fa)
        out["requires_2sa"] = bool(api.requires_2sa)
        out["is_trusted_session"] = bool(api.is_trusted_session)

        if api.requires_2fa or api.requires_2sa:
            out["error"] = "re-authentication demanded in fresh process"
            print(json.dumps(out))
            return 1

        # Prove the session is actually usable, not just non-erroring.
        names = [lst.title for lst in api.reminders.lists()]
        out["list_count"] = len(names)
        out["ok"] = True
    except Exception as exc:  # noqa: BLE001 - spike probe reports, never raises
        out["error"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps(out))
        return 1

    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
