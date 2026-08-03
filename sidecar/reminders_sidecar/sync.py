"""
Sync engine: pulls iCloud into the cache and pushes local edits back out.

Two pull modes:

  full_sync   -- every list, every reminder. Used on first run and on demand.
                 The real account this was built against holds ~2,900 reminders
                 across 14 lists, so this reports progress rather than blocking
                 silently.

  delta_sync  -- iter_changes(since=cursor). Cheap, runs on the background
                 timer. It only ever reports reminders, so lists are refreshed
                 wholesale on every delta pass too. That is not redundancy; it
                 is the only way a renamed or deleted list is ever noticed.

Push is an outbox: local edits land in the cache immediately, then drain to
iCloud. A push whose base change tag no longer matches the server is recorded
as a conflict instead of overwriting -- the phone writes to the same records
and was observed replacing fields wholesale rather than merging.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Callable, Optional

from .db import Cache, to_iso, utcnow
from .icloud import AuthRequired, ConflictError, ICloudClient, SidecarError

LOGGER = logging.getLogger(__name__)

CURSOR_KEY = "sync_cursor"
LAST_FULL_KEY = "last_full_sync"
LAST_SYNC_KEY = "last_sync"


class SyncEngine:
    def __init__(
        self,
        cache: Cache,
        client: ICloudClient,
        emit: Optional[Callable[[str, dict], None]] = None,
    ):
        self.cache = cache
        self.client = client
        self.emit = emit or (lambda _e, _d: None)
        self._lock = threading.Lock()
        # Writes kick a push each, so several drains can be requested at once.
        # Only one may run: two draining the same rows would push twice.
        self._push_lock = threading.Lock()
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------ pull
    def full_sync(self) -> dict:
        """Rebuild the cache from scratch. Safe to run at any time."""
        with self._lock:
            self._running = True
            try:
                self.emit("sync_started", {"mode": "full", "determinate": True})

                # Take the cursor BEFORE reading, so anything that changes while
                # we page through is picked up by the next delta rather than lost.
                cursor = self.client.sync_cursor()

                lists = self.client.lists()
                self.cache.replace_lists(lists)

                # Progress is weighted by each list's reminder count rather than
                # counting lists, because the work is wildly uneven -- one list
                # on the account this was built against holds 1,219 of the 2,900
                # records. A bar that moves in equal steps per list would sit at
                # 90% for most of the sync.
                work = [l for l in lists if not l.get("is_group")]
                weights = [max(int(l.get("count") or 0), 1) for l in work]
                budget = sum(weights) or 1
                self.emit(
                    "sync_progress",
                    {
                        "stage": "lists",
                        "count": len(lists),
                        "expected": sum(int(l.get("count") or 0) for l in work),
                        "percent": 0.0,
                        "done": 0,
                        "of": len(work),
                    },
                )

                total = 0
                spent = 0
                for i, l in enumerate(work):
                    reminders, tags_by = self.client.reminders_for(l["id"])
                    self.cache.upsert_reminders(reminders)
                    for rid, tags in tags_by.items():
                        self.cache.replace_tags_for(rid, tags)
                    total += len(reminders)
                    spent += weights[i]
                    self.emit(
                        "sync_progress",
                        {
                            "stage": "reminders",
                            "list": l["title"],
                            "index": i + 1,
                            "done": i + 1,
                            "of": len(work),
                            "total": total,
                            "percent": round(100.0 * spent / budget, 1),
                        },
                    )

                if cursor:
                    self.cache.set_meta(CURSOR_KEY, cursor)
                now = to_iso(utcnow())
                self.cache.set_meta(LAST_FULL_KEY, now)
                self.cache.set_meta(LAST_SYNC_KEY, now)
                self.emit("sync_finished", {"mode": "full", "reminders": total})
                return {"mode": "full", "lists": len(lists), "reminders": total}
            finally:
                self._running = False

    def delta_sync(self) -> dict:
        """Apply changes since the stored cursor. Falls back to full if unset."""
        cursor = self.cache.get_meta(CURSOR_KEY)
        if not cursor:
            return self.full_sync()

        with self._lock:
            self._running = True
            try:
                # A delta has no measurable size until the changes come back, so
                # the bar animates rather than lying about a percentage.
                self.emit("sync_started", {"mode": "delta", "determinate": False})

                # Lists never appear in the delta stream -- always refresh them.
                lists = self.client.lists()
                self.cache.replace_lists(lists)
                self.emit("sync_progress", {"stage": "lists", "count": len(lists)})

                changes = self.client.changes_since(cursor)
                updated = [c for c in changes if not c.get("deleted")]
                deleted = [c for c in changes if c.get("deleted")]
                self.emit(
                    "sync_progress",
                    {"stage": "changes", "total": len(changes)},
                )

                if updated:
                    self.cache.upsert_reminders(updated)
                    self._refresh_tags_for_changed(updated)
                for d in deleted:
                    self.cache.apply_local_edit(d["id"], {"deleted": 1})
                    self.cache.clear_dirty(d["id"], None)

                new_cursor = self.client.sync_cursor()
                if new_cursor:
                    self.cache.set_meta(CURSOR_KEY, new_cursor)
                self.cache.set_meta(LAST_SYNC_KEY, to_iso(utcnow()))

                self.emit(
                    "sync_finished",
                    {"mode": "delta", "updated": len(updated), "deleted": len(deleted)},
                )
                return {
                    "mode": "delta",
                    "updated": len(updated),
                    "deleted": len(deleted),
                }
            finally:
                self._running = False

    def _refresh_tags_for_changed(self, changed: list[dict]) -> None:
        """
        Re-read tags for reminders the delta touched.

        Hashtag records don't come through iter_changes, and the phone rewrites
        HashtagIDs wholesale, so a changed reminder's tags cannot be trusted to
        be what we already had. Only reminders that actually reference hashtags
        are re-read, to keep this cheap.
        """
        for c in changed:
            ids = c.get("hashtag_ids")
            if ids is None:
                continue
            if not ids:
                self.cache.replace_tags_for(c["id"], [])
                continue
            try:
                rem = self.client._service().get(c["id"])  # noqa: SLF001
                self.cache.replace_tags_for(c["id"], self.client.tags_for(rem))
            except Exception as exc:  # noqa: BLE001 - tags are cosmetic; never fail sync
                LOGGER.debug("tag refresh failed for %s: %s", c["id"], exc)

    # ------------------------------------------------------------------ push
    def flush_outbox(self) -> dict:
        """
        Drain queued local edits to iCloud.

        Conflicts are recorded, not resolved: the server copy wins in the cache
        so the UI matches reality, and the local version is preserved in the
        conflicts table for the user to re-apply.
        """
        if not self._push_lock.acquire(blocking=False):
            # Another drain is already in flight; it will pick up whatever was
            # queued in the meantime, so this call is redundant rather than lost.
            return {"pushed": 0, "conflicts": 0, "failed": 0, "skipped": True}
        try:
            return self._drain()
        finally:
            self._push_lock.release()

    def push_now(self) -> dict:
        """A push on its own, with the same expired-session recovery as a sync."""
        return self._with_reauth(self.flush_outbox)

    def _drain(self) -> dict:
        pushed = 0
        conflicts = 0
        failed = 0
        expired: Optional[SidecarError] = None

        for item in self.cache.pending():
            seq = item["seq"]
            op = item["op"]
            rid = item["reminder_id"]
            payload = item["payload"]

            try:
                if op == "create":
                    created = self.client.create(payload)
                    self.cache.replace_id(rid, created["id"])
                    self.cache.upsert_reminders([created])
                    self.cache.clear_dirty(created["id"], created.get("change_tag"))
                elif op == "update":
                    updated = self.client.update(rid, payload, item.get("base_tag"))
                    self.cache.clear_dirty(rid, updated.get("change_tag"))
                    self.cache.upsert_reminders([updated])
                elif op == "delete":
                    self.client.delete(rid)
                    self.cache.clear_dirty(rid, None)
                else:
                    LOGGER.warning("unknown outbox op %r", op)
                self.cache.dequeue(seq)
                pushed += 1

            except ConflictError as exc:
                local = self.cache.reminder(rid) or {}
                remote = {}
                if exc.detail:
                    try:
                        import json

                        remote = json.loads(exc.detail)
                    except ValueError:
                        remote = {"raw": exc.detail}
                self.cache.record_conflict(rid, local, remote)
                # Server truth into the cache; the local edit lives on in the
                # conflict row rather than being silently dropped or forced.
                if remote:
                    self.cache.clear_dirty(rid, remote.get("change_tag"))
                    self.cache.upsert_reminders([remote])
                self.cache.dequeue(seq)
                conflicts += 1
                self.emit("conflict", {"reminder_id": rid})

            except SidecarError as exc:
                self.cache.record_failure(seq, exc.message)
                failed += 1
                self.emit("push_failed", {"reminder_id": rid, "error": exc.to_dict()})
                # Auth problems will fail every remaining item too; stop early.
                if exc.code in ("AUTH_REQUIRED", "2FA_REQUIRED", "TERMS_REQUIRED"):
                    # An expired session is recoverable, so it has to escape this
                    # loop rather than being absorbed into a failure count --
                    # otherwise the caller never learns there is anything to
                    # retry. The item stays queued; only its attempt count moved.
                    if exc.code == "AUTH_REQUIRED":
                        expired = exc
                    break

        if expired is not None:
            raise expired
        return {"pushed": pushed, "conflicts": conflicts, "failed": failed}

    def sync_now(self, full: bool = False) -> dict:
        """Push first, then pull, so local edits aren't clobbered by our own pull."""
        push = self._with_reauth(self.flush_outbox)
        pull = self._with_reauth(self.full_sync if full else self.delta_sync)
        return {"push": push, "pull": pull}

    def _with_reauth(self, fn: Callable[[], dict]) -> dict:
        """
        Run a sync step, rebuilding the session once if Apple has expired it.

        iCloud session tokens do not last forever, and the old behaviour was to
        surface that as "sign in again" on a timer -- every background pass after
        expiry threw the user back to the login screen. A restore uses the saved
        credential and the trust token, so it needs nothing from them. Only one
        retry: if the second attempt still fails, the session really is gone and
        the sign-in prompt is the honest answer.
        """
        try:
            return fn()
        except AuthRequired:
            if not self.client.restore():
                raise
            LOGGER.info("iCloud session restored; retrying %s", getattr(fn, "__name__", fn))
            self.emit("auth_changed", self.client.status())
            return fn()
