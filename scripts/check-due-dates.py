"""
Prints what iCloud actually stores in a reminder's DueDate, read both ways.

    py scripts\\check-due-dates.py

Apple stores a due date as wall-clock fields encoded as though they were UTC,
not as a point on the timeline. The app now reads it that way. This proves it
against a live account instead of assuming: the two columns differ by the local
UTC offset, so whichever one matches what the iPhone shows is the right reading.

  as wall clock   what the app shows now
  as instant      what it showed before, and what looked overdue early

If "as instant" is the column that matches your phone, the assumption is
backwards and floating_to_instant in sidecar/reminders_sidecar/timeutil.py needs
inverting. Nothing here writes anything.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sidecar_command() -> list[str]:
    """Prefer the frozen exe; fall back to the venv, then this interpreter."""
    override = os.environ.get("REMINDERS_SIDECAR")
    if override:
        args = os.environ.get("REMINDERS_SIDECAR_ARGS", "").split()
        return [override, *args]

    exe = ROOT / "dist-sidecar" / ("reminders-sidecar.exe" if os.name == "nt" else "reminders-sidecar")
    if exe.exists():
        return [str(exe)]

    venv = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python = str(venv) if venv.exists() else sys.executable
    return [python, "-m", "reminders_sidecar"]


def main() -> int:
    cmd = sidecar_command()
    env = {**os.environ, "PYTHONPATH": str(ROOT / "sidecar")}
    print("Using:", " ".join(cmd))

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
    )

    def send(method: str, params: dict | None = None) -> dict:
        proc.stdin.write(json.dumps({"id": 1, "method": method, "params": params or {}}) + "\n")
        proc.stdin.flush()
        # Skip unsolicited events; only the reply carries an id.
        while True:
            line = proc.stdout.readline()
            if not line:
                raise SystemExit("the sidecar exited without answering")
            msg = json.loads(line)
            if msg.get("id") is not None:
                return msg

    # The sidecar restores its own session on startup, so give it a moment
    # before asking it to talk to iCloud.
    for _ in range(30):
        st = send("auth_status").get("result") or {}
        if st.get("authenticated"):
            break
        if not st.get("restoring"):
            break
        import time

        time.sleep(1)

    reply = send("due_probe", {"limit": 15})
    if not reply.get("ok"):
        err = reply.get("error") or {}
        print("\nFailed:", err.get("message") or err)
        if err.get("code") == "AUTH_REQUIRED":
            print("Sign in through the app first, then run this again.")
        proc.kill()
        return 1

    data = reply["result"]
    print(f"\nLocal zone: {data['zone']}\n")
    head = f"{'reminder':<34} {'day?':<5} {'as wall clock':<26} {'as instant':<26} zone"
    print(head)
    print("-" * len(head))
    for r in data["reminders"]:
        print(
            f"{r['title'][:33]:<34} "
            f"{('all' if r['all_day'] else '-'):<5} "
            f"{r['as_wall_clock'][:25]:<26} "
            f"{r['as_instant'][:25]:<26} "
            f"{r['time_zone'] or '-'}"
        )

    print(
        "\nOpen Reminders on your iPhone and compare. The matching column is the"
        "\ncorrect reading; if it is 'as instant', tell Claude and the conversion"
        "\nneeds to be inverted."
    )
    send("shutdown")
    proc.wait(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
