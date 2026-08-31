"""
Find -- and optionally remove -- Hashtag records the spike left in the account.

Two Phase 1 scripts created tags and cleaned up only half of what they made:

  * `probe_schema.dump_tag_schema` created `spikeprobe` through
    `create_hashtag`, then asked you to type `#phonetag` by hand on the iPhone
    so the two encodings could be compared.
  * `probe_fixes.probe_tag_fix` created `stringtag` with `Name` as STRING.

Both finish with `svc.delete(svc.get(rem.id))` -- they delete the *reminder* and
never touch the Hashtag records hanging off it. And deletion here is a soft
delete: the reminder record stays in the zone with `Deleted=1`, still listing
those hashtags in `HashtagIDs`. So the tag survives with exactly one reference
left, and that reference is on a reminder Apple's own app will not show you.

That is the whole of the reported symptom. Apple's Delete Tag and Rename Tag
work by rewriting the reminders that carry the tag:

  * Delete Tag strips it from every reminder the UI can see. There are none, so
    nothing is written, the Hashtag record survives, and the tag comes straight
    back on the next refresh -- "I click delete and nothing happens".
  * Rename Tag writes a *new* Hashtag record and repoints those same reminders.
    Again there are none to repoint, so the new tag appears and the old record
    is left exactly where it was -- "it creates a new tag and the old one stays".

Why this can work where find_stray_lists could not: Apple discards List writes,
which is what left `ZZSPIKE new list` unkillable. It does not discard Reminder
writes -- check 2 of the spike passed, and check 5's `delete_hashtag` really did
change `HashtagIDs`. The reference keeping this tag alive lives on a Reminder
record, which is the side of the account that accepts writes.

So the removal goes in two stages, gentlest first:

  1. `delete_hashtag(reminder, hashtag)` -- the library's atomic pair: strip the
     ID out of `Reminder.HashtagIDs` and set `Deleted=1` on the Hashtag. It
     writes no text field, so the ENCRYPTED_BYTES bug documented in the README
     does not apply to it.
  2. If the record is still live afterwards, the raw ladder from
     force_delete_list.py -- forceUpdate, delete, forceDelete -- with Apple's
     verbatim answer printed for each.

`update_hashtag` is deliberately never called. It writes `Name` as
ENCRYPTED_BYTES, which is the encoding that makes a tag invisible-but-present in
the first place; renaming a stray that way would hide the symptom and leave the
record behind.

Reports by default and changes nothing. Pass --delete to act on what it found.

Usage (Windows PowerShell, from the repo root):

    py -m venv .venv
    .\\.venv\\Scripts\\Activate.ps1
    pip install -r spike\\requirements.txt
    python spike\\find_stray_tags.py --apple-id you@example.com
    python spike\\find_stray_tags.py --apple-id you@example.com --delete

    # or go straight at one tag by name
    python spike\\find_stray_tags.py --apple-id you@example.com --name phonetag --delete
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import connect  # noqa: E402


def _zone():
    from pyicloud.services.reminders._constants import _REMINDERS_ZONE_REQ

    return _REMINDERS_ZONE_REQ


def _rec_name(value: str, prefix: str) -> str:
    """
    Normalize to the `Prefix/UUID` form.

    Reminder.hashtag_ids holds bare UUIDs while Hashtag.id holds the full record
    name. Comparing the two without this is how a tag that *is* referenced gets
    reported as an orphan.
    """
    text = str(value)
    return text if "/" in text else f"{prefix}/{text}"


# ------------------------------------------------------------------ reading
def raw_records(svc: Any, ids: list[str]) -> dict[str, Any]:
    """
    Raw records by name. The typed models drop Deleted and the Name encoding,
    and both are what distinguish a live stray from one already dealt with.

    Takes the *reminders* service: `_raw` hangs off that, not off
    PyiCloudService.
    """
    if not ids:
        return {}
    from pyicloud.common.cloudkit import CKRecord

    out: dict[str, Any] = {}
    # Lookup takes a list, but a long one risks a request Apple truncates, so
    # this pages rather than trusting a single call with a few hundred names.
    for i in range(0, len(ids), 50):
        resp = svc._raw.lookup(  # noqa: SLF001
            record_names=ids[i : i + 50], zone_id=_zone()
        )
        for rec in resp.records:
            if isinstance(rec, CKRecord):
                out[rec.recordName] = rec
    return out


def describe(rec: Any) -> tuple[bool, Optional[str], str]:
    """(deleted, changeTag, how Name is stored) for one raw Hashtag record."""
    if rec is None:
        return False, None, "unknown"
    deleted = bool(rec.fields.get_value("Deleted"))
    tag = getattr(rec, "recordChangeTag", None)
    # get_field, not get: get returns the wrapper, whose .type is None and whose
    # .value is still base64. The declared type is the interesting part -- an
    # iPhone writes STRING, pyicloud writes ENCRYPTED_BYTES.
    name = rec.fields.get_field("Name")
    return deleted, tag, (name.type if name is not None else None) or "unknown"


def scan(svc: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, set[str]]]:
    """
    Walk every list and return (hashtags, reminders, referenced_by).

    The compound `reminderList` query carries Hashtag records alongside the
    reminders and applies no client-side Deleted filter, so soft-deleted
    reminders -- the ones holding the strays -- come back here too. That is the
    whole reason this can be done from the API rather than from the phone.
    """
    hashtags: dict[str, Any] = {}
    reminders: dict[str, Any] = {}
    referenced_by: dict[str, set[str]] = {}

    lists = list(svc.lists())
    for i, lst in enumerate(lists, 1):
        print(f"  [{i}/{len(lists)}] {str(lst.title)!r} ... ", end="", flush=True)
        try:
            batch = svc.list_reminders(
                list_id=lst.id, include_completed=True, results_limit=200
            )
        except Exception as exc:  # noqa: BLE001 - one bad list must not end the scan
            print(f"skipped ({type(exc).__name__}: {exc})")
            continue

        for rem in batch.reminders:
            reminders[rem.id] = rem
            for raw_id in rem.hashtag_ids or []:
                referenced_by.setdefault(_rec_name(raw_id, "Hashtag"), set()).add(rem.id)

        found = getattr(batch, "hashtags", None) or {}
        values = found.values() if isinstance(found, dict) else found
        for h in values:
            hashtags[_rec_name(h.id, "Hashtag")] = h
        print(f"{len(batch.reminders)} reminders")

    return hashtags, reminders, referenced_by


# ------------------------------------------------------------------ writing
def attempt(svc: Any, op: Any) -> str:
    """Send one raw operation and report Apple's verbatim answer, 200s included."""
    from pyicloud.common.cloudkit import CKRecord

    try:
        resp = svc._raw.modify(operations=[op], zone_id=_zone())  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001 - Apple's complaint is the point
        payload = getattr(exc, "payload", None)
        return f"raised {type(exc).__name__}: {exc}" + (
            f" payload={payload}" if payload else ""
        )

    # A 200 is not success: per-record errors ride inside it. force_delete_list
    # learned this the hard way and the same trap is here.
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


