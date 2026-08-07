"""
Find -- and optionally remove -- List records the spike left in the account.

Item 6 of the Phase 1 spike created a `List` record through the raw CloudKit
endpoint to test whether list creation was possible. Apple accepted the write.
The cleanup phase was meant to take it away again and at least one survived:
`ZZSPIKE new list` still comes back from `lists()`.

It shows up on Windows and nowhere else because the spike wrote `Name` as
ENCRYPTED_BYTES rather than the STRING form an iPhone writes. Apple's own
clients ignore records shaped like that -- the same wall the tag-writing
experiment hit -- while this app decodes them on purpose, since that decoding is
what stops legitimate lists rendering as b'Groceries'.

Two things could have kept it visible, and the report below distinguishes them:

  * Deleted is unset -- cleanup never ran, or Apple rejected it. The record is
    live and deleting it here is the fix.
  * Deleted is set -- the record *was* soft-deleted, and the filter that should
    be hiding it (ICloudClient._deleted_list_ids) is failing silently. Deleting
    it again would achieve nothing; the bug is in the app and I should fix it
    there instead.

Reports by default and changes nothing. Pass --delete to act on what it found.

Usage (Windows PowerShell, from the repo root):

    py -m venv .venv
    .\\.venv\\Scripts\\Activate.ps1
    pip install -r spike\\requirements.txt
    python spike\\find_stray_lists.py --apple-id you@example.com
    python spike\\find_stray_lists.py --apple-id you@example.com --delete
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import MARKER, connect  # noqa: E402
from list_create_experiment import delete_list  # noqa: E402


def _raw_records(svc: Any, ids: list[str]) -> dict[str, Any]:
    """
    The typed model drops Deleted and recordChangeTag; both matter here.

    Takes the *reminders* service, not PyiCloudService -- `_raw` hangs off the
    former. Everything below is consistent about that, because mixing the two is
    what broke the first version of this script.
    """
    from pyicloud.common.cloudkit import CKRecord
    from pyicloud.services.reminders._constants import _REMINDERS_ZONE_REQ

    resp = svc._raw.lookup(  # noqa: SLF001
        record_names=ids, zone_id=_REMINDERS_ZONE_REQ
    )
    return {
        rec.recordName: rec
        for rec in resp.records
        if isinstance(rec, CKRecord)
    }


def _describe(rec: Any) -> tuple[bool, Optional[str], str]:
    """(deleted, changeTag, how Name is stored) for one raw List record."""
    deleted = bool(rec.fields.get_value("Deleted")) if rec is not None else False
    tag = getattr(rec, "recordChangeTag", None) if rec is not None else None

    encoding = "unknown"
    if rec is not None:
        # get_field, not get: get returns the wrapper, whose .type is None and
        # whose .value is still base64. get_field gives the declared type and
        # the decoded value. The type is the distinction that explains the whole
        # symptom -- an iPhone writes STRING, the spike wrote ENCRYPTED_BYTES.
        name = rec.fields.get_field("Name")
        if name is not None:
            encoding = name.type or "unknown"
    return deleted, tag, encoding


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apple-id", default=os.environ.get("ICLOUD_APPLE_ID"))
    p.add_argument("--accept-terms", action="store_true")
    p.add_argument(
        "--marker",
        default=MARKER,
        help=f"Case-insensitive substring identifying strays (default {MARKER})",
    )
    p.add_argument(
        "--delete",
        action="store_true",
        help="Actually remove what was found. Without this, nothing is written.",
    )
    args = p.parse_args()

    # Resolved once. delete_list, _raw_records and lists() all want the
    # reminders service -- run_spike.py passes exactly this -- and reaching for
    # PyiCloudService instead is an AttributeError only at the point of writing.
    svc = connect(apple_id=args.apple_id, accept_terms=args.accept_terms).reminders

    everything = list(svc.lists())
    needle = args.marker.lower()
    # str() rather than .title directly: a record written as ENCRYPTED_BYTES
    # stringifies to the literal b'...' form, and that is exactly what is being
    # looked for here.
    strays = [l for l in everything if needle in str(l.title).lower()]

    print(f"\n{len(everything)} List records in the account.")
    if not strays:
        print(f"None contain {args.marker!r}. Nothing to do.")
        return 0

    raw = _raw_records(svc, [l.id for l in strays])

    print(f"{len(strays)} matching {args.marker!r}:\n")
    live: list[tuple[Any, Optional[str]]] = []
    for l in strays:
        rec = raw.get(l.id)
        deleted, tag, encoding = _describe(rec)
        print(f"  title          {str(l.title)!r}")
        print(f"  recordName     {l.id}")
        print(f"  Name stored as {encoding}")
        print(f"  Deleted flag   {deleted}")
        print(f"  changeTag      {tag}")
        if deleted:
            print(
                "  -> Already soft-deleted. Deleting again will not help: the app's\n"
                "     _deleted_list_ids filter should be hiding this and is not.\n"
                "     Send me this output -- the fix belongs in the app."
            )
        else:
            print("  -> Live record. Deleting it here is the fix.")
            live.append((l, tag))
        print()

    if not args.delete:
        print("Reported only. Re-run with --delete to remove the live records above.")
        return 0

    if not live:
        print("Nothing here is a live record, so --delete has nothing safe to do.")
        return 0

    for l, tag in live:
        print(f"Deleting {str(l.title)!r} ({l.id}) ... ", end="", flush=True)
        print(delete_list(svc, l.id, tag))

    # Read it back rather than trusting the message above. modify() can return
    # 200 with per-record errors embedded -- list_create_experiment says so in
    # as many words -- so "soft-deleted" means the call did not raise, not that
    # the record changed. The flag is the only answer that counts.
    print("\nVerifying:")
    after = _raw_records(svc, [l.id for l, _ in live])
    stuck = []
    for l, _ in live:
        rec = after.get(l.id)
        if rec is None:
            print(f"  {l.id}: gone from the zone entirely.")
            continue
        deleted, _, _ = _describe(rec)
        print(f"  {l.id}: Deleted flag is now {deleted}")
        if not deleted:
            stuck.append(l)

    if stuck:
        print(
            "\nApple accepted the request and the flag did not change, which puts\n"
            "this with list rename and delete on the list of writes it takes and\n"
            "ignores. Send me this output -- the remaining option is to hide it in\n"
            "the app, which is entirely under our control."
        )
        return 1

    print(
        "\nDone. The Windows app rebuilds its list table from the server on every\n"
        "sync, so the list should disappear within one sync cycle -- or straight\n"
        "away via Settings -> Re-download everything."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
