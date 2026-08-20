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
  - A reminder's DueDate is a wall-clock time encoded as if UTC, not an instant.
    See timeutil for why, and for the conversion both ways.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from .timeutil import floating_to_instant, instant_to_floating

LOGGER = logging.getLogger(__name__)

# What an actually-unauthorized answer from Apple looks like in a message.
#
# The old test was `"401" in text or "403" in text`, which also matches a record
# name, a reminder count, and a request id -- every one of those turned a
# perfectly good session into "sign in again". Anchor on the status code being
# used *as* a status.
_UNAUTHORIZED = re.compile(
    r"\b(?:401|403)\b\s*(?:client\s+)?(?:error|unauthori[sz]ed|forbidden)"
    r"|(?:status|status_code|http)\W{0,3}(?:401|403)\b",
    re.I,
)


class SidecarError(Exception):
    """Base error carrying a stable machine-readable code for the UI."""

    code = "ERROR"

    def __init__(self, message: str, detail: str = "", data: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.detail = detail
        # Structured extras the UI can render. 2FA uses it to say *how* the code
        # was sent, which is the difference between "check your iPhone" and
        # "check your texts" -- and the user cannot type a code they are staring
        # past.
        self.data = dict(data or {})

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
            "data": self.data,
        }


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
    """
    Normalize a genuine instant to tz-aware UTC.

    Right for CreationDate, LastModifiedDate and CompletionDate, which Apple
    sets from a server clock. Wrong for DueDate, which is a wall-clock time --
    that goes through timeutil instead.
    """
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
        self.restoring = False
        # How the outstanding verification code was delivered, if any. Empty
        # once there is no challenge in flight.
        self._two_factor: dict = {}
        # Why the last restore gave up. A network failure and a rejected
        # credential both used to arrive here as a bare False, and the caller
        # then treated both as "you have been signed out".
        self.last_restore_error: Optional[SidecarError] = None
        # Serializes everything that swaps `_api` out. The background restore
        # and a login typed into the gate are two different threads reaching for
        # the same field: whichever finished last won, and if that was the
        # restore it threw away the 2FA challenge the login had just armed --
        # after which every code the user typed was checked against nothing.
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ auth
    @property
    def connected(self) -> bool:
        return self._api is not None and not self._pending_2fa

    @property
    def awaiting_2fa(self) -> bool:
        """
        A verification code is outstanding and someone is typing it right now.

        Worth asking before rebuilding the session: the challenge lives on the
        PyiCloudService object, so replacing it makes Apple mint a fresh code and
        retire the one in the user's hand. A background sync doing that on its
        timer is enough to make a correctly typed code come back "invalid".
        """
        return self._api is not None and self._pending_2fa

    def _service(self):
        if not self.connected:
            raise AuthRequired("Not signed in to iCloud")
        return self._api.reminders

    # -- credential storage --------------------------------------------------
    #
    # pyicloud reads the password back out of the keyring itself when
    # PyiCloudService is constructed without one, so remembering it is the whole
    # mechanism behind a session that survives a restart. Windows Credential
    # Manager is the backend; nothing is written to disk in the clear.
    def remember_password(self, password: str) -> bool:
        if not password:
            return False
        try:
            from pyicloud.utils import store_password_in_keyring

            store_password_in_keyring(self.apple_id, password)
            return True
        except Exception as exc:  # noqa: BLE001 - never fail a login over this
            LOGGER.warning("could not save the password to the keyring: %s", exc)
            return False

    def forget_password(self) -> None:
        try:
            from pyicloud.utils import (
                delete_password_in_keyring,
                password_exists_in_keyring,
            )

            if password_exists_in_keyring(self.apple_id):
                delete_password_in_keyring(self.apple_id)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("could not clear the stored password: %s", exc)

    @property
    def has_saved_password(self) -> bool:
        if not self.apple_id:
            return False
        try:
            from pyicloud.utils import password_exists_in_keyring

            return bool(password_exists_in_keyring(self.apple_id))
        except Exception:  # noqa: BLE001
            return False

    def invalidate(self) -> None:
        """
        Drop the in-memory session so a restore actually rebuilds it.

        A 401 from CloudKit does not disturb the PyiCloudService object -- it
        has no idea its token stopped working -- so `connected` stays True. That
        made restore() below return True without reconnecting, which quietly
        turned the one-retry recovery into a no-op and sent every expiry
        straight to the sign-in screen. It also left status() claiming
        authenticated while nothing could sync, which is what "it looks like
        you're still logged in" was.

        Only the live session is discarded. The keyring password, the cookies
        and the trust token all survive, which is what lets the reconnect
        happen without a 2FA prompt.
        """
        with self._lock:
            self._api = None
            self._pending_2fa = False
            self._two_factor = {}

    def restore(self) -> bool:
        """
        Re-establish a session without asking the user anything.

        Two things make this work: the cookie directory holds a session token
        pyicloud validates first, and behind it the keyring password plus the
        trust token let it redo the SRP handshake without a fresh 2FA code.
        Returns False rather than raising -- a failed restore just means the
        sign-in screen, not an error worth showing.
        """
        with self._lock:
            if self.connected or not self.apple_id:
                return self.connected
            if self.awaiting_2fa:
                # Not ours to rebuild: the code the user is holding belongs to
                # the challenge on the session we would be throwing away.
                self.last_restore_error = TwoFactorRequired(
                    "Waiting for a verification code", data=dict(self._two_factor)
                )
                return False
            self.restoring = True
            self.last_restore_error = None
            try:
                self.connect()
                return True
            except SidecarError as exc:
                self.last_restore_error = exc
                LOGGER.info("session restore failed (%s): %s", exc.code, exc.message)
                return False
            except Exception as exc:  # noqa: BLE001
                self.last_restore_error = NetworkError(
                    "Could not reach iCloud", str(exc)
                )
                LOGGER.info("session restore failed: %s", exc)
                return False
            finally:
                self.restoring = False

    @property
    def restore_is_retryable(self) -> bool:
        """
        Whether a failed restore is worth another go on its own.

        Not being able to reach Apple says nothing about the session -- the
        laptop woke up before its Wi-Fi did, or the VPN was mid-handshake. The
        app used to answer that with the sticky "iCloud sign-in needed" notice,
        which only a full sign-in clears, so a ten-second network blip cost a
        password. A credential Apple actually rejected, or a 2FA prompt, is a
        different thing: those genuinely need the person.
        """
        return isinstance(self.last_restore_error, NetworkError) and (
            self.has_saved_password
        )

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

        with self._lock:
            # A half-built session from a previous attempt must not survive into
            # this one. pyicloud hangs the whole 2FA challenge off the service
            # object, so keeping the old one around means a code minted for the
            # old challenge being checked against the new one.
            self._api = None
            self._pending_2fa = False
            self._two_factor = {}
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
                # Only if the service object survived the raise: with no session
                # there is nothing to arm, and failing that here would turn
                # "enter your code" into "sign in again".
                data = self.arm_2fa() if self._api is not None else {}
                raise TwoFactorRequired(
                    "Two-factor authentication required", str(exc), data=data
                ) from exc
            except PyiCloudFailedLoginException as exc:
                raise AuthRequired("iCloud rejected the sign-in", str(exc)) from exc
            except Exception as exc:  # noqa: BLE001
                raise NetworkError("Could not reach iCloud", str(exc)) from exc

            if self._api.requires_2fa:
                self._pending_2fa = True
                raise TwoFactorRequired(
                    "Two-factor authentication required", data=self.arm_2fa()
                )
            self._pending_2fa = False
            return self.status()

    # -- two-factor ----------------------------------------------------------
    #
    # pyicloud picks the verifier for a code from state that only
    # `request_2fa_code` establishes. For an account with trusted devices that
    # is Apple's HSA2 *bridge*: a live websocket to Apple's push service, over
    # which the code shown on the iPhone is negotiated. With no bridge in hand
    # `validate_2fa_code` quietly falls back to the legacy
    # /verify/trusteddevice/securitycode endpoint, which Apple no longer accepts
    # for these accounts -- so every code came back "invalid", however carefully
    # it was typed.
    #
    # Two rules follow, and the sign-in flow was breaking both:
    #
    #   1. Arm the challenge exactly once, and do it here, so the code that goes
    #      out is the code the verifier is expecting. Arming twice is not
    #      harmless -- each attempt makes Apple mint a *new* code and retire the
    #      previous one, so the message the user is reading is already dead.
    #   2. Never swallow a failure to arm. A bridge that would not bootstrap
    #      used to be caught and ignored by the sign-in screen, after which
    #      nothing could ever verify and the app blamed the user's typing.

    def arm_2fa(self) -> dict:
        """
        Ask Apple to deliver a verification code, and record how it went.

        Returns {sent, method, notice, error}. `method` is one of Apple's
        delivery routes -- trusted_device, sms, security_key -- because "enter
        the code on your iPhone" is useless advice to someone whose code came
        by text.
        """
        with self._lock:
            if self._api is None:
                raise AuthRequired("Not signed in")
            info: dict[str, Any] = {"sent": False, "method": "unknown", "notice": None}
            try:
                info["sent"] = bool(self._api.request_2fa_code())
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("2FA delivery failed: %s", exc)
                info["error"] = str(exc)
                if self._request_sms_code():
                    info["sent"] = True
                    info.pop("error", None)
            info["method"] = self._delivery_method()
            info["notice"] = getattr(self._api, "two_factor_delivery_notice", None)
            self._two_factor = info
            return dict(info)

    def _request_sms_code(self) -> bool:
        """
        Fall back to a text message when the trusted-device bridge will not come up.

        pyicloud only offers SMS once Apple has already said `mode == "sms"`,
        which it does not say while the account has trusted devices -- precisely
        the case where the bridge is the thing that just failed. A trusted phone
        number on the challenge is enough to ask for a text, so ask.
        """
        api = self._api
        try:
            if api._trusted_phone_number() is None:  # noqa: SLF001
                return False
            api._request_sms_2fa_code(  # noqa: SLF001
                notice="We couldn't reach your Apple devices, so we sent a text instead."
            )
            return True
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("SMS fallback failed: %s", exc)
            return False

    def _delivery_method(self) -> str:
        try:
            return str(self._api.two_factor_delivery_method)
        except Exception:  # noqa: BLE001
            return "unknown"

    def _challenge_is_armed(self) -> bool:
        """
        Whether there is a live challenge for a code to be checked against.

        The bridge is a websocket session that Apple closes after one verdict,
        so a rejected code leaves nothing behind. Noticing that here is what
        turns a second dead end into "here is a fresh code".
        """
        if self._delivery_method() != "trusted_device":
            # SMS and the legacy endpoint are stateless; they verify whenever.
            return True
        return getattr(self._api, "_trusted_device_bridge_state", None) is not None

    def request_2fa(self) -> dict:
        return self.arm_2fa()

    def submit_2fa(self, code: str) -> dict:
        with self._lock:
            if self._api is None:
                raise AuthRequired("Not signed in")

            # Apple's own mail and the Windows autofill both hand over "123 456",
            # and a stray space is not a wrong code.
            digits = re.sub(r"\D", "", code or "")
            if len(digits) != 6:
                raise TwoFactorRequired(
                    "Enter the six-digit code Apple sent you.",
                    data=dict(self._two_factor),
                )

            if self._delivery_method() == "security_key":
                raise TwoFactorRequired(
                    "This Apple ID verifies with a hardware security key, which "
                    "this app can't prompt for. Sign in at icloud.com once to "
                    "trust this computer, then try again.",
                    data=dict(self._two_factor),
                )

            if not self._challenge_is_armed():
                # There is genuinely nothing to check against: say so honestly
                # and put a fresh code in the user's hand, rather than calling
                # their typing wrong and leaving them to guess.
                info = self.arm_2fa()
                raise TwoFactorRequired(
                    "That code has expired. Apple has sent a new one -- enter "
                    "the code you just received.",
                    data=info,
                )

            try:
                accepted = bool(self._api.validate_2fa_code(digits))
            except Exception as exc:  # noqa: BLE001
                raise self._classify(exc) from exc

            if not accepted and not self._recover_from_a_false_rejection():
                raise TwoFactorRequired(
                    "Apple rejected that code. Check the six digits, or ask for "
                    "a new code.",
                    data=dict(self._two_factor),
                )

            if not self._api.is_trusted_session:
                try:
                    self._api.trust_session()
                except Exception as exc:  # noqa: BLE001 - non-fatal
                    LOGGER.warning("trust_session failed: %s", exc)
            self._pending_2fa = False
            self._two_factor = {}
            return self.status()

    def _recover_from_a_false_rejection(self) -> bool:
        """
        Decide whether a code pyicloud called False was actually fine.

        `validate_2fa_code` returns `not requires_2sa`, and that stays true
        whenever the trust handshake *after* a correct code did not land -- a
        slow link, or Apple taking a moment to mark the session trusted. Reported
        as "wrong code" it sends people round the loop typing codes that were
        never the problem, and each loop retires the code they were holding.

        A genuinely wrong code never reaches that handshake, and `trust_session`
        clears pyicloud's own mfa flag before it touches the network -- so the
        flag still being set is how we know the code itself was refused, and the
        retry below is skipped rather than costing the user five seconds.
        """
        if getattr(self._api, "_requires_mfa", True):
            return False
        for delay in (0.0, 1.5, 3.0):
            if delay:
                time.sleep(delay)
            try:
                if self._api.trust_session() and not self._api.requires_2fa:
                    return True
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("trust retry failed: %s", exc)
        return not self._api.requires_2fa

    def status(self) -> dict:
        if self._api is None:
            return {
                "authenticated": False,
                "apple_id": self.apple_id,
                # The UI waits on these instead of flashing the sign-in form at
                # someone who is already signed in.
                "restoring": self.restoring,
                "can_restore": self.has_saved_password,
                "needs_2fa": False,
                "two_factor": {},
            }
        return {
            "authenticated": self.connected,
            "apple_id": self.apple_id,
            "needs_2fa": bool(self._pending_2fa),
            # So the gate can go straight to the code box on a session Apple
            # expired, instead of asking for a password it already has.
            "two_factor": dict(self._two_factor),
            "trusted_session": bool(getattr(self._api, "is_trusted_session", False)),
            "restoring": self.restoring,
            "can_restore": self.has_saved_password,
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
        # No TimeZone is written, so the reminder floats and its stored
        # components are read back as local wall-clock -- what the phone does
        # for a reminder created on the device.
        due = instant_to_floating(_as_utc(due))
        try:
            rem = svc.create(
                list_id=payload["list_id"],
                title=payload.get("title") or "",
                desc=payload.get("description") or "",
                due_date=due,
                priority=int(payload.get("priority") or 0),
                flagged=bool(payload.get("flagged")),
                # Without this every reminder Windows creates is a timed one,
                # whatever the app was told. The server has already snapped the
                # instant to local midnight, which is the shape Apple stores.
                all_day=bool(payload.get("all_day")),
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
        if "all_day" in payload:
            rem.all_day = bool(payload["all_day"])
        if "due_date" in payload:
            due = payload["due_date"]
            if isinstance(due, str):
                due = datetime.fromisoformat(due)
            # Re-encoded in whatever zone this record already carries, so
            # editing a reminder pinned to another zone doesn't quietly move it.
            rem.due_date = instant_to_floating(_as_utc(due), rem.time_zone)
            # A bare due date still implies a time was picked -- but only when
            # the caller said nothing about all_day. Assuming it unconditionally
            # is what made all-day reminders impossible to keep.
            if due is not None and "all_day" not in payload:
                rem.all_day = False

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
            "due_date": (
                floating_to_instant(r.due_date, r.time_zone).isoformat()
                if r.due_date
                else None
            ),
            "time_zone": r.time_zone or None,
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
        # Already ours: classifying it again is how a NetworkError whose detail
        # happened to mention a 403 turned into "you have been signed out".
        if isinstance(exc, SidecarError):
            return exc

        name = type(exc).__name__
        text = str(exc)
        if "AcceptTerms" in name or "termsUpdateNeeded" in text:
            return TermsRequired(
                "Apple requires you to accept updated iCloud terms.", text
            )
        if (
            "2FA" in name
            or "RemindersAuthError" in name
            or "FailedLogin" in name
            or _UNAUTHORIZED.search(text) is not None
        ):
            return AuthRequired("Your iCloud session expired. Please sign in again.", text)
        return NetworkError("iCloud request failed", text)
