"""
Find -- and optionally remove -- Hashtag records the spike left in the account.

The symptom this exists for: a tag that Apple's Reminders app will not delete.
Tapping delete appears to do nothing, and renaming it produces a *second* tag
under the new name while the original stays put.

That is what a rename is, underneath -- create the new hashtag, delete the old
one -- so a rename that half-lands is a delete that silently failed, twice over.

Why a delete would fail on a tag but not on a reminder is the interesting part.
A tag is not an object in its own right: it is one `Hashtag` record per tagged
reminder, plus that reminder's `HashtagIDs`. Deleting the tag means walking the
reminders that carry it and stripping it from each. A `Hashtag` whose reminder
the phone cannot show has nothing for that walk to touch, so the tag survives in
the tag list with no reachable copy to remove -- and the phone reports success.

Phase 1 left exactly that shape behind. `probe_schema.dump_tag_schema` asked for
a tag to be typed by hand on `ZZSPIKE tag probe`, to compare a phone-written
record against an API-written one, and its cleanup then deleted the *reminder*.
Reminder deletion here is a soft delete (`Deleted = 1`), and nothing ever
deleted the hashtags. `run_spike` check 5 and `probe_fixes` are the same story
with `spiketag` and `stringtag`.

So this reports every Hashtag record in the zone, resolves the reminder each one
points at, and says which of these it is:

  * the reminder is live          -- the tag is genuinely in use. Left alone
                                     unless you pass --include-live.
  * the reminder is soft-deleted  -- orphaned by a delete that only got halfway.
                                     This is the one that jams the phone's UI.
  * the reminder is gone entirely -- same, with nothing left to point at.
  * the hashtag is already
    Deleted = 1                   -- removed at the record level. If the phone
                                     still shows it, the record is not what is
                                     keeping it there; send me the output.

Reports by default and changes nothing. --go acts, and needs --name so that a
mistyped flag cannot strip every tag in the account.

Usage (Windows PowerShell, from the repo root):

    py -m venv .venv
    .\\.venv\\Scripts\\Activate.ps1
    pip install -r spike\\requirements.txt

    python spike\\find_stray_tags.py --apple-id you@example.com
    python spike\\find_stray_tags.py --apple-id you@example.com --name "phone tag"
    python spike\\find_stray_tags.py --apple-id you@example.com --name "phone tag" --go
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import connect  # noqa: E402

# Names Phase 1 is known to have written or asked for. Only used to flag them in
# the report -- nothing is ever deleted without --name naming it.
SPIKE_NAMES = {"spiketag", "stringtag", "spikeprobe", "phonetag", "phone tag"}

LOOKUP_CHUNK = 100


def _zone_req():
    from pyicloud.services.reminders._constants import _REMINDERS_ZONE_REQ

    return _REMINDERS_ZONE_REQ


# ----------------------------------------------------------------- reading ---
def all_hashtag_records(svc: Any) -> list[Any]:
    """
    Every Hashtag record in the Reminders zone.

    /changes/zone with no sync token is a full snapshot -- it is how `lists()`
    reads lists. The one thing it will not tolerate is being read a page at a
    time: probe_schema found records missing because a capped scan stopped
    before the end of the feed, where the newest records sort. So this follows
    syncToken/moreComing all the way out.

    Tombstones come back as CKTombstoneRecord rather than CKRecord and are
    skipped on purpose: a tombstone is a record that is already gone.
    """
    from pyicloud.common.cloudkit import CKRecord, CKZoneChangesZoneReq
    from pyicloud.services.reminders._constants import _REMINDERS_ZONE

    out: list[Any] = []
    seen: set[str] = set()
    errors: list[str] = []
    token: Optional[str] = None
    more = True

    while more:
        resp = svc._raw.changes(  # noqa: SLF001
            zone_req=CKZoneChangesZoneReq(
                zoneID=_REMINDERS_ZONE,
                desiredRecordTypes=["Hashtag"],
                syncToken=token,
            )
        )
        if not resp.zones:
            break

        previous = token
        more = False
        for zone in resp.zones:
            for rec in zone.records:
                if isinstance(rec, CKRecord):
                    if rec.recordType == "Hashtag" and rec.recordName not in seen:
                        seen.add(rec.recordName)
                        out.append(rec)
                    continue
                code = getattr(rec, "serverErrorCode", None)
                if code:
                    errors.append(f"{getattr(rec, 'recordName', '?')}: {code}")
            token = zone.syncToken
            more = more or bool(zone.moreComing)

        # A token that does not advance would page forever; stop instead.
        if token == previous:
            break

    if errors:
        print(f"  (the change feed returned {len(errors)} error items: "
              f"{'; '.join(errors[:5])})")
    return out


def tag_name(rec: Any) -> tuple[str, str]:
    """
    (text, declared CloudKit type) for a Hashtag's Name.

    get_field rather than get: the wrapper carries the declared type, and STRING
    vs ENCRYPTED_BYTES is the difference between a record the phone wrote and
    one pyicloud did.
    """
    field = rec.fields.get_field("Name")
    if field is None:
        return "", "missing"
    value = getattr(field, "value", None)
    if isinstance(value, (bytes, bytearray)):
        try:
            text = bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            text = repr(bytes(value))
    else:
        text = "" if value is None else str(value)
    return text, getattr(field, "type", None) or "unknown"


def reminder_ref(rec: Any) -> str:
    field = rec.fields.get_field("Reminder")
    value = getattr(field, "value", None)
    return getattr(value, "recordName", "") or ""


def lookup_records(svc: Any, record_names: list[str]) -> dict[str, Any]:
    """Exact records by name, chunked. Anything absent is simply not in the map."""
    from pyicloud.common.cloudkit import CKRecord

    found: dict[str, Any] = {}
    for i in range(0, len(record_names), LOOKUP_CHUNK):
        chunk = record_names[i : i + LOOKUP_CHUNK]
        try:
            resp = svc._raw.lookup(record_names=chunk, zone_id=_zone_req())  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            print(f"  lookup of {len(chunk)} records raised: "
                  f"{type(exc).__name__}: {exc}")
            continue
        for rec in resp.records:
            if isinstance(rec, CKRecord):
                found[rec.recordName] = rec
    return found


def is_deleted(rec: Optional[Any]) -> bool:
    return bool(rec.fields.get_value("Deleted")) if rec is not None else False


def verdict(hashtag_rec: Any, reminder_rec: Optional[Any], has_ref: bool) -> tuple[str, bool]:
    """(explanation, is_a_stray)."""
    if is_deleted(hashtag_rec):
        return (
            "already Deleted=1. The record is not what is keeping this tag on "
            "screen; send me this output.",
            False,
        )
    if not has_ref:
        return (
            "no Reminder reference at all -- nothing for the phone's delete to "
            "walk. Stray.",
            True,
        )
    if reminder_rec is None:
        return (
            "points at a reminder that is not in the zone. Stray.",
            True,
        )
    if is_deleted(reminder_rec):
        return (
            "points at a soft-deleted reminder. The phone cannot show that "
            "reminder, so its 'delete tag' has nothing to strip the tag from -- "
            "which is exactly the delete-does-nothing symptom. Stray.",
            True,
        )
    return ("attached to a live reminder: the tag is in use.", False)


# ----------------------------------------------------------------- writing ---
def read_back(svc: Any, record_name: str) -> tuple[Optional[Any], str]:
    """(record, note). Gone is the outcome being hoped for."""
    from pyicloud.common.cloudkit import CKRecord

    try:
        resp = svc._raw.lookup(record_names=[record_name], zone_id=_zone_req())  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        return None, f"lookup raised: {type(exc).__name__}: {exc}"
    for item in resp.records:
        if isinstance(item, CKRecord) and item.recordName == record_name:
            return item, ""
        code = getattr(item, "serverErrorCode", None)
        if code:
            return None, f"lookup says {code} ({getattr(item, 'reason', '')})"
    return None, "not in the lookup response at all"


def describe_state(rec: Optional[Any]) -> str:
    if rec is None:
        return "gone"
    return f"present, Deleted={is_deleted(rec)}, changeTag={rec.recordChangeTag}"


def gone_or_deleted(rec: Optional[Any]) -> bool:
    return rec is None or is_deleted(rec)


def send(svc: Any, op: Any) -> str:
    """One raw operation, with Apple's verbatim answer -- 200s included.

    A 200 is not success here. modify() can carry per-record errors inside a
    200, which is what went unread for the stray list until force_delete_list
    started printing them.
    """
    from pyicloud.common.cloudkit import CKRecord

    try:
        resp = svc._raw.modify(operations=[op], zone_id=_zone_req())  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001 - Apple's complaint is the point
        payload = getattr(exc, "payload", None)
        return f"raised {type(exc).__name__}: {exc}" + (
            f" payload={payload}" if payload else ""
        )

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


def sanctioned_delete(svc: Any, rec: Any, reminder_name: str) -> str:
    """
    The library's own delete_hashtag: one atomic modify that strips the id from
    Reminder.HashtagIDs and sets Hashtag.Deleted=1 together.

    It is tried first even when the reminder is soft-deleted, because a
    soft-deleted reminder is still a record and still answers a lookup -- the
    phone's inability to show it is a UI matter, not a storage one. Leaving the
    id in HashtagIDs is what would let the tag come back.
    """
    try:
        reminder = svc.get(reminder_name)
    except Exception as exc:  # noqa: BLE001
        return f"could not fetch {reminder_name}: {type(exc).__name__}: {exc}"
    try:
        svc.delete_hashtag(reminder, svc._record_to_hashtag(rec))  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        return f"delete_hashtag raised: {type(exc).__name__}: {exc}"
    return "delete_hashtag accepted"


def ladder(record_name: str, change_tag: Optional[str]) -> list[tuple[str, Any]]:
    """
    Escalation for a hashtag with no usable reminder, gentlest first.

    forceUpdate and forceDelete drop the change-tag precondition. That is worth
    trying separately rather than assuming: if Apple were refusing on a stale
    tag it would say so, and the stray list showed it answering 200 and doing
    nothing instead.
    """
    from pyicloud.common.cloudkit import CKModifyOperation

    bare = {"recordName": record_name, "recordType": "Hashtag"}
    flag = {"Deleted": {"type": "INT64", "value": 1}}
    return [
        (
            "update Deleted=1",
            CKModifyOperation(
                operationType="update",
                record={**bare, "recordChangeTag": change_tag, "fields": flag},
            ),
        ),
        (
            "forceUpdate Deleted=1",
            CKModifyOperation(
                operationType="forceUpdate", record={**bare, "fields": flag}
            ),
        ),
        (
            "delete",
            CKModifyOperation(
                operationType="delete", record={**bare, "recordChangeTag": change_tag}
            ),
        ),
        ("forceDelete", CKModifyOperation(operationType="forceDelete", record=bare)),
    ]


def remove(svc: Any, rec: Any, reminder_name: str, reminder_rec: Optional[Any]) -> bool:
    """Take one Hashtag record away. Returns whether it actually went."""
    record_name = rec.recordName

    if reminder_name and reminder_rec is not None:
        print(f"    delete_hashtag: {sanctioned_delete(svc, rec, reminder_name)}")
        after, note = read_back(svc, record_name)
        print(f"    after: {describe_state(after)}{(' -- ' + note) if note else ''}")
        if gone_or_deleted(after):
            return True
        rec = after or rec

    for label, op in ladder(record_name, getattr(rec, "recordChangeTag", None)):
        print(f"    {label}")
        print(f"      apple: {send(svc, op)}")
        after, note = read_back(svc, record_name)
        print(f"      after: {describe_state(after)}{(' -- ' + note) if note else ''}")
        if gone_or_deleted(after):
            return True
        # Each rung needs the change tag as it stands now, not as it was.
        rec = after or rec
    return False


# -------------------------------------------------------------------- main ---
def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apple-id", default=os.environ.get("ICLOUD_APPLE_ID"))
    p.add_argument("--accept-terms", action="store_true")
    p.add_argument(
        "--name",
        action="append",
        default=[],
        metavar="TAG",
        help="Tag name to act on, case- and space-insensitive. Repeatable. "
        "Without it every tag is reported and none is touched.",
    )
    p.add_argument(
        "--include-live",
        action="store_true",
        help="Also remove copies attached to reminders that still exist. This "
        "is what 'delete this tag everywhere' means, and it edits real "
        "reminders, so it is off by default.",
    )
    p.add_argument(
        "--go",
        action="store_true",
        help="Actually send the writes. Requires --name.",
    )
    args = p.parse_args()

    if args.go and not args.name:
        print("--go needs --name: refusing to write against every tag in the account.")
        return 2

    svc = connect(apple_id=args.apple_id, accept_terms=args.accept_terms).reminders

    records = all_hashtag_records(svc)
    print(f"\n{len(records)} Hashtag records in the Reminders zone.")
    if not records:
        print("Nothing to report.")
        return 0

    wanted = {n.strip().lower().replace(" ", "") for n in args.name}

    def matches(name: str) -> bool:
        return not wanted or name.strip().lower().replace(" ", "") in wanted

    # Resolve every referenced reminder in one pass rather than one call each:
    # a tag used across a large list is otherwise a lookup per reminder.
    refs = [r for r in (reminder_ref(rec) for rec in records) if r]
    reminders = lookup_records(svc, sorted(set(refs)))

    by_name: dict[str, list[tuple[Any, str, Optional[Any], str, bool]]] = {}
    for rec in records:
        name, encoding = tag_name(rec)
        if not matches(name):
            continue
        ref = reminder_ref(rec)
        rem = reminders.get(ref) if ref else None
        note, stray = verdict(rec, rem, bool(ref))
        by_name.setdefault(name, []).append((rec, encoding, rem, note, stray))

    if not by_name:
        # Print what is there rather than just "nothing". A tag the phone shows
        # but this does not list means the feed did not reach the end, and that
        # is worth seeing rather than reading as "already clean".
        asked = ", ".join(repr(n) for n in args.name)
        present = ", ".join(repr(n) for n in sorted({tag_name(r)[0] for r in records}))
        print(f"Nothing named {asked}. The tags found were: {present}")
        return 0

    targets: list[tuple[Any, str, Optional[Any]]] = []
    for name in sorted(by_name):
        entries = by_name[name]
        flag = "  <- written or requested by the Phase 1 spike" if (
            name.strip().lower() in SPIKE_NAMES
        ) else ""
        print(f"\n#{name}  ({len(entries)} record"
              f"{'s' if len(entries) != 1 else ''}){flag}")
        for rec, encoding, rem, note, stray in entries:
            ref = reminder_ref(rec)
            attached = f"{ref} -- {describe_state(rem)}" if ref else "(none)"
            print(f"  recordName     {rec.recordName}")
            print(f"  Name stored as {encoding}")
            print(f"  Deleted flag   {is_deleted(rec)}")
            print(f"  reminder       {attached}")
            print(f"  -> {note}")
            print()
            if stray or (args.include_live and not is_deleted(rec)):
                targets.append((rec, ref, rem))

    if not args.go:
        if targets:
            print(f"{len(targets)} record(s) would be removed. Re-run with --go "
                  f"(and --name) to do it.")
        else:
            print("Nothing here is removable: every copy is on a live reminder "
                  "(--include-live covers those) or is already deleted.")
        return 0

    if not targets:
        print("Nothing to remove.")
        return 0

    stuck = []
    for rec, ref, rem in targets:
        print(f"\nRemoving {rec.recordName}")
        if not remove(svc, rec, ref, rem):
            stuck.append(rec.recordName)

    if stuck:
        print(
            "\nEvery operation was accepted and none of them changed anything, "
            "which is\nthe behaviour list create, rename and delete all show. "
            "Send me this output:\nthe next move is on our side, not Apple's."
        )
        return 1

    print(
        "\nDone. Reminders on the phone should drop the tag on its next refresh; "
        "the\nWindows app re-reads tags every sync, or straight away via "
        "Settings ->\nRe-download everything."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
