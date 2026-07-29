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
from .icloud import AuthRequired, ICloudClient, SidecarError
from .notifications import plan_notifications
from .sync import CURSOR_KEY, LAST_SYNC_KEY, SyncEngine

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
            "request_2fa": lambda p: {"sent": self.client.request_2fa()},
            "lists": lambda p: self.cache.lists(),
            "reminders": self.m_reminders,
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
            "shutdown": self.m_shutdown,
        }

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
        st = self.client.connect(
            password=p.get("password"), accept_terms=bool(p.get("accept_terms"))
        )
        self._kick_sync(full=not self.cache.lists())
        return st

    def m_submit_2fa(self, p: dict) -> dict:
        st = self.client.submit_2fa(p["code"])
        self._kick_sync(full=not self.cache.lists())
        return st

    # reads -----------------------------------------------------------------
    def m_reminders(self, p: dict) -> list[dict]:
        return self.cache.reminders(
            list_id=p.get("list_id"),
            tag=p.get("tag"),
            include_completed=bool(p.get("include_completed")),
            search=p.get("search"),
            limit=int(p.get("limit") or 1000),
        )

    # writes ----------------------------------------------------------------
    def m_create_reminder(self, p: dict) -> dict:
        import uuid

        local_id = f"local/{uuid.uuid4()}"
        row = {
            "id": local_id,
            "list_id": p["list_id"],
            "title": p.get("title") or "",
            "description": p.get("description") or "",
            "due_date": self._norm_due(p.get("due_date")),
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
        if "due_date" in p:
            fields["due_date"] = self._norm_due(p["due_date"])
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
    def _norm_due(value: Any) -> Optional[str]:
        """Accept ISO strings or epoch millis; always store tz-aware UTC ISO."""
        if value in (None, ""):
            return None
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat()
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            # A bare local datetime from a date picker: interpret in the user's
            # zone, never as UTC. Apple would silently assume UTC here.
            dt = dt.astimezone()
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
                self.sync.flush_outbox()
            except SidecarError as exc:
                self.emit("sync_error", exc.to_dict())
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("push failed")

        threading.Thread(target=run, daemon=True, name="push").start()

    # notifications ---------------------------------------------------------
    def m_due_notifications(self, p: dict) -> dict:
        now = from_iso(p.get("now")) or utcnow()
        plan = plan_notifications(
            self.cache,
            now=now,
            stale_after_minutes=int(p.get("stale_after_minutes") or 60),
            max_individual=int(p.get("max_individual") or 3),
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
