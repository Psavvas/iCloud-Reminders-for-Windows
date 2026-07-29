"""
Verify the two fixes that the schema dump pointed to.

Root cause, common to both open items: pyicloud writes Reminders text fields
through `_encode_cloudkit_text_field`, which emits

    {"type": "ENCRYPTED_BYTES", "value": base64(utf8(text))}

but Apple stores these names as plain `STRING`. Evidence from a real account:

  - Every one of the 14 List records has  Name -> STRING ("School")
  - A hashtag created by the iPhone has   Name -> STRING ("phonetag")
  - A hashtag created by pyicloud has     Name -> ENCRYPTED_BYTES (<10 bytes>)

Apple accepts the byte form silently, so the write looks successful while the
Reminders UI cannot read it. That is why the API-made tag never rendered and
why the created list appeared as b'ZZSPIKE probe list'.

This script tests, against the live account:
  1. A hashtag written with Name as STRING (plus the Type/Imported fields the
     phone sets) -- does it render as a real tag chip?
  2. List creation with Name as STRING, in several shapes, since the very first
     attempt with a bare STRING Name was rejected with
     "byte values must be base64 encoded" and we still owe that an explanation.

Run:
    python spike\\probe_fixes.py --apple-id you@example.com
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import MARKER, confirm, connect, section  # noqa: E402
from probe_schema import describe, lookup  # noqa: E402

from pyicloud.common.cloudkit import CKModifyOperation, CKRecord  # noqa: E402
from pyicloud.services.reminders._constants import _REMINDERS_ZONE_REQ  # noqa: E402
from pyicloud.services.reminders._protocol import (  # noqa: E402
    _generate_resolution_token_map,
)


# ---------------------------------------------------------------- tags -----
def probe_tag_fix(svc, list_id: str) -> bool:
    section("1. Hashtag with Name as STRING (the shape the iPhone writes)")

    rem = svc.create(list_id=list_id, title=f"{MARKER} tag fix", desc="")
    print(f"    reminder {rem.id}")

    now_ms = int(time.time() * 1000)
    reminder_record_name = (
        rem.id if rem.id.startswith("Reminder/") else f"Reminder/{rem.id}"
    )

    # Reuse the library's own linked-child plumbing (it correctly updates
    # Reminder.HashtagIDs and the resolution token map atomically) and change
    # only the thing that was wrong: the encoding of Name.
    child_name, _resp = svc._writes._create_linked_child(  # noqa: SLF001
        reminder=rem,
        reminder_ids_attr="hashtag_ids",
        prefix="Hashtag",
        record_type="Hashtag",
        field_name="HashtagIDs",
        token_field_name="hashtagIDs",
        child_fields={
            "Name": {"type": "STRING", "value": "stringtag"},  # <-- the fix
            "Deleted": {"type": "INT64", "value": 0},
            "Type": {"type": "INT64", "value": 0},  # phone sets these
            "Imported": {"type": "INT64", "value": 0},
            "Reminder": {
                "type": "REFERENCE",
                "value": {"recordName": reminder_record_name, "action": "VALIDATE"},
            },
            "CreationDate": {"type": "TIMESTAMP", "value": now_ms},
        },
        operation_name="Create hashtag with STRING name",
    )
    print(f"    created {child_name}")

    print("\n    Our hashtag record now looks like:")
    print(json.dumps([describe(r) for r in lookup(svc, [child_name])], indent=6, default=str))

    fresh = svc.get(rem.id)
    print(f"\n    reminder.hashtag_ids: {fresh.hashtag_ids}")
    tags = svc.tags_for(fresh)
    print(f"    tags_for() -> {[getattr(t, 'name', None) for t in tags]}")

    ok, note = confirm(
        f"On '{MARKER} tag fix', does 'stringtag' now render as a real TAG chip?"
    )
    print(f"    -> {note}")

    try:
        svc.delete(svc.get(rem.id))
        print(f"    cleaned up {rem.id}")
    except Exception as exc:  # noqa: BLE001
        print(f"    cleanup failed: {exc}")
    return ok


# --------------------------------------------------------------- lists -----
def _try(svc, label: str, fields: dict) -> str | None:
    record_name = f"List/{str(uuid.uuid4()).upper()}"
    op = CKModifyOperation(
        operationType="create",
        record={"recordName": record_name, "recordType": "List", "fields": fields},
    )
    print(f"\n    -> {label}")
    try:
        resp = svc._raw.modify(operations=[op], zone_id=_REMINDERS_ZONE_REQ)  # noqa: SLF001
        errs = [
            f"{getattr(i,'recordName','?')}: {getattr(i,'serverErrorCode','')} "
            f"({getattr(i,'reason','')})"
            for i in resp.records
            if not isinstance(i, CKRecord)
        ]
        if errs:
            print(f"       REJECTED: {'; '.join(errs)}")
            return None
        print("       ACCEPTED")
        return record_name
    except Exception as exc:  # noqa: BLE001 - Apple's exact wording is the point
        print(f"       ERROR: {type(exc).__name__}: {exc}")
        payload = getattr(exc, "payload", None)
        if payload:
            print(f"       payload: {json.dumps(payload, default=str)[:600]}")
        return None


def probe_list_fix(svc) -> bool:
    section("2. List creation with Name as STRING")
    title = f"{MARKER} string list"
    now_ms = int(time.time() * 1000)

    # Shape 1 repeats the original bare-STRING attempt verbatim, so we can see
    # whether "byte values must be base64 encoded" reproduces or was incidental.
    shapes = [
        ("bare STRING Name (the original failing shape)", {"Name": {"type": "STRING", "value": title}}),
        (
            "STRING Name + the scaffolding every real list carries",
            {
                "Name": {"type": "STRING", "value": title},
                "Color": {"type": "STRING", "value": "#FF9500"},
                "BadgeEmblem": {"type": "STRING", "value": ""},
                "SortingStyle": {"type": "STRING", "value": "manual"},
                "ReminderIDs": {"type": "STRING", "value": "[]"},
                "Deleted": {"type": "INT64", "value": 0},
                "IsGroup": {"type": "INT64", "value": 0},
                "Imported": {"type": "INT64", "value": 0},
                "IsLinkedToAccount": {"type": "INT64", "value": 1},
                "ShouldAutoCategorizeItems": {"type": "INT64", "value": 0},
                "ShouldCategorizeGroceryItems": {"type": "INT64", "value": 0},
                "ResolutionTokenMap": {
                    "type": "STRING",
                    "value": _generate_resolution_token_map(["name", "color"]),
                },
            },
        ),
        (
            "minimal STRING Name + Deleted/IsGroup only",
            {
                "Name": {"type": "STRING", "value": title},
                "Deleted": {"type": "INT64", "value": 0},
                "IsGroup": {"type": "INT64", "value": 0},
            },
        ),
    ]

    created = None
    for label, fields in shapes:
        created = _try(svc, label, fields)
        if created:
            break

    if not created:
        print("\n    Every STRING shape was rejected.")
        return False

    titles = [l.title for l in svc.lists()]
    exact = title in titles
    print(f"\n    exact title match in lists()? {exact}")
    print(f"    titles now: {titles}")
    print("\n    Raw record we just created:")
    print(json.dumps([describe(r) for r in lookup(svc, [created])], indent=6, default=str))

    ok, note = confirm(
        f"Does a list named exactly '{title}' (clean text, no b'...') appear on your iPhone?"
    )
    print(f"    -> {note}")

    from list_create_experiment import delete_list

    print("    cleanup:", delete_list(svc, created, None))
    return ok and exact


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--apple-id", default=os.environ.get("ICLOUD_APPLE_ID"))
    p.add_argument("--list", default="Inbox")
    p.add_argument("--accept-terms", action="store_true")
    args = p.parse_args()

    api = connect(apple_id=args.apple_id, accept_terms=args.accept_terms)
    svc = api.reminders

    lists = list(svc.lists())
    target = next((l for l in lists if l.title == args.list), None)
    if target is None:
        usable = [l for l in lists if not l.is_group]
        target = usable[0] if usable else None
    if target is None:
        print("no usable list")
        return 1
    print(f"(using {target.title!r})")

    tag_ok = probe_tag_fix(svc, target.id)
    list_ok = probe_list_fix(svc)

    section("SUMMARY")
    print(f"    Item 5 (tags) fixed by STRING encoding:  {'YES' if tag_ok else 'NO'}")
    print(f"    Item 6 (list creation) works cleanly:    {'YES' if list_ok else 'NO'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