def raw_ladder(record_name: str, change_tag: Optional[str]) -> list[tuple[str, Any]]:
    """The fallback ops, gentlest first. Only Deleted -- no text field goes near this."""
    from pyicloud.common.cloudkit import CKModifyOperation

    bare = {"recordName": record_name, "recordType": "Hashtag"}
    flag = {
        "Deleted": {"type": "INT64", "value": 1},
        "LastModifiedDate": {"type": "TIMESTAMP", "value": int(time.time() * 1000)},
    }
    return [
        (
            "forceUpdate Deleted=1",
            CKModifyOperation(operationType="forceUpdate", record={**bare, "fields": flag}),
        ),
        (
            "delete",
            CKModifyOperation(
                operationType="delete", record={**bare, "recordChangeTag": change_tag}
            ),
        ),
        ("forceDelete", CKModifyOperation(operationType="forceDelete", record=bare)),
    ]


def unlink_via_library(svc: Any, hashtag_name: str, reminder_ids: set[str]) -> None:
    """
    Stage 1: strip the tag off each reminder still listing it.

    Re-fetches the reminder and its tags immediately before writing rather than
    reusing the objects from the scan -- delete_hashtag sends the change tag as
    a precondition and the scan's copy is minutes stale by now.
    """
    for rid in sorted(reminder_ids):
        try:
            rem = svc.get(rid)
        except Exception as exc:  # noqa: BLE001
            print(f"    {rid}: could not re-read ({type(exc).__name__}: {exc})")
            continue

        target = next(
            (
                t
                for t in svc.tags_for(rem)
                if _rec_name(t.id, "Hashtag") == hashtag_name
            ),
            None,
        )
        if target is None:
            print(f"    {rid}: no longer lists this tag")
            continue

        try:
            svc.delete_hashtag(rem, target)
            print(f"    {rid}: delete_hashtag accepted")
        except Exception as exc:  # noqa: BLE001
            print(f"    {rid}: delete_hashtag failed ({type(exc).__name__}: {exc})")


