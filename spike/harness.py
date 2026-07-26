"""
Shared plumbing for the Phase 1 validation spike.

Throwaway code. Nothing here is meant to survive into the real app.
"""

from __future__ import annotations

import getpass
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from pyicloud import PyiCloudService
from pyicloud.exceptions import (
    PyiCloud2FARequiredException,
    PyiCloudAcceptTermsException,
    PyiCloudFailedLoginException,
)

PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED"
UNVERIFIED = "UNVERIFIED"
SKIP = "SKIP"

# Everything the spike creates is prefixed with this so cleanup is unambiguous
# and so you can spot strays in the iPhone UI at a glance.
MARKER = "ZZSPIKE"


@dataclass
class Result:
    item: str
    name: str
    status: str = SKIP
    detail: str = ""
    evidence: str = ""


@dataclass
class Recorder:
    results: list[Result] = field(default_factory=list)

    def add(
        self,
        item: str,
        name: str,
        status: str,
        detail: str = "",
        evidence: str = "",
    ) -> Result:
        r = Result(item=item, name=name, status=status, detail=detail, evidence=evidence)
        self.results.append(r)
        icon = {
            PASS: "[PASS]",
            FAIL: "[FAIL]",
            BLOCKED: "[BLOCKED]",
            UNVERIFIED: "[UNVERIFIED]",
            SKIP: "[SKIP]",
        }.get(status, "[?]")
        print(f"\n  {icon} {item}. {name}")
        if detail:
            print(f"         {detail}")
        return r

    def table(self) -> str:
        lines = [
            "| # | Check | Result | Notes |",
            "|---|-------|--------|-------|",
        ]
        for r in self.results:
            note = (r.detail or "").replace("|", "\\|").replace("\n", " ")
            if len(note) > 160:
                note = note[:157] + "..."
            lines.append(f"| {r.item} | {r.name} | **{r.status}** | {note} |")
        return "\n".join(lines)

    def exit_code(self) -> int:
        return 1 if any(r.status == FAIL for r in self.results) else 0


def section(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def run_check(
    rec: Recorder,
    item: str,
    name: str,
    fn: Callable[[], tuple[str, str]],
) -> Optional[Any]:
    """
    Run one check. `fn` returns (status, detail). Exceptions become FAIL rather
    than killing the run, because a later check may still be informative.
    """
    section(f"CHECK {item}: {name}")
    try:
        status, detail = fn()
    except Exception as exc:  # noqa: BLE001 - spike: report, don't crash
        rec.add(item, name, FAIL, f"{type(exc).__name__}: {exc}")
        return None
    rec.add(item, name, status, detail)
    return None


def confirm(question: str, auto: bool = False) -> tuple[bool, str]:
    """
    Ask the human to confirm something only they can see (i.e. the iPhone).

    Returns (confirmed, note). `auto=True` skips the prompt and reports
    UNVERIFIED, for unattended runs.
    """
    if auto:
        return False, "not confirmed on device (--no-confirm)"
    print("\n  >>> CHECK YOUR IPHONE <<<")
    print(f"  {question}")
    while True:
        ans = input("  [y] yes  [n] no  [s] skip : ").strip().lower()
        if ans in ("y", "yes"):
            return True, "confirmed on iPhone"
        if ans in ("n", "no"):
            return False, "NOT visible on iPhone"
        if ans in ("s", "skip"):
            return False, "skipped device confirmation"


def connect(
    apple_id: Optional[str] = None,
    password: Optional[str] = None,
    accept_terms: bool = False,
    interactive: bool = True,
) -> PyiCloudService:
    """
    Build an authenticated PyiCloudService, handling 2FA and the T&C wall.

    Password resolution order: explicit arg -> keyring (pyicloud does this
    itself) -> interactive prompt. The password is never written to disk here;
    `icloud auth login` is what puts it in the keyring.
    """
    apple_id = apple_id or os.environ.get("ICLOUD_APPLE_ID")
    if not apple_id:
        if not interactive:
            raise RuntimeError("No Apple ID: set ICLOUD_APPLE_ID or pass --apple-id")
        apple_id = input("Apple ID: ").strip()

    def _build(pw: Optional[str]) -> PyiCloudService:
        return PyiCloudService(apple_id, password=pw, accept_terms=accept_terms)

    try:
        api = _build(password)
    except PyiCloudAcceptTermsException as exc:
        raise RuntimeError(
            "Apple is requiring acceptance of updated iCloud terms. "
            "Re-run with --accept-terms (or accept them at icloud.com). "
            f"Underlying error: {exc}"
        ) from exc
    except (PyiCloudFailedLoginException, PyiCloud2FARequiredException):
        if password is not None or not interactive:
            raise
        pw = getpass.getpass(f"iCloud password for {apple_id}: ")
        api = _build(pw)

    if api.requires_2fa:
        if not interactive:
            raise RuntimeError("2FA required but running non-interactively")
        print("\n  Two-factor authentication required.")
        api.request_2fa_code()
        code = input("  Enter the 6-digit code from your device: ").strip()
        if not api.validate_2fa_code(code):
            raise RuntimeError("2FA code rejected by Apple")
        if not api.is_trusted_session:
            print("  Session not trusted yet; requesting trust...")
            api.trust_session()
    elif api.requires_2sa:
        if not interactive:
            raise RuntimeError("2SA required but running non-interactively")
        devices = api.trusted_devices
        for i, d in enumerate(devices):
            print(f"  [{i}] {d.get('deviceName', d.get('phoneNumber', '?'))}")
        idx = int(input("  Device index: ").strip() or "0")
        device = devices[idx]
        if not api.send_verification_code(device):
            raise RuntimeError("Failed to send verification code")
        code = input("  Code: ").strip()
        if not api.validate_verification_code(device, code):
            raise RuntimeError("Verification code rejected")

    return api


def fmt_dt(dt: Any) -> str:
    if dt is None:
        return "None"
    tz = getattr(dt, "tzinfo", None)
    return f"{dt.isoformat()} (tzinfo={tz})"


def eprint(*a: Any) -> None:
    print(*a, file=sys.stderr)
