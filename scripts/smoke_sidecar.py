"""
End-to-end smoke test of the sidecar across a real process boundary.

Spawns `python -m reminders_sidecar` exactly as the Tauri shell does and
exchanges real newline-delimited JSON over its stdin/stdout. This is the one
place the protocol is exercised through an actual pipe rather than by calling
methods in-process, so it catches stdout pollution, framing mistakes, and
envelope drift that unit tests cannot.

No iCloud account or network needed: it works against an empty cache.

    python scripts/smoke_sidecar.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Client:
    def __init__(self, data_dir: str):
        env_python = sys.executable
        self.proc = subprocess.Popen(
            [env_python, "-m", "reminders_sidecar", "--data-dir", data_dir,
             "--log-level", "WARNING"],
            cwd=str(ROOT / "sidecar"),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._id = 0

    def read_message(self) -> dict:
        line = self.proc.stdout.readline()
        if not line:
            err = self.proc.stderr.read()
            raise RuntimeError(f"sidecar closed stdout. stderr:\n{err}")
        return json.loads(line)

    def call(self, method: str, **params):
        self._id += 1
        self.proc.stdin.write(
            json.dumps({"id": self._id, "method": method, "params": params}) + "\n"
        )
        self.proc.stdin.flush()
        while True:
            msg = self.read_message()
            if "event" in msg:
                print(f"    (event: {msg['event']})")
                continue
            assert msg["id"] == self._id, f"id mismatch: {msg}"
            return msg

    def close(self):
        try:
            self.call("shutdown")
        except Exception:
            pass
        self.proc.stdin.close()
        self.proc.wait(timeout=10)


CHECKS: list[tuple[str, bool]] = []


def check(label: str, ok: bool, detail: str = ""):
    CHECKS.append((label, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        print(f"data dir: {tmp}\n")
        c = Client(tmp)

        hello = c.read_message()
        check("emits a ready event on startup", hello.get("event") == "ready", str(hello))

        r = c.call("ping")
        check("ping returns ok envelope", r.get("ok") is True and r["result"] == {"pong": True})

        r = c.call("lists")
        check("empty cache returns [] not an error", r.get("ok") is True and r["result"] == [])

        r = c.call("auth_status")
        res = r.get("result") or {}
        check(
            "auth_status reports unauthenticated with no session",
            r.get("ok") is True and res.get("authenticated") is False,
            str(res),
        )

        r = c.call("sync_status")
        res = r.get("result") or {}
        check(
            "sync_status has the expected shape",
            set(res) == {"running", "last_sync", "has_cursor", "pending_pushes", "conflicts"},
            str(sorted(res)),
        )

        r = c.call("due_notifications")
        res = r.get("result") or {}
        check(
            "due_notifications returns an empty plan",
            r.get("ok") is True and res == {"toasts": [], "notified_ids": []},
            str(res),
        )

        r = c.call("no_such_method")
        err = r.get("error") or {}
        check(
            "unknown method returns a coded error, not a crash",
            r.get("ok") is False and err.get("code") == "NO_METHOD",
            str(err),
        )

        r = c.call("update_reminder", id="Reminder/does-not-exist", title="x")
        err = r.get("error") or {}
        check(
            "editing a missing reminder errors cleanly",
            r.get("ok") is False and "code" in err,
            str(err),
        )

        # A write with no session must still land locally: the whole point of
        # the cache is that the UI keeps working while iCloud is unreachable.
        r = c.call("create_reminder", list_id="List/A", title="offline write")
        res = r.get("result") or {}
        check(
            "create works with no network and is marked pending",
            r.get("ok") is True and res.get("title") == "offline write" and res.get("dirty") == 1,
            str(res.get("id")),
        )

        r = c.call("reminders", list_id="List/A")
        rows = r.get("result") or []
        check(
            "the offline write reads back from the cache",
            len(rows) == 1 and rows[0]["title"] == "offline write",
        )

        # Malformed input must not take the process down.
        c.proc.stdin.write("this is not json\n")
        c.proc.stdin.flush()
        msg = c.read_message()
        check(
            "garbage input is rejected without killing the process",
            msg.get("ok") is False and (msg.get("error") or {}).get("code") == "BAD_REQUEST",
            str(msg),
        )

        r = c.call("ping")
        check("still responsive after bad input", r.get("ok") is True)

        c.close()

    failed = [l for l, ok in CHECKS if not ok]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