# --------------------------------------------------------------------- main
def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apple-id", default=os.environ.get("ICLOUD_APPLE_ID"))
    p.add_argument("--accept-terms", action="store_true")
    p.add_argument("--name", help="Only tags with this name (case-insensitive)")
    p.add_argument("--record-name", help="Target one Hashtag record instead of scanning")
    p.add_argument(
        "--delete",
        action="store_true",
        help="Actually remove what was found. Without this, nothing is written.",
    )
    args = p.parse_args()

    svc = connect(apple_id=args.apple_id, accept_terms=args.accept_terms).reminders

    print("\nScanning every list for Hashtag records:")
    hashtags, reminders, referenced_by = scan(svc)
    print(f"\n{len(hashtags)} Hashtag records across {len(reminders)} reminders.")

    # A reminder can reference a Hashtag the compound query never returned --
    # that is a stray of a different shape and worth surfacing rather than
    # silently dropping.
    dangling = sorted(set(referenced_by) - set(hashtags))
    if dangling:
        print(
            f"{len(dangling)} ID(s) referenced by a reminder with no Hashtag record "
            "in the response; carried through so the lookup below can report them."
        )
        for name in dangling:
            hashtags.setdefault(name, None)

    candidates = list(hashtags)
    if args.record_name:
        want = _rec_name(args.record_name, "Hashtag")
        candidates = [c for c in candidates if c == want]
        if not candidates:
            candidates = [want]  # let the lookup below say whether it exists
    if args.name:
        needle = args.name.lstrip("#").lower()
        candidates = [
            c
            for c in candidates
            if hashtags.get(c) is not None
            and str(hashtags[c].name).lower() == needle
        ]
        if not candidates:
            print(f"\nNo tag named {args.name!r}. Nothing to do.")
            return 0

    raw = raw_records(svc, candidates)

    strays: list[tuple[str, str, set[str], Optional[str]]] = []
    for name in sorted(candidates):
        h = hashtags.get(name)
        refs = referenced_by.get(name, set())
        live_refs = {r for r in refs if not getattr(reminders.get(r), "deleted", False)}
        rec = raw.get(name)
        deleted, change_tag, encoding = describe(rec)

        if live_refs and not args.record_name and not args.name:
            continue  # in use by a reminder you can actually see; leave it alone

        label = str(h.name) if h is not None else "<no Hashtag record>"
        print(f"\n  name           {label!r}")
        print(f"  recordName     {name}")
        print(f"  Name stored as {encoding}")
        print(f"  Deleted flag   {deleted}")
        print(f"  changeTag      {change_tag}")
        if refs:
            print("  referenced by:")
            for rid in sorted(refs):
                rem = reminders.get(rid)
                title = str(getattr(rem, "title", "")) if rem is not None else "?"
                flag = " [deleted]" if getattr(rem, "deleted", False) else ""
                print(f"    {rid}  {title!r}{flag}")
        else:
            print("  referenced by: nothing")

        if rec is None:
            print("  -> No record in the zone. Nothing here to remove.")
            continue
        if deleted:
            print(
                "  -> Already soft-deleted. Removing it again achieves nothing; if\n"
                "     Apple's app still lists this tag, send me this output."
            )
            continue
        if live_refs:
            print(
                "  -> Live and carried by a reminder you can see. This is a real tag,\n"
                "     not a stray -- delete it in Reminders, not here."
            )
            continue

        print(
            "  -> Live record with no reminder you can see. This is the stray:\n"
            "     Apple's Delete Tag has nothing to rewrite, which is why it does\n"
            "     nothing and why Rename leaves this one behind."
        )
        strays.append((name, label, refs, change_tag))

    if not strays:
        print("\nNo live strays found.")
        return 0

    if not args.delete:
        print(
            f"\n{len(strays)} stray tag(s). Reported only -- re-run with --delete to "
            "remove them."
        )
        return 0

    for name, label, refs, change_tag in strays:
        print(f"\nRemoving {label!r} ({name})")
        print("  1. unlink from the reminders still holding it")
        unlink_via_library(svc, name, refs)

        rec = raw_records(svc, [name]).get(name)
        deleted, change_tag, _ = describe(rec)
        if rec is None or deleted:
            print("  -> gone after stage 1.")
            continue

        print("  2. record still live; walking the raw ladder")
        for op_label, op in raw_ladder(name, change_tag):
            print(f"    {op_label}")
            print(f"      apple: {attempt(svc, op)}")
            rec = raw_records(svc, [name]).get(name)
            deleted, change_tag, _ = describe(rec)
            if rec is None or deleted:
                print(f"    -> {op_label} worked.")
                break

    # Read it back rather than trusting the messages above: modify() answers 200
    # with per-record errors embedded, so "accepted" is not "changed".
    print("\nVerifying:")
    after = raw_records(svc, [name for name, _, _, _ in strays])
    stuck = []
    for name, label, _, _ in strays:
        rec = after.get(name)
        if rec is None:
            print(f"  {label!r}: gone from the zone entirely.")
            continue
        deleted, _, _ = describe(rec)
        print(f"  {label!r}: Deleted flag is now {deleted}")
        if not deleted:
            stuck.append(label)

    if stuck:
        print(
            "\nApple accepted the writes and the flag did not change, which would put\n"
            "hashtags with List records on the side of the API it takes and ignores.\n"
            "Send me this output before trying anything else."
        )
        return 1

    print(
        "\nDone. Apple's own apps drop a tag once nothing references it, so the chip\n"
        "should disappear from Reminders within a sync. The Windows app already\n"
        "hides tags whose only reminders are deleted, so it needs nothing."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
