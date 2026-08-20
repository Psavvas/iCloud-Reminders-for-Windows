"""
Newline-delimited JSON over stdio. One JSON object per line, both directions.

Request   {"id": 1, "method": "lists", "params": {}}
Response  {"id": 1, "ok": true, "result": ...}
          {"id": 1, "ok": false, "error": {"code": "...", "message": "..."}}
Event     {"event": "sync_progress", "data": {...}}          (unsolicited)

Reads are served straight from SQLite, so the UI never waits on the network.
Sync runs on a worker thread; the read loop keeps answering while a 2,900-record
full sync is in flight.

stdout is reserved for protocol traffic. Logging goes to stderr, or a stray
print would corrupt the stream.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .db import Cache, from_iso, to_iso, utcnow
from .icloud import AuthRequired, ICloudClient, SidecarError, TwoFactorRequired
from .notifications import plan_notifications
from .sync import CURSOR_KEY, LAST_SYNC_KEY, SyncEngine
from .timeutil import local_zone

LOGGER = logging.getLogger("sidecar")


class Server:
    def __init__(self, db_path: str, apple_id: Optional[str], cookie_dir: Optional[str]):
        self.cache = Cache(db_path)
        self.apple_id = apple_id or self.cache.get_meta("apple_id") or ""
        self.client = ICloudClient(self.apple_id, cookie_dir=cookie_dir)
        self.sync = SyncEngine(self.cache, self.client, emit=self.emit)
        self._out_lock = threading.Lock()
        self._stop = threading.Event()
        self._methods: dict[str, Callable[[dict], Any]] = self._build_methods()

    # ------------------------------------------------------------- transport
    def _write(self, obj: dict) -> None:
        line = json.dumps(obj, default=str, separators=(",", ":"))
        with self._out_lock:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    def emit(self, event: str, data: dict) -> None:
        self._write({"event": event, "data": data})

    # --------------------------------------------------------------- methods
    def _build_methods(self) -> dict[str, Callable[[dict], Any]]:
        return {
            "ping": lambda p: {"pong": True},
            "auth_status": self.m_auth_status,
            "login": self.m_login,
            "submit_2fa": self.m_submit_2fa,
            "request_2fa": lambda p: self.client.request_2fa(),
            "lists": lambda p: self.cache.lists(),
            "reminders": self.m_reminders,
            "smart_counts": lambda p: self.cache.smart_counts(),
            "settings": lambda p: self.cache.get_settings(),
            "set_settings": lambda p: self.cache.set_settings(p or {}),
            "sign_out": self.m_sign_out,
            "restore_reminder": self.m_restore_reminder,
            "reminder": lambda p: self.cache.reminder(p["id"]),
            "tags": lambda p: self.cache.all_tags(),
            "create_reminder": self.m_create_reminder,
            "update_reminder": self.m_update_reminder,
            "delete_reminder": self.m_delete_reminder,
            "sync": self.m_sync,
            "sync_status": self.m_sync_status,
            "due_notifications": self.m_due_notifications,
            "conflicts": lambda p: self.cache.conflicts(),
            "resolve_conflict": self.m_resolve_conflict,
            "due_probe": self.m_due_probe,
            "shutdown": self.m_shutdown,
        }

    def m_due_probe(self, p: dict) -> dict:
        """
        Dump what iCloud actually stores in DueDate, both ways round.

        Backs `scripts/check-due-dates.py`. The wall-clock reading and the
        instant reading differ by the local UTC offset, so comparing them
        against what an iPhone shows settles which one Apple means -- rather
        than the app assuming and being wrong by four hours or a whole day.
        """
        from .timeutil import floating_to_instant, local_zone

        svc = self.client._service()  # noqa: SLF001 - diagnostic, by design
        rows: list[dict] = []
        for l in self.client.lists():
            if l.get("is_group") or len(rows) >= int(p.get("limit") or 12):
                continue
            batch = svc.list_reminders(
                list_id=l["id"], include_completed=False, results_limit=50
            )
            for r in batch.reminders:
                if r.due_date is None or r.deleted:
                    continue
                raw = r.due_date.astimezone(timezone.utc)
                rows.append(
                    {
                        "list": l["title"],
                        "title": (r.title or "")[:48],
                        "all_day": bool(r.all_day),
                        "time_zone": r.time_zone,
                        "raw_ms": int(raw.timestamp() * 1000),
                        "raw_utc": raw.isoformat(),
                        # What the app shows now, and what it showed before.
                        "as_wall_clock": floating_to_instant(
                            r.due_date, r.time_zone
                        ).astimezone().isoformat(),
                        "as_instant": raw.astimezone().isoformat(),
                    }
                )
                if len(rows) >= int(p.get("limit") or 12):
                    break
        return {"zone": str(local_zone()), "reminders": rows}

    # auth ------------------------------------------------------------------
    def m_auth_status(self, p: dict) -> dict:
        st = self.client.status()
        st["has_cache"] = bool(self.cache.lists())
        st["last_sync"] = self.cache.get_meta(LAST_SYNC_KEY)
        return st

    def m_login(self, p: dict) -> dict:
        apple_id = p.get("apple_id") or self.apple_id
        if not apple_id:
            raise AuthRequired("No Apple ID provided")
        if apple_id != self.apple_id:
            self.apple_id = apple_id
            self.client.apple_id = apple_id
        self.cache.set_meta("apple_id", apple_id)

        password = p.get("password")
        remember = self.cache.get_settings().get("remember_password", True)
        try:
            st = self.client.connect(
                password=password, accept_terms=bool(p.get("accept_terms"))
            )
        except TwoFactorRequired:
            # The password itself was accepted -- 2FA is the *second* factor --
            # so it is worth keeping even though this call is about to fail.
            if password and remember:
                self.client.remember_password(password)
            raise
        if password and remember:
            self.client.remember_password(password)
        self._kick_sync(full=not self.cache.lists())
        return st

    def m_submit_2fa(self, p: dict) -> dict:
        st = self.client.submit_2fa(p["code"])
        self._kick_sync(full=not self.cache.lists())
        return st

    # How long to wait between restore attempts that failed for reasons that
    # have nothing to do with the session. A laptop resuming from sleep, or a
    # machine that launched the app before its Wi-Fi came up, is back inside a
    # couple of minutes; the last step keeps a longer outage from turning into a
    # password prompt the moment the user looks at the window.
    RESTORE_BACKOFF_SECONDS = (5, 15, 45, 120, 300)

    def _restore_session(self) -> None:
        """
        Try to pick up where the last run left off, in the background.

        Without this the sidecar starts with no session at all and reports
        "not authenticated" on every launch, so the app asks for a password each
        time even though the stored session was still good. It runs off-thread
        because it talks to Apple and the stdio loop must stay responsive.

        A failure to reach Apple is retried rather than reported: launching
        before the network is up is the single most common way this fails, and
        answering it with the sign-in form is what "it signs me out all the
        time" looked like from the outside.
        """
        if self.client.connected or not self.apple_id:
            return

        # Set before the thread starts: the UI calls auth_status the moment it
        # sees `ready`, and a False here would send it to the sign-in form for
        # the split second before the restore reports in.
        self.client.restoring = True

        def run():
            ok = self.client.restore()
            for delay in self.RESTORE_BACKOFF_SECONDS:
                if ok or not getattr(self.client, "restore_is_retryable", False):
                    break
                # Keep saying "restoring" across the wait, so the UI holds the
                # "signing you back in" state instead of dropping to a password
                # form over a blip it is about to recover from.
                self.client.restoring = True
                if self._stop.wait(delay):
                    return
                ok = self.client.restore()
            self.emit("auth_changed", self.client.status())
            if ok:
                self._kick_sync(full=not self.cache.lists())

        threading.Thread(target=run, daemon=True, name="restore").start()

    # reads -----------------------------------------------------------------
    def m_reminders(self, p: dict) -> list[dict]:
        return self.cache.reminders(
            list_id=p.get("list_id"),
            tag=p.get("tag"),
            scope=p.get("scope"),
            include_completed=bool(p.get("include_completed")),
            search=p.get("search"),
            sort=p.get("sort"),
            limit=int(p.get("limit") or 1000),
        )

    def m_sign_out(self, p: dict) -> dict:
        """
        Drop the session but keep the cache, so the app still shows data while
        signed out. Passing purge=true clears the cached reminders too.
        """
        # Signing out has to clear the saved credential too, or the next launch
        # would silently sign straight back in.
        self.client.forget_password()
        self.client = ICloudClient(self.apple_id, cookie_dir=self.client.cookie_dir)
        self.sync = SyncEngine(self.cache, self.client, emit=self.emit)
        if p.get("purge"):
            self.cache.set_meta(CURSOR_KEY, None)
        return {"signed_out": True}

    def m_restore_reminder(self, p: dict) -> dict:
        """Undo a soft delete, which is what makes the Deleted list useful."""
        rid = p["id"]
        current = self.cache.reminder(rid)
        if not current:
            raise SidecarError(f"No such reminder: {rid}")
        self.cache.apply_local_edit(rid, {"deleted": 0})
        self.cache.enqueue(rid, "update", {"deleted": False}, current.get("change_tag"))
        self._kick_push()
        return self.cache.reminder(rid)

    # writes ----------------------------------------------------------------
    def m_create_reminder(self, p: dict) -> dict:
        import uuid

        local_id = f"local/{uuid.uuid4()}"
        row = {
            "id": local_id,
            "list_id": p["list_id"],
            "title": p.get("title") or "",
            "description": p.get("description") or "",
            "due_date": self._norm_due(p.get("due_date"), bool(p.get("all_day"))),
            "priority": int(p.get("priority") or 0),
            "completed": False,
            "flagged": bool(p.get("flagged")),
            "all_day": bool(p.get("all_day")),
        }
        self.cache.insert_local_reminder(row)
        self.cache.enqueue(local_id, "create", row, None)
        self._kick_push()
        return self.cache.reminder(local_id) or row

    def m_update_reminder(self, p: dict) -> dict:
        rid = p["id"]
        current = self.cache.reminder(rid)
        if not current:
            raise SidecarError(f"No such reminder: {rid}")
        fields = {
            k: v
            for k, v in p.items()
            if k in ("title", "description", "priority", "completed", "flagged")
        }
        # all_day only moves when it is sent. Editing the date of an all-day
        # reminder should leave it all-day, and editing a timed one should leave
        # it timed -- the toggle is what changes the kind, not the date field.
        if "all_day" in p:
            fields["all_day"] = 1 if p["all_day"] else 0
        all_day = bool(fields.get("all_day", current.get("all_day")))

        if "due_date" in p:
            fields["due_date"] = self._norm_due(p["due_date"], all_day)
        elif "all_day" in p and all_day and current.get("due_date"):
            # Switched to all-day without touching the date: the old time has to
            # go, or this is an "all-day" reminder that still notifies at 14:30.
            fields["due_date"] = self._norm_due(current["due_date"], True)
        if "completed" in fields:
            fields["completed"] = 1 if fields["completed"] else 0
        if "flagged" in fields:
            fields["flagged"] = 1 if fields["flagged"] else 0

        self.cache.apply_local_edit(rid, fields)
        self.cache.enqueue(rid, "update", fields, current.get("change_tag"))
        self._kick_push()
        return self.cache.reminder(rid)

    def m_delete_reminder(self, p: dict) -> dict:
        rid = p["id"]
        current = self.cache.reminder(rid)
        if not current:
            raise SidecarError(f"No such reminder: {rid}")
        self.cache.apply_local_edit(rid, {"deleted": 1})
        self.cache.enqueue(rid, "delete", {}, current.get("change_tag"))
        self._kick_push()
        return {"deleted": rid}

    @staticmethod
    def _norm_due(value: Any, all_day: bool = False) -> Optional[str]:
        """
        Accept ISO strings or epoch millis; always store tz-aware UTC ISO.

        An all-day reminder's instant is local midnight -- notify_at shifts off
        it to find the morning, and end_of_day counts from it to decide overdue.
        Both break on an "all-day" reminder stored at 14:30, and a date input
        that yields midnight does so only by convention. Snapping here means
        nothing downstream has to trust the caller.
        """
        if value in (None, ""):
            return None
        if isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(value))
            if dt.tzinfo is None:
                # A bare local datetime from a date picker: interpret in the
                # user's zone, never as UTC. Apple would silently assume UTC.
                dt = dt.astimezone()
        if all_day:
            # Midnight where the user is, not in UTC -- the date is the point.
            dt = dt.astimezone(local_zone()).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        return dt.astimezone(timezone.utc).isoformat()

    # sync ------------------------------------------------------------------
    def m_sync(self, p: dict) -> dict:
        if self.sync.running:
            return {"queued": False, "reason": "already running"}
        self._kick_sync(full=bool(p.get("full")))
        return {"queued": True}

    def m_sync_status(self, p: dict) -> dict:
        return {
            "running": self.sync.running,
            "last_sync": self.cache.get_meta(LAST_SYNC_KEY),
            "has_cursor": bool(self.cache.get_meta(CURSOR_KEY)),
            "pending_pushes": len(self.cache.pending()),
            "conflicts": len(self.cache.conflicts()),
            "sync_minutes": int(self.cache.get_settings().get("sync_minutes") or 10),
        }

    def _kick_sync(self, full: bool = False) -> None:
        def run():
            try:
                self.sync.sync_now(full=full)
            except SidecarError as exc:
                self.emit("sync_error", exc.to_dict())
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("sync failed")
                self.emit("sync_error", {"code": "ERROR", "message": str(exc)})

        threading.Thread(target=run, daemon=True, name="sync").start()

    def _kick_push(self) -> None:
        def run():
            try:
                self.sync.push_now()
            except SidecarError as exc:
                self.emit("sync_error", exc.to_dict())
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("push failed")

        threading.Thread(target=run, daemon=True, name="push").start()

    # notifications ---------------------------------------------------------
    def m_due_notifications(self, p: dict) -> dict:
        cfg = self.cache.get_settings()
        if not cfg.get("notifications_enabled", True):
            return {"toasts": [], "notified_ids": []}
        now = from_iso(p.get("now")) or utcnow()
        plan = plan_notifications(
            self.cache,
            now=now,
            stale_after_minutes=int(
                p.get("stale_after_minutes") or cfg.get("stale_after_minutes") or 60
            ),
            max_individual=int(
                p.get("max_individual") or cfg.get("max_individual_toasts") or 3
            ),
        )
        return plan.to_dict()

    def m_resolve_conflict(self, p: dict) -> dict:
        cid = int(p["id"])
        if p.get("keep") == "local":
            row = next((c for c in self.cache.conflicts() if c["id"] == cid), None)
            if row:
                local = row["local"]
                fields = {
                    k: local.get(k)
                    for k in ("title", "description", "due_date", "priority", "completed")
                    if k in local
                }
                self.cache.apply_local_edit(local["id"], fields)
                current = self.cache.reminder(local["id"]) or {}
                self.cache.enqueue(
                    local["id"], "update", fields, current.get("change_tag")
                )
                self._kick_push()
        self.cache.resolve_conflict(cid)
        return {"resolved": cid}

    def m_shutdown(self, p: dict) -> dict:
        self._stop.set()
        return {"bye": True}

    # ------------------------------------------------------------------ loop
    def serve(self) -> int:
        self.emit("ready", {"apple_id": self.apple_id})
        self._restore_session()
        for line in sys.stdin:
            if self._stop.is_set():
                break
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except ValueError:
                self._write({"id": None, "ok": False, "error": {
                    "code": "BAD_REQUEST", "message": "invalid JSON"}})
                continue

            rid = req.get("id")
            method = req.get("method")
            params = req.get("params") or {}
            fn = self._methods.get(method or "")
            if fn is None:
                self._write({"id": rid, "ok": False, "error": {
                    "code": "NO_METHOD", "message": f"unknown method {method!r}"}})
                continue

            try:
                result = fn(params)
                self._write({"id": rid, "ok": True, "result": result})
            except SidecarError as exc:
                self._write({"id": rid, "ok": False, "error": exc.to_dict()})
            except Exception as exc:  # noqa: BLE001
                LOGGER.error("method %s failed\n%s", method, traceback.format_exc())
                self._write({"id": rid, "ok": False, "error": {
                    "code": "ERROR", "message": str(exc)}})

        self.cache.close()
        return 0
