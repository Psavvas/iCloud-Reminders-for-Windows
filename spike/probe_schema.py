"""
Follow-up probe for the two unresolved Phase 1 items.

Two corrections over the first version of this file:

1. Item 6's `BAD_REQUEST (byte values must be base64 encoded)` is explained.
   pyicloud encodes Reminders text fields as
       {"type": "ENCRYPTED_BYTES", "value": base64(utf8(text))}
   via `_encode_cloudkit_text_field` -- see its use for Hashtag.Name in
   `_writes.create_hashtag`. The first attempt sent List.Name as a plain
   STRING, which is exactly the error Apple returned. This version sends the
   encoded form first.

2. The record dumps came back empty because they scanned /changes/zone with a
   page cap, and freshly-created records sort to the end of the change feed --
   past the cutoff. `tags_for()` doesn't do that; it uses /records/lookup by
   exact record name. So does this file now.

Run:
    python spike\\probe_schema.py --apple-id you@example.com
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import MARKER, connect, section  # noqa: E402

from pyicloud.common.cloudkit import CKModifyOperation, CKRecord  # noqa: E402
from pyicloud.services.reminders._constants import _REMINDERS_ZONE_REQ  # noqa: E402
from pyicloud.services.reminders._protocol import (  # noqa: E402
    _encode_cloudkit_text_field,
)


def lookup(svc, record_names: list[str]) -> list[CKRecord]:
    """Fetch exact records by name. No pagination, no cutoff."""
    if not record_names:
        return []
    resp = svc._raw.lookup(  # noqa: SLF001
        record_names=record_names, zone_id=_REMINDERS_ZONE_REQ
    )
    return [r for r in resp.records if isinstance(r, CKRecord)]


def describe(rec: CKRecord) -> dict:
    """Field-by-field: declared CloudKit type + a safe sample of the value."""
    fields = {}
    for key in rec.fields.keys():
        wrapper = rec.fields.get_field(key)
        val = getattr(wrapper, "value", None)
        if isinstance(val, (bytes, bytearray)):
            try:
                sample = f"<{len(val)} bytes> utf8={val.decode('utf-8')!r}"
            except UnicodeDecodeError:
                sample = f"<{len(val)} bytes, not utf-8>"
        else:
            s = str(val)
            sample = s if len(s) <= 100 else s[:97] + "..."
        fields[key] = {"type": getattr(wrapper, "type", "?"), "sample": sample}
    return {
        "recordName": rec.recordName,
        "recordType": rec.recordType,
        "recordChangeTag": rec.recordChangeTag,
        "fields": fields,
    }


def dump_list_schema(svc) -> list[dict]:
    section("A. Real 'List' record schema (what Apple actually stores)")
    lists = list(svc.lists())
    print(f"    {len(lists)} lists; looking up raw records by name")
    recs = lookup(svc, [l.id for l in lists])
    described = [describe(r) for r in recs]
    print(f"    {len(described)} raw List records retrieved\n")

    union: dict[str, set] = {}
    for d in described:
        for k, v in d["fields"].items():
            union.setdefault(k, set()).add(v["type"])
    print("    Field name -> CloudKit type(s) across all lists:")
    for k in sorted(union):
        print(f"      {k:<28} {sorted(union[k])}")

    if described:
        print("\n    Full dump of one list:")
        print(json.dumps(described[0], indent=6, default=str))
    return described


def retry_list_create(svc, template: dict | None) -> None:
    section("B. Retry list creation with correct field encoding")
    title = f"{MARKER} probe list"
    tmpl = (template or {}).get("fields", {})
    name_type = tmpl.get("Name", {}).get("type")
    print(f"    Apple stores List.Name as: {name_type or 'unknown'}")

    shapes: list[tuple[str, dict]] = [
        # The fix: same encoding pyicloud uses for Hashtag.Name.
        ("Name as ENCRYPTED_BYTES (base64)", {"Name": _encode_cloudkit_text_field(title)}),
        (
            "Name as ENCRYPTED_BYTES + Color/flags",
            {
                "Name": _encode_cloudkit_text_field(title),
                "Color": _encode_cloudkit_text_field("#FF9500"),
                "Deleted": {"type": "INT64", "value": 0},
                "IsGroup": {"type": "INT64", "value": 0},
            },
        ),
    ]
    # If the account reports some other type for Name, try that too.
    if name_type and name_type not in ("ENCRYPTED_BYTES",):
        shapes.append(
            (f"Name as observed type {name_type}", {"Name": {"type": name_type, "value": title}})
        )

    created = None
    for label, fields in shapes:
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
            else:
                print("       ACCEPTED")
                created = record_name
                break
        except Exception as exc:  # noqa: BLE001 - Apple's wording is the evidence
            print(f"       ERROR: {type(exc).__name__}: {exc}")
            payload = getattr(exc, "payload", None)
            if payload:
                print(f"       payload: {json.dumps(payload, default=str)[:800]}")

    if not created:
        print("\n    No shape accepted. Item 6 = not possible via this path.")
        return

    titles = [l.title for l in svc.lists()]
    print(f"\n    visible via lists()? {title in titles}")
    print(f"    titles now: {titles}")
    print("\n    >>> CHECK YOUR IPHONE: does the list appear in Reminders? <<<")
    input("    press Enter to clean it up... ")
    from list_create_experiment import delete_list

    print("    cleanup:", delete_list(svc, created, None))


def dump_tag_schema(svc, list_id: str) -> None:
    section("C. Hashtag records: API-created vs phone-created")

    rem = svc.create(list_id=list_id, title=f"{MARKER} tag probe", desc="")
    print(f"    created reminder {rem.id}")
    tag = svc.create_hashtag(rem, "spikeprobe")
    print(f"    create_hashtag -> {tag.id}")

    ours = lookup(svc, [tag.id])
    print("\n    OUR hashtag record (looked up by name):")
    print(json.dumps([describe(r) for r in ours], indent=6, default=str))

    fresh = svc.get(rem.id)
    print(f"\n    reminder.hashtag_ids after our create: {fresh.hashtag_ids}")
    rem_rec = lookup(svc, [rem.id])
    if rem_rec:
        d = describe(rem_rec[0])
        print("\n    OUR reminder record, tag/title-related fields:")
        print(
            json.dumps(
                {
                    k: v
                    for k, v in d["fields"].items()
                    if "hash" in k.lower() or "tag" in k.lower() or "title" in k.lower()
                },
                indent=6,
                default=str,
            )
        )

    print("\n    >>> NOW ON YOUR IPHONE <<<")
    print(f"    1. Open the reminder '{MARKER} tag probe'")
    print("    2. Add a tag by hand: type #phonetag in the title field")
    print("    3. Confirm it shows as a highlighted tag chip, then wait ~5s")
    input("    ...then press Enter here. ")

    after = svc.get(rem.id)
    print(f"\n    reminder.hashtag_ids after the phone edit: {after.hashtag_ids}")
    new_ids = [i for i in (after.hashtag_ids or []) if i not in (fresh.hashtag_ids or [])]
    print(f"    new hashtag ids from the phone: {new_ids}")

    if new_ids:
        names = [i if "/" in i else f"Hashtag/{i}" for i in new_ids]
        print("\n    PHONE-created hashtag record(s):")
        print(json.dumps([describe(r) for r in lookup(svc, names)], indent=6, default=str))
    else:
        print("\n    The phone's tag did NOT land in Reminder.HashtagIDs.")
        print("    That means tags are not (only) modelled as Hashtag records.")

    rem_rec2 = lookup(svc, [rem.id])
    if rem_rec2:
        d = describe(rem_rec2[0])
        print("\n    Reminder record AFTER the phone edit, tag/title fields:")
        print(
            json.dumps(
                {
                    k: v
                    for k, v in d["fields"].items()
                    if "hash" in k.lower() or "tag" in k.lower() or "title" in k.lower()
                },
                indent=6,
                default=str,
            )
        )
        print("\n    ^ Compare TitleDocument before/after. If the phone's tag is")
        print("      encoded inside the CRDT title document, that is what makes a")
        print("      tag 'real' -- and writing a Hashtag record alone never will.")

    try:
        svc.delete(svc.get(rem.id))
        print(f"\n    cleaned up {rem.id}")
    except Exception as exc:  # noqa: BLE001
        print(f"\n    cleanup failed: {exc}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--apple-id", default=os.environ.get("ICLOUD_APPLE_ID"))
    p.add_argument("--list", help="List name to use for the tag probe")
    p.add_argument("--accept-terms", action="store_true")
    p.add_argument("--skip-tags", action="store_true")
    p.add_argument("--skip-list", action="store_true")
    args = p.parse_args()

    api = connect(apple_id=args.apple_id, accept_terms=args.accept_terms)
    svc = api.reminders

    described = dump_list_schema(svc)
    if not args.skip_list:
        retry_list_create(svc, described[0] if described else None)

    if not args.skip_tags:
        lists = list(svc.lists())
        target = None
        if args.list:
            target = next((l for l in lists if l.title == args.list), None)
        if target is None:
            usable = [l for l in lists if not l.is_group]
            for wanted in ("inbox", "reminders"):
                target = next((l for l in usable if (l.title or "").lower() == wanted), None)
                if target:
                    break
            target = target or (usable[0] if usable else None)
        if target is None:
            print("no usable list for tag probe")
            return 1
        print(f"\n(using list {target.title!r} for the tag probe)")
        dump_tag_schema(svc, target.id)

    return 0


if __name__ == "__main__":
    sys.exit(main())
