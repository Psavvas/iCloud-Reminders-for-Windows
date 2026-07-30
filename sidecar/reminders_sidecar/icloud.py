"""
iCloud access layer.

Wraps pyicloud and normalizes everything the rest of the app sees: tz-aware UTC
datetimes, plain dicts, and a small set of typed errors so the UI can tell
"you need to log in again" apart from "the network is down".

Phase 1 findings encoded here:
  - List.color is a JSON blob, not a hex string. Parse it, expose daHexString.
  - Tags are read-only. pyicloud writes Hashtag.Name as ENCRYPTED_BYTES while
    Apple stores STRING; the record round-trips over the API but never renders
    on the device, and correcting the encoding did not fix it. Reading works
    fine, so tags are surfaced and filterable but not editable.
  - Reminder deletion is a soft delete (Deleted = 1).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

LOGGER = logging.getLogger(__name__)


class SidecarError(Exception):
    """Base error carrying a stable machine-readable code for the UI."""

    code = "ERROR"

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "detail": self.detail}


class AuthRequired(SidecarError):
    """No usable session. The UI must show a login prompt."""

    code = "AUTH_REQUIRED"


class TwoFactorRequired(SidecarError):
    code = "2FA_REQUIRED"


class TermsRequired(SidecarError):
    """Apple is gating login on updated T&Cs. Never fail this silently."""

    code = "TERMS_REQUIRED"


class NetworkError(SidecarError):
    code = "NETWORK"


class ConflictError(SidecarError):
    """Remote record moved under us; caller must not clobber it."""

    code = "CONFLICT"


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalize to tz-aware UTC. Naive values are treated as UTC by Apple."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_color(raw: Any) -> Optional[str]:
    """
    Extract a CSS hex colour from the JSON blob Apple stores.

    Observed shape:
      {"daHexString": "#5AC8FA", "ckSymbolicColorName": "lightBlue",
       "red": 0.35, "green": 0.78, "blue": 0.98, "alpha": 1, ...}

    Group rows carry no colour at all, so None is a normal answer.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("#"):
            return s
        try:
            raw = json.loads(s)
        except (ValueError, TypeError):
            return None
    if isinstance(raw, dict):
        hexstr = raw.get("daHexString")
        if isinstance(hexstr, str) and hexstr.startswith("#"):
            return hexstr
        try:
            r, g, b = (raw["red"], raw["green"], raw["blue"])
            return "#{:02X}{:02X}{:02X}".format(
                round(float(r) * 255), round(float(g) * 255), round(float(b) * 255)
            )
        except (KeyError, TypeError, ValueError):
            return None
    return None


