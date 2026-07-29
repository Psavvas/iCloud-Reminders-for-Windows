"""
Phase 1, item 6: can we CREATE a reminder list?

There is no public API for this. `RemindersService` exposes `lists()` (read
only); grepping the whole pyicloud package for a list-creation method returns
nothing. So this module goes underneath the typed service and drives the raw
CloudKit `modify` endpoint directly, mirroring the record shape that
`_writes.create()` uses for reminders.

What we know from the library source:
  - Lists are CloudKit records of type "List" in zone "Reminders"
    (REGULAR_CUSTOM_ZONE), container com.apple.reminders, private scope.
  - `_mappers.record_to_list()` reads these fields off a List record:
        Name (str), Color (str), Count (int), BadgeEmblem,
        SortingStyle, IsGroup (int)
  - Unlike a Reminder's title, a List's Name is a plain STRING -- no CRDT
    protobuf document encoding needed. That makes a hand-built create
    plausible.
  - Reminder records are created with recordName "Reminder/<UUID-UPPER>", so
    "List/<UUID-UPPER>" is the natural guess for lists.

Everything below is a hypothesis test. Failure is a legitimate, reportable
outcome -- we capture Apple's exact serverErrorCode rather than guessing.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from pyicloud.common.cloudkit import CKModifyOperation, CKRecord
from pyicloud.services.reminders._constants import _REMINDERS_ZONE_REQ
from pyicloud.services.reminders._protocol import (
    _encode_cloudkit_text_field,
    _generate_resolution_token_map,
)


def public_api_has_list_creation(service: Any) -> tuple[bool, list[str]]:
    """Introspect the service for anything that could create a list."""
    candidates = [
        name
        for name in dir(service)
        if not name.startswith("__")
        and any(v in name.lower() for v in ("create", "add", "new"))
    ]
    has = any("list" in name.lower() for name in candidates)
    return has, sorted(candidates)


def _write_record(record_name: str, fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "recordName": record_name,
        "recordType": "List",
        "fields": fields,
    }


def _attempt(
    raw: Any,
    record_name: str,
    fields: dict[str, Any],
    label: str,
) -> tuple[bool, str, Optional[CKRecord]]:
    """Try one create shape. Returns (ok, message, created_record)."""
    op = CKModifyOperation(
        operationType="create",
        record=_write_record(record_name, fields),
    )
    try:
        resp = raw.modify(operations=[op], zone_id=_REMINDERS_ZONE_REQ)
    except Exception as exc:  # noqa: BLE001 - we want Apple's verbatim complaint
        payload = getattr(exc, "payload", None)
        extra = f" payload={payload}" if payload else ""
        return False, f"{label}: {type(exc).__name__}: {exc}{extra}", None

    # modify() can return 200 with per-record errors embedded.
    errors = []
    created: Optional[CKRecord] = None
    for item in resp.records:
        if isinstance(item, CKRecord):
            if item.recordName == record_name:
                created = item
        else:
            code = getattr(item, "serverErrorCode", None)
            reason = getattr(item, "reason", None)
            if code or reason:
                errors.append(f"{getattr(item, 'recordName', '?')}: {code} ({reason})")

    if errors:
        return False, f"{label}: rejected -> " + "; ".join(errors), None
    if created is None:
        return False, f"{label}: no error but no record echoed back", None
    return True, f"{label}: accepted", created


def try_create_list(
    service: Any,
    title: str,
    color: str = "#FF9500",
) -> dict[str, Any]:
    """
    Attempt list creation with escalating field sets.

    Returns a dict describing what happened, including every attempt's error so
    the report can quote Apple rather than paraphrase it.
    """
    raw = service._raw  # noqa: SLF001 - deliberate: no public path exists
    attempts: list[str] = []
    now_ms = int(time.time() * 1000)

    # Reminders text fields are NOT plain STRING on the wire. pyicloud encodes
    # them as {"type": "ENCRYPTED_BYTES", "value": base64(utf8(text))} -- see
    # `_encode_cloudkit_text_field`, used for Hashtag.Name in _writes.py.
    # Sending STRING is what produced Apple's
    #   BAD_REQUEST (byte values must be base64 encoded)
    shapes: list[tuple[str, dict[str, Any]]] = [
        (
            "minimal (Name as ENCRYPTED_BYTES)",
            {"Name": _encode_cloudkit_text_field(title)},
        ),
        (
            "Name+Color (both ENCRYPTED_BYTES)",
            {
                "Name": _encode_cloudkit_text_field(title),
                "Color": _encode_cloudkit_text_field(color),
            },
        ),
        (
            "reminder-parity (encoded text + timestamps, tokens, flags)",
            {
                "Name": _encode_cloudkit_text_field(title),
                "Color": _encode_cloudkit_text_field(color),
                "Count": {"type": "INT64", "value": 0},
                "IsGroup": {"type": "INT64", "value": 0},
                "Deleted": {"type": "INT64", "value": 0},
                "CreationDate": {"type": "TIMESTAMP", "value": now_ms},
                "LastModifiedDate": {"type": "TIMESTAMP", "value": now_ms},
                "ResolutionTokenMap": {
                    "type": "STRING",
                    "value": _generate_resolution_token_map(
                        ["name", "color", "creationDate", "lastModifiedDate"]
                    ),
                },
            },
        ),
    ]

    for label, fields in shapes:
        record_name = f"List/{str(uuid.uuid4()).upper()}"
        ok, msg, rec = _attempt(raw, record_name, fields, label)
        attempts.append(msg)
        print(f"    - {msg}")
        if ok:
            return {
                "created": True,
                "record_name": record_name,
                "record_change_tag": getattr(rec, "recordChangeTag", None),
                "winning_shape": label,
                "attempts": attempts,
            }

    return {"created": False, "attempts": attempts}


def delete_list(service: Any, record_name: str, record_change_tag: Optional[str]) -> str:
    """
    Best-effort cleanup, mirroring how reminders are deleted (soft Deleted=1),
    then falling back to a hard forceDelete.
    """
    raw = service._raw  # noqa: SLF001
    now_ms = int(time.time() * 1000)

    soft = CKModifyOperation(
        operationType="update",
        record={
            "recordName": record_name,
            "recordType": "List",
            "recordChangeTag": record_change_tag,
            "fields": {
                "Deleted": {"type": "INT64", "value": 1},
                "LastModifiedDate": {"type": "TIMESTAMP", "value": now_ms},
                "ResolutionTokenMap": {
                    "type": "STRING",
                    "value": _generate_resolution_token_map(
                        ["deleted", "lastModifiedDate"]
                    ),
                },
            },
        },
    )
    try:
        raw.modify(operations=[soft], zone_id=_REMINDERS_ZONE_REQ)
        return "soft-deleted (Deleted=1)"
    except Exception as exc:  # noqa: BLE001
        soft_err = f"{type(exc).__name__}: {exc}"

    hard = CKModifyOperation(
        operationType="forceDelete",
        record={"recordName": record_name, "recordType": "List"},
    )
    try:
        raw.modify(operations=[hard], zone_id=_REMINDERS_ZONE_REQ)
        return "force-deleted"
    except Exception as exc:  # noqa: BLE001
        return f"CLEANUP FAILED (soft: {soft_err}) (hard: {type(exc).__name__}: {exc})"
