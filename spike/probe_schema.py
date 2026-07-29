"""
Follow-up probe for the two unresolved Phase 1 items.

Item 6 came back `BAD_REQUEST (byte values must be base64 encoded)`. That is not
Apple saying "you may not create lists" -- it's Apple rejecting the *shape* of
the record we sent. The fix is to stop guessing: read the exact field types off
a List record that already exists in the account, then mirror them.

Item 5's hashtag was created and read back through the API but never rendered on
the phone. Same approach: compare an API-created Hashtag record against one the
Reminders app itself produced, field by field.

Run:
    python spike\\probe_schema.py --apple-id you@example.com

Nothing here is destructive except the clearly-marked create attempts, which are
cleaned up at the end.
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

from pyicloud.common.cloudkit import (  # noqa: E402
    CKModifyOperation,
    CKRecord,
    CKZoneChangesZoneReq,
)
from pyicloud.services.reminders._constants import (  # noqa: E402
    _REMINDERS_ZONE,
    _REMINDERS_ZONE_REQ,
)


def raw_records(svc, record_type: str, limit: int = 400) -> list[CKRecord]:
    """Pull raw CKRecords of one type, bypassing the typed mapper."""
    out: list[CKRecord] = []
    token = None
    for _ in range(10):  # page guard
        resp = svc._raw.changes(  # noqa: SLF001
            zone_req=CKZoneChangesZoneReq(
                zoneID=_REMINDERS_ZONE,
                desiredRecordTypes=[record_type],
                syncToken=token,
            )
        )
        if not resp.zones:
            break
        more = False
        for zone in resp.zones:
            for rec in zone.records:
                if isinstance(rec, CKRecord) and rec.recordType == record_type:
                    out.append(rec)
            token = zone.syncToken
            more = more or bool(zone.moreComing)
        if not more or len(out) >= limit:
            break
    return out


def describe(rec: CKRecord) -> dict:
    """Field-by-field description: declared CloudKit type + a safe sample."""
    fields = {}
    for key in rec.fields.keys():
        wrapper = rec.fields.get_field(key)
        ftype = getattr(wrapper, "type", "?")
        val = getattr(wrapper, "value", None)
        if isinstance(val, (bytes, bytearray)):
            sample = f"<{len(val)} bytes>"
        else:
            s = str(val)
            sample = s if len(s) <= 80 else s[:77] + "..."
        fields[key] = {"type": ftype, "sample": sample}
    return {
        "recordName": rec.recordName,
        "recordType": rec.recordType,
        "recordChangeTag": rec.recordChangeTag,
        "fields": fields,
    }


def dump_list_schema(svc) -> list[dict]:
    section("A. Real 'List' record schema (what Apple actually stores)")
    recs = raw_records(svc, "List")
    print(f"    {len(recs)} List records\n")
    described = [describe(r) for r in recs]

    # Union of field names -> types seen, so we can spot required/typed fields.
    union: dict[str, set] = {}
    for d in described:
        for k, v in d["fields"].items():
            union.setdefault(k, set()).add(v["type"])
    print("    Field types across all lists:")
    for k in sorted(union):
        print(f"      {k:<28} {sorted(union[k])}")

    if described:
        print("\n    Full dump of first list:")
        print(json.dumps(described[0], indent=6, default=str))
    return described


def retry_list_create(svc, template: dict | None) -> None:
    section("B. Retry list creation, mirroring the observed schema")
    if not template:
        print("    no template list available; skipping")
        return

    title = f"{MARKER} probe list"
    tmpl_fields = template["fields"]

    # Shape 1: clone every field type from a real list, substituting our values
    # and dropping server-managed / identity fields.
    skip = {
        "Count",
        "ReminderIDs",
        "ParticipantIDs",
        "ShareeIDs",
        "CreationDate",
        "LastModifiedDate",
        "ResolutionTokenMap",
    }
    cloned: dict = {}
    for name, meta in tmpl_fields.items():
        if name in skip:
            continue
        ftype = meta["type"]
        if name == "Name":
            cloned[name] = {"type": ftype, "value": title}
        elif ftype in ("STRING",):
            continue  # don't blindly copy other strings
        elif ftype in ("INT64",):
            cloned[name] = {"type": ftype, "value": 0}

    shapes: list[tuple[str, dict]] = []
    if cloned:
        shapes.append(("schema-cloned from real list", cloned))

    # Shape 2: whatever type Apple declares for Name, on its own.
    name_type = tmpl_fields.get("Name", {}).get("type")
    if name_type:
        shapes.append(
            (f"Name only, as declared type {name_type}", {"Name": {"type": name_type, "value": title}})
        )

    created = None
    for label, fields in shapes:
        record_name = f"List/{str(uuid.uuid4()).upper()}"
        op = CKModifyOperation(
            operationType="create",
            record={"recordName": record_name, "recordType": "List", "fields": fields},
        )
        print(f"\n    -> {label}")
        print(f"       fields: {json.dumps(fields, default=str)[:300]}")
        try:
            resp = svc._raw.modify(operations=[op], zone_id=_REMINDERS_ZONE_REQ)  # noqa: SLF001
            errs = [
                f"{getattr(i, 'recordName', '?')}: {getattr(i, 'serverErrorCode', '')} "
                f"({getattr(i, 'reason', '')})"
                for i in resp.records
                if not isinstance(i, CKRecord)
            ]
            if errs:
                print(f"       REJECTED: {'; '.join(errs)}")
            else:
                print("       ACCEPTED")
                created = record_name
                break
        except Exception as exc:  # noqa: BLE001 - print Apple's words verbatim
            print(f"       ERROR: {type(exc).__name__}: {exc}")
            payload = getattr(exc, "payload", None)
            if payload:
                print(f"       payload: {json.dumps(payload, default=str)[:800]}")

    if created:
        titles = [l.title for l in svc.lists()]
        print(f"\n    visible via lists()? {f'{MARKER} probe list' in titles}")
        print("    >>> CHECK YOUR IPHONE: did the list appear? <<<")
        input("    press Enter to clean it up... ")
        from list_create_experiment import delete_list

        print("    cleanup:", delete_list(svc, created, None))


def dump_tag_schema(svc, list_id: str) -> None:
    section("C. Hashtag records: API-created vs phone-created")

    before = {r.recordName: describe(r) for r in raw_records(svc, "Hashtag")}
    print(f"    {len(before)} existing Hashtag records")
    if before:
        first = next(iter(before.values()))
        print("\n    Example of an EXISTING hashtag record:")
        print(json.dumps(first, indent=6, default=str))

    rem = svc.create(list_id=list_id, title=f"{MARKER} tag probe", desc="")
    print(f"\n    created reminder {rem.id}")
    tag = svc.create_hashtag(rem, "spikeprobe")
    print(f"    create_hashtag -> {getattr(tag, 'id', '?')}")

    after = {r.recordName: describe(r) for r in raw_records(svc, "Hashtag")}
    new = [v for k, v in after.items() if k not in before]
    print("\n    OUR newly created hashtag record:")
    for d in new:
        print(json.dumps(d, indent=6, default=str))

    # The reminder side of the link matters as much as the Hashtag record.
    rem_raw = [r for r in raw_records(svc, "Reminder") if r.recordName.endswith(rem.id.split("/")[-1])]
    if rem_raw:
        d = describe(rem_raw[0])
        interesting = {
            k: v for k, v in d["fields"].items()
            if "hash" in k.lower() or "tag" in k.lower() or "Title" in k
        }
        print("\n    Tag-related fields on OUR reminder record:")
        print(json.dumps(interesting, indent=6, default=str))

    print("\n    >>> NOW ON YOUR IPHONE <<<")
    print(f"    1. Open the reminder '{MARKER} tag probe'")
    print("    2. Add a tag to it manually (type # then 'phonetag')")
    print("    3. Make sure it saves and shows as a tag chip")
    input("    ...then press Enter here. ")

    final = {r.recordName: describe(r) for r in raw_records(svc, "Hashtag")}
    phone_made = [v for k, v in final.items() if k not in after]
    print("\n    PHONE-created hashtag record(s):")
    if not phone_made:
        print("      none found -- the phone may not have synced yet, or tags")
        print("      are not stored as Hashtag records at all.")
    for d in phone_made:
        print(json.dumps(d, indent=6, default=str))

    rem_raw2 = [r for r in raw_records(svc, "Reminder") if r.recordName.endswith(rem.id.split("/")[-1])]
    if rem_raw2:
        d = describe(rem_raw2[0])
        interesting = {
            k: v for k, v in d["fields"].items()
            if "hash" in k.lower() or "tag" in k.lower() or "Title" in k
        }
        print("\n    Tag-related fields on the reminder AFTER the phone edit:")
        print(json.dumps(interesting, indent=6, default=str))

    print("\n    Compare the two dumps above: any field our version is missing")
    print("    is what makes a tag 'real' to the Reminders UI.")

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