class ICloudClient:
    """Thin, normalizing wrapper over pyicloud's RemindersService."""

    def __init__(self, apple_id: str, cookie_dir: Optional[str] = None):
        self.apple_id = apple_id
        self.cookie_dir = cookie_dir
        self._api = None
        self._pending_2fa = False

    # ------------------------------------------------------------------ auth
    @property
    def connected(self) -> bool:
        return self._api is not None and not self._pending_2fa

    def _service(self):
        if not self.connected:
            raise AuthRequired("Not signed in to iCloud")
        return self._api.reminders

    def connect(self, password: Optional[str] = None, accept_terms: bool = False):
        """
        Establish a session. With no password, relies on the stored session and
        keyring entry, which is the normal path after the first login.
        """
        from pyicloud import PyiCloudService
        from pyicloud.exceptions import (
            PyiCloud2FARequiredException,
            PyiCloudAcceptTermsException,
            PyiCloudFailedLoginException,
        )

        try:
            kwargs: dict[str, Any] = {"accept_terms": accept_terms}
            if self.cookie_dir:
                kwargs["cookie_directory"] = self.cookie_dir
            self._api = PyiCloudService(self.apple_id, password=password, **kwargs)
        except PyiCloudAcceptTermsException as exc:
            raise TermsRequired(
                "Apple requires you to accept updated iCloud terms before "
                "this app can sync.",
                str(exc),
            ) from exc
        except PyiCloud2FARequiredException as exc:
            self._pending_2fa = True
            raise TwoFactorRequired("Two-factor authentication required", str(exc)) from exc
        except PyiCloudFailedLoginException as exc:
            raise AuthRequired("iCloud rejected the sign-in", str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise NetworkError("Could not reach iCloud", str(exc)) from exc

        if self._api.requires_2fa:
            self._pending_2fa = True
            raise TwoFactorRequired("Two-factor authentication required")
        self._pending_2fa = False
        return self.status()

    def request_2fa(self) -> bool:
        if self._api is None:
            raise AuthRequired("Not signed in")
        return bool(self._api.request_2fa_code())

    def submit_2fa(self, code: str) -> dict:
        if self._api is None:
            raise AuthRequired("Not signed in")
        if not self._api.validate_2fa_code(code):
            raise TwoFactorRequired("That code was rejected")
        if not self._api.is_trusted_session:
            try:
                self._api.trust_session()
            except Exception as exc:  # noqa: BLE001 - non-fatal
                LOGGER.warning("trust_session failed: %s", exc)
        self._pending_2fa = False
        return self.status()

    def status(self) -> dict:
        if self._api is None:
            return {"authenticated": False, "apple_id": self.apple_id}
        return {
            "authenticated": self.connected,
            "apple_id": self.apple_id,
            "needs_2fa": bool(self._pending_2fa),
            "trusted_session": bool(getattr(self._api, "is_trusted_session", False)),
        }

    # ----------------------------------------------------------------- reads
    @staticmethod
    def _clean_title(title: Any) -> str:
        """
        Decode a list name that came back as bytes.

        Apple stores List.Name as a plain STRING. A record written with the
        ENCRYPTED_BYTES encoding pyicloud uses for hashtags stores raw bytes
        instead, and the mapper's str() then yields the literal "b'name'".
        Apple's own clients ignore such a record entirely.
        """
        if isinstance(title, (bytes, bytearray)):
            try:
                return title.decode("utf-8")
            except UnicodeDecodeError:
                return "Untitled"
        text = str(title or "")
        # The mapper has usually already stringified it by this point.
        m = re.fullmatch(r"b(['\"])(.*)\1", text, re.S)
        return m.group(2) if m else text

    def _deleted_list_ids(self, ids: list[str]) -> set[str]:
        """
        List IDs whose record is flagged deleted.

        `lists()` yields every List record in the zone, including soft-deleted
        ones -- deletion here is a Deleted=1 flag, not a removal -- and the
        typed model does not carry that flag. Apple's clients hide these, so
        the raw records are consulted to do the same.
        """
        if not ids:
            return set()
        try:
            from pyicloud.common.cloudkit import CKRecord
            from pyicloud.services.reminders._constants import _REMINDERS_ZONE_REQ

            resp = self._service()._raw.lookup(  # noqa: SLF001
                record_names=ids, zone_id=_REMINDERS_ZONE_REQ
            )
            out = set()
            for rec in resp.records:
                if isinstance(rec, CKRecord) and rec.fields.get_value("Deleted"):
                    out.add(rec.recordName)
            return out
        except Exception as exc:  # noqa: BLE001 - never fail a sync over this
            LOGGER.debug("could not read List deleted flags: %s", exc)
            return set()

    def lists(self) -> list[dict]:
        svc = self._service()
        raw = list(svc.lists())
        hidden = self._deleted_list_ids([l.id for l in raw])
        out = []
        for l in raw:
            if l.id in hidden:
                continue
            out.append(
                {
                    "id": l.id,
                    "title": self._clean_title(l.title),
                    "color_hex": parse_color(l.color),
                    "count": l.count,
                    "is_group": bool(l.is_group),
                }
            )
        return out

    def reminders_for(self, list_id: str) -> tuple[list[dict], dict[str, list[dict]]]:
        """
        Snapshot one list.

        Returns (reminders, tags_by_reminder_id). The compound query already
        carries hashtags, so this avoids a lookup per reminder -- which matters
        on a list of 1219.
        """
        svc = self._service()
        try:
            batch = svc.list_reminders(
                list_id=list_id, include_completed=True, results_limit=200
            )
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc) from exc

        reminders = [self._reminder_to_dict(r) for r in batch.reminders]

        tags_by: dict[str, list[dict]] = {}
        hashtags = getattr(batch, "hashtags", None) or {}
        values = hashtags.values() if isinstance(hashtags, dict) else hashtags
        for h in values:
            rid = getattr(h, "reminder_id", None)
            name = getattr(h, "name", None)
            hid = getattr(h, "id", None)
            if not rid or not name:
                continue
            tags_by.setdefault(rid, []).append({"id": hid, "name": str(name)})
        return reminders, tags_by

    def tags_for(self, reminder) -> list[dict]:
        svc = self._service()
        return [
            {"id": t.id, "name": str(t.name)}
            for t in svc.tags_for(reminder)
            if getattr(t, "name", None)
        ]

    def sync_cursor(self) -> Optional[str]:
        try:
            return self._service().sync_cursor()
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("sync_cursor failed: %s", exc)
            return None

    def changes_since(self, cursor: Optional[str]) -> list[dict]:
        """
        Delta since `cursor`.

        Only reminders: iter_changes filters recordType == 'Reminder', so list
        changes are invisible here and the caller must refresh lists separately.
        """
        svc = self._service()
        out = []
        try:
            for evt in svc.iter_changes(since=cursor):
                if evt.type == "deleted" or evt.reminder is None:
                    out.append({"id": evt.reminder_id, "deleted": True})
                else:
                    d = self._reminder_to_dict(evt.reminder)
                    d["deleted"] = bool(d.get("deleted"))
                    out.append(d)
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc) from exc
        return out

    # ---------------------------------------------------------------- writes
    def create(self, payload: dict) -> dict:
        svc = self._service()
        due = payload.get("due_date")
        if isinstance(due, str):
            due = datetime.fromisoformat(due)
        due = _as_utc(due)
        try:
            rem = svc.create(
                list_id=payload["list_id"],
                title=payload.get("title") or "",
                desc=payload.get("description") or "",
                due_date=due,
                priority=int(payload.get("priority") or 0),
                flagged=bool(payload.get("flagged")),
            )
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc) from exc
        return self._reminder_to_dict(rem)

    def update(self, reminder_id: str, payload: dict, base_tag: Optional[str]) -> dict:
        """
        Push a local edit.

        Refuses to write when the remote record has moved since we read it --
        the phone writes too, and Phase 1 showed it replaces fields wholesale
        rather than merging.
        """
        svc = self._service()
        try:
            rem = svc.get(reminder_id)
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc) from exc

        if base_tag and rem.record_change_tag and rem.record_change_tag != base_tag:
            raise ConflictError(
                "This reminder changed on another device while you were editing it.",
                json.dumps(self._reminder_to_dict(rem), default=str),
            )

        if "title" in payload:
            rem.title = payload["title"] or ""
        if "description" in payload:
            rem.desc = payload["description"] or ""
        if "priority" in payload:
            rem.priority = int(payload["priority"] or 0)
        if "completed" in payload:
            rem.completed = bool(payload["completed"])
        if "flagged" in payload:
            rem.flagged = bool(payload["flagged"])
        if "deleted" in payload:
            # Deletion is a soft flag, so restoring is just clearing it.
            rem.deleted = bool(payload["deleted"])
        if "due_date" in payload:
            due = payload["due_date"]
            if isinstance(due, str):
                due = datetime.fromisoformat(due)
            rem.due_date = _as_utc(due)

        try:
            svc.update(rem)
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc) from exc
        return self._reminder_to_dict(rem)

    def delete(self, reminder_id: str) -> None:
        svc = self._service()
        try:
            svc.delete(svc.get(reminder_id))
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc) from exc

    # ------------------------------------------------------------- internals
    @staticmethod
    def _reminder_to_dict(r) -> dict:
        return {
            "id": r.id,
            "list_id": r.list_id,
            "title": r.title or "",
            "description": r.desc or "",
            "due_date": (_as_utc(r.due_date).isoformat() if r.due_date else None),
            "priority": int(r.priority or 0),
            "completed": bool(r.completed),
            "completed_date": (
                _as_utc(r.completed_date).isoformat() if r.completed_date else None
            ),
            "flagged": bool(r.flagged),
            "all_day": bool(r.all_day),
            "deleted": bool(r.deleted),
            "created": (_as_utc(r.created).isoformat() if r.created else None),
            "modified": (_as_utc(r.modified).isoformat() if r.modified else None),
            "change_tag": r.record_change_tag,
            "hashtag_ids": list(r.hashtag_ids or []),
        }

    @staticmethod
    def _classify(exc: Exception) -> SidecarError:
        """Map pyicloud/CloudKit failures onto errors the UI can act on."""
        name = type(exc).__name__
        text = str(exc)
        if "AcceptTerms" in name or "termsUpdateNeeded" in text:
            return TermsRequired(
                "Apple requires you to accept updated iCloud terms.", text
            )
        if "2FA" in name or "RemindersAuthError" in name or "401" in text or "403" in text:
            return AuthRequired("Your iCloud session expired. Please sign in again.", text)
        if isinstance(exc, SidecarError):
            return exc
        return NetworkError("iCloud request failed", text)
