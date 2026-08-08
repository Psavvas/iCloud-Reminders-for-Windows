"""
Try, in escalating order, to actually remove a List record from iCloud.

find_stray_lists.py established that `ZZSPIKE new list` is live in the account
and that a soft delete -- an `update` writing Deleted=1 -- is accepted with a
200 and changes nothing. That is Apple's established shape here: it takes list
writes and discards them.

But that is not the end of what can be tried, for two reasons.

The first is that the force delete never ran. list_create_experiment.delete_list
falls back to forceDelete only when the soft delete *raises*, and a 200 that
does nothing does not raise, so the one operation most likely to work was
skipped every time.

The second is that Apple's answer was thrown away. delete_list ignores what
modify() returns, and _attempt in the same module notes that a 200 can carry a
per-record serverErrorCode. If Apple is refusing for a stated reason, it has
been saying so and nobody has read it.

So this walks the four operation types CloudKit accepts for removing a record,
prints Apple's verbatim response to each, and re-reads the record afterwards to
see what actually changed. It stops at the first one that works. If all four
fail, the output is the evidence that hiding it client-side is the only option
left, rather than a guess that it is.

Usage (Windows PowerShell, from the repo root, with the venv active):

    python spike\\force_delete_list.py --apple-id you@example.com
    python spike\\force_delete_list.py --apple-id you@example.com --go

Reports what it would try by default; --go actually sends the writes.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import MARKER, connect  # noqa: E402


def _zone():
    from pyicloud.services.reminders._constants import _REMINDERS_ZONE_REQ

    return _REMINDERS_ZONE_REQ


def read_record(svc: Any, record_name: str) -> tuple[Optional[Any], str]:
    """
    (record, note). A record that is genuinely gone comes back as an error item
    rather than a CKRecord, which is the outcome being hoped for here.
    """
    from pyicloud.common.cloudkit import CKRecord

    try:
        resp = svc._raw.lookup(record_names=[record_name], zone_id=_zone())  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        return None, f"lookup raised: {type(exc).__name__}: {exc}"

    for item in resp.records:
        if isinstance(item, CKRecord) and item.recordName == record_name:
            return item, ""
        code = getattr(item, "serverErrorCode", None)
        if code:
            return None, f"lookup says {code} ({getattr(item, 'reason', '')})"
    return None, "not in the lookup response at all"


def describe(rec: Any) -> str:
    if rec is None:
        return "gone"
    deleted = bool(rec.fields.get_value("Deleted"))
    return f"present, Deleted={deleted}, changeTag={rec.recordChangeTag}"


def attempt(svc: Any, op: Any, label: str) -> str:
    """Send one operation and report Apple's verbatim answer, 200s included."""
    from pyicloud.common.cloudkit import CKRecord

    try:
        resp = svc._raw.modify(operations=[op], zone_id=_zone())  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001 - Apple's complaint is the point
        payload = getattr(exc, "payload", None)
        return f"raised {type(exc).__name__}: {exc}" + (f" payload={payload}" if payload else "")

    # A 200 is not success. Per-record errors ride inside it, which is exactly
    # what went unnoticed until now.
    notes = []
    for item in resp.records:
        if isinstance(item, CKRecord):
            notes.append(f"echoed {item.recordName}")
        else:
            code = getattr(item, "serverErrorCode", None)
            reason = getattr(item, "reason", None)
            if code or reason:
                notes.append(f"ERROR {code} ({reason})")
    return "200; " + ("; ".join(notes) if notes else "empty response body")


def build_ops(record_name: str, change_tag: Optional[str]) -> list[tuple[str, Any]]:
    """
    The ladder, gentlest first.

    forceUpdate and forceDelete drop the change-tag precondition, which is worth
    trying separately: if Apple were rejecting on a stale tag it would say so,
    but it answers 200, so the tag is probably not what is stopping this.
    """
    from pyicloud.common.cloudkit import CKModifyOperation

    now_ms = int(time.time() * 1000)
    flag = {
        "Deleted": {"type": "INT64", "value": 1},
        "LastModifiedDate": {"type": "TIMESTAMP", "value": now_ms},
    }
    bare = {"recordName": record_name, "recordType": "List"}

    return [
        (
            "forceUpdate Deleted=1",
            CKModifyOperation(operationType="forceUpdate", record={**bare, "fields": flag}),
        ),
        (
            "delete",
            CKModifyOperation(operationType="delete", record={**bare, "recordChangeTag": change_tag}),
        ),
        (
            "forceDelete",
            CKModifyOperation(operationType="forceDelete", record=bare),
        ),
    ]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apple-id", default=os.environ.get("ICLOUD_APPLE_ID"))
    p.add_argument("--accept-terms", action="store_true")
    p.add_argument("--marker", default=MARKER)
    p.add_argument("--record-name", help="Target one record instead of searching")
    p.add_argument("--go", action="store_true", help="Actually send the writes")
    args = p.parse_args()

    svc = connect(apple_id=args.apple_id, accept_terms=args.accept_terms).reminders

    if args.record_name:
        targets = [args.record_name]
    else:
        needle = args.marker.lower()
        targets = [l.id for l in svc.lists() if needle in str(l.title).lower()]

    if not targets:
        print(f"Nothing matching {args.marker!r}. Nothing to do.")
        return 0

    failed = []
    for record_name in targets:
        rec, note = read_record(svc, record_name)
        print(f"\n{record_name}\n  before: {describe(rec)}{(' -- ' + note) if note else ''}")
        if rec is None:
            print("  already gone; skipping.")
            continue

        ops = build_ops(record_name, rec.recordChangeTag)
        if not args.go:
            print("  would try, in order: " + ", ".join(label for label, _ in ops))
            print("  (re-run with --go to send them)")
            continue

        won = False
        for label, op in ops:
            print(f"\n  {label}")
            print(f"    apple: {attempt(svc, op, label)}")
            after, note = read_record(svc, record_name)
            print(f"    after: {describe(after)}{(' -- ' + note) if note else ''}")
            if after is None or bool(after.fields.get_value("Deleted")):
                print(f"\n  -> {label} worked.")
                won = True
                break
            # Each op needs the tag as it stands now, not as it was at the start.
            rec = after

        if not won:
            failed.append(record_name)

    if failed:
        print(
            "\nEvery operation was accepted and none of them changed anything, "
            "which is the same behaviour list create, rename and delete all show.\n"
            "The record cannot be removed through this API. Hiding it in the app "
            "is the remaining option, and that one does not depend on Apple."
        )
        return 1

    if args.go:
        print("\nDone. The app rebuilds its list table from the server every sync.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
