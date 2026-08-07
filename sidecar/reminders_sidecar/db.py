"""
Local SQLite cache.

The UI reads only from here, never from the network. Everything the network
layer learns gets written here first; the UI is then told to refresh.

Time policy, per the Phase 1 landmine: every datetime crossing this boundary is
tz-aware UTC, stored as ISO-8601 with an explicit offset. Naive datetimes are
rejected rather than guessed at.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

SCHEMA_VERSION = 2

# The Completed view shows only this many, most recent first. Apple's own client
# does the same; the full history is thousands of rows on a real account and
# rendering it makes the app feel slow for no benefit.
COMPLETED_LIMIT = 50

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS lists (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    color_hex  TEXT,
    count      INTEGER NOT NULL DEFAULT 0,
    is_group   INTEGER NOT NULL DEFAULT 0,
    position   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS reminders (
    id           TEXT PRIMARY KEY,
    list_id      TEXT NOT NULL,
    title        TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    due_date     TEXT,               -- ISO-8601 UTC, or NULL
    priority     INTEGER NOT NULL DEFAULT 0,
    completed    INTEGER NOT NULL DEFAULT 0,
    completed_date TEXT,               -- ISO-8601 UTC, when it was ticked off
    flagged      INTEGER NOT NULL DEFAULT 0,
    all_day      INTEGER NOT NULL DEFAULT 0,
    deleted      INTEGER NOT NULL DEFAULT 0,
    created      TEXT,
    modified     TEXT,
    change_tag   TEXT,               -- CloudKit recordChangeTag, for conflicts
    notified     INTEGER NOT NULL DEFAULT 0,
    dirty        INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_reminders_list ON reminders(list_id);
CREATE INDEX IF NOT EXISTS idx_reminders_due  ON reminders(due_date)
    WHERE completed = 0 AND deleted = 0;

CREATE TABLE IF NOT EXISTS tags (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    reminder_id TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tags_reminder ON tags(reminder_id);
CREATE INDEX IF NOT EXISTS idx_tags_name     ON tags(name);

-- Local edits waiting to reach iCloud. Kept separate from `reminders` so a
-- failed push never corrupts what the UI displays.
CREATE TABLE IF NOT EXISTS outbox (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    reminder_id TEXT NOT NULL,
    op          TEXT NOT NULL,       -- create | update | delete
    payload     TEXT NOT NULL,       -- JSON
    base_tag    TEXT,                -- change_tag we edited from
    created_at  TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT
);

-- Divergence between a local edit and a remote change to the same reminder.
-- Recorded rather than silently resolved: the phone also writes.
CREATE TABLE IF NOT EXISTS conflicts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    reminder_id TEXT NOT NULL,
    local_json  TEXT NOT NULL,
    remote_json TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    resolved    INTEGER NOT NULL DEFAULT 0
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: Optional[datetime]) -> Optional[str]:
    """Serialize a tz-aware datetime as UTC ISO-8601. Naive input is a bug."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        raise ValueError(
            "naive datetime reached the cache; callers must pass tz-aware values"
        )
    return dt.astimezone(timezone.utc).isoformat()


def from_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class Cache:
    """
    Thread-safe SQLite wrapper.

    One connection guarded by a lock. Sync runs on a background thread while
    the stdio loop keeps serving reads, so concurrent access is normal here.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._conn.commit()
        self.set_meta("schema_version", str(SCHEMA_VERSION))

    def _migrate(self) -> None:
        """
        Additive migrations for caches created by an earlier version.

        The cache is disposable -- a full sync rebuilds it -- but silently
        wiping someone's queued edits would not be, so columns are added in
        place rather than by recreating the table.
        """
        cols = {
            r["name"]
            for r in self._conn.execute("PRAGMA table_info(reminders)").fetchall()
        }
        if "completed_date" not in cols:
            self._conn.execute("ALTER TABLE reminders ADD COLUMN completed_date TEXT")
            # Nothing to backfill from: the column did not exist, so the value
            # was never recorded. The next full sync fills it in.
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reminders_completed "
            "ON reminders(completed_date DESC) WHERE completed = 1 AND deleted = 0"
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ meta
    def get_meta(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: Optional[str]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()

    # ----------------------------------------------------------------- lists
    def replace_lists(self, lists: Iterable[dict]) -> None:
        """
        Full refresh of the list table.

        List changes never appear in the delta stream (iter_changes filters to
        recordType == 'Reminder'), so lists are always refreshed wholesale.
        """
        rows = [
            (
                l["id"],
                l.get("title") or "Untitled",
                l.get("color_hex"),
                int(l.get("count") or 0),
                1 if l.get("is_group") else 0,
                i,
            )
            for i, l in enumerate(lists)
        ]
        with self._lock:
            self._conn.execute("DELETE FROM lists")
            self._conn.executemany(
                "INSERT INTO lists(id,title,color_hex,count,is_group,position) "
                "VALUES(?,?,?,?,?,?)",
                rows,
            )
            self._conn.commit()

    def lists(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT l.*, ("
                "  SELECT COUNT(*) FROM reminders r"
                "   WHERE r.list_id = l.id AND r.completed = 0 AND r.deleted = 0"
                ") AS open_count "
                "FROM lists l ORDER BY l.position"
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------- reminders
    def upsert_reminders(self, reminders: Iterable[dict]) -> int:
        """
        Insert or update reminders from the server.

        Preserves `notified` so a re-sync never re-fires a toast, and refuses to
        stomp a row still carrying an unpushed local edit (dirty = 1).
        """
        n = 0
        with self._lock:
            for r in reminders:
                existing = self._conn.execute(
                    "SELECT notified, dirty FROM reminders WHERE id = ?", (r["id"],)
                ).fetchone()
                if existing and existing["dirty"]:
                    # Local edit not yet pushed. Sync records the remote copy as
                    # a conflict elsewhere; don't overwrite what the user sees.
                    continue
                notified = existing["notified"] if existing else 0
                self._conn.execute(
                    "INSERT INTO reminders("
                    " id,list_id,title,description,due_date,priority,completed,"
                    " completed_date,flagged,all_day,deleted,created,modified,"
                    " change_tag,notified,dirty"
                    ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    " list_id=excluded.list_id, title=excluded.title,"
                    " description=excluded.description, due_date=excluded.due_date,"
                    " priority=excluded.priority, completed=excluded.completed,"
                    " completed_date=excluded.completed_date,"
                    " flagged=excluded.flagged, all_day=excluded.all_day,"
                    " deleted=excluded.deleted, modified=excluded.modified,"
                    " change_tag=excluded.change_tag, notified=excluded.notified",
                    (
                        r["id"],
                        r.get("list_id") or "",
                        r.get("title") or "",
                        r.get("description") or "",
                        r.get("due_date"),
                        int(r.get("priority") or 0),
                        1 if r.get("completed") else 0,
                        r.get("completed_date"),
                        1 if r.get("flagged") else 0,
                        1 if r.get("all_day") else 0,
                        1 if r.get("deleted") else 0,
                        r.get("created"),
                        r.get("modified"),
                        r.get("change_tag"),
                        notified,
                    ),
                )
                n += 1
            self._conn.commit()
        return n

    @staticmethod
    def _order_clause(sort: Optional[str], scope: Optional[str] = None) -> str:
        """
        SQL ordering for a sort mode. Kept here rather than in the UI so a
        1,219-row list is ordered and truncated by SQLite, not by JavaScript.
        """
        # Undated sorts last in every date-based mode; an undated reminder is
        # not "infinitely soon".
        due_nulls_last = "CASE WHEN r.due_date IS NULL THEN 1 ELSE 0 END, r.due_date"
        # Apple's priority values are not ordinal: 1 high, 5 medium, 9 low,
        # 0 none. Sorting numerically would put "none" first.
        priority_rank = "CASE r.priority WHEN 1 THEN 0 WHEN 5 THEN 1 WHEN 9 THEN 2 ELSE 3 END"

        if scope == "completed" and sort in (None, "", "manual"):
            # Most recently ticked off first; that is the only useful order here.
            return "r.completed_date DESC NULLS LAST, r.modified DESC"

        return {
            "title": "r.title COLLATE NOCASE, " + due_nulls_last,
            "title_desc": "r.title COLLATE NOCASE DESC",
            "due": due_nulls_last + ", r.title COLLATE NOCASE",
            "due_desc": "CASE WHEN r.due_date IS NULL THEN 1 ELSE 0 END, r.due_date DESC",
            "priority": priority_rank + ", " + due_nulls_last,
            "created": "r.created DESC",
            "created_asc": "r.created",
        }.get(
            sort or "manual",
            # Default: incomplete first, then soonest due, then alphabetical.
            "r.completed, " + due_nulls_last + ", r.title COLLATE NOCASE",
        )

    @staticmethod
    def _day_bounds(now: Optional[datetime] = None) -> tuple[str, str]:
        """
        Start of today and start of tomorrow, as UTC ISO strings.

        Computed from the machine's local calendar day, not UTC's -- "today"
        means the user's today. The sidecar runs on their machine, so local
        time is the right reference.
        """
        local_now = (now or utcnow()).astimezone()
        start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return to_iso(start), to_iso(end)

    def reminders(
        self,
        list_id: Optional[str] = None,
        tag: Optional[str] = None,
        scope: Optional[str] = None,
        include_completed: bool = False,
        search: Optional[str] = None,
        sort: Optional[str] = None,
        limit: int = 1000,
        now: Optional[datetime] = None,
    ) -> list[dict]:
        """
        Query the cache.

        `scope` selects a smart list: today, upcoming, completed, deleted, all.
        Anything else (including None) is the plain view, optionally narrowed by
        list or tag.
        """
        sql = [
            "SELECT r.* FROM reminders r",
        ]
        args: list[Any] = []
        if tag:
            sql.append("JOIN tags t ON t.reminder_id = r.id AND t.name = ?")
            args.append(tag)

        # Deleted is the only scope that looks at soft-deleted rows.
        sql.append("WHERE r.deleted = %d" % (1 if scope == "deleted" else 0))

        if scope == "today":
            # Apple's Today includes anything already overdue, not just today's.
            _start, end = self._day_bounds(now)
            sql.append("AND r.completed = 0 AND r.due_date IS NOT NULL AND r.due_date < ?")
            args.append(end)
        elif scope == "upcoming":
            _start, end = self._day_bounds(now)
            sql.append("AND r.completed = 0 AND r.due_date IS NOT NULL AND r.due_date >= ?")
            args.append(end)
        elif scope == "completed":
            sql.append("AND r.completed = 1")
        elif scope == "deleted":
            pass  # the deleted flag above is the whole filter
        else:
            if not include_completed:
                sql.append("AND r.completed = 0")

        if list_id:
            sql.append("AND r.list_id = ?")
            args.append(list_id)
        if search:
            sql.append("AND (r.title LIKE ? OR r.description LIKE ?)")
            like = f"%{search}%"
            args += [like, like]
        sql.append("ORDER BY " + self._order_clause(sort, scope))
        sql.append("LIMIT ?")
        # Completed is capped hard: the full history can be thousands of rows and
        # nobody scrolls it. Fifty most-recent is what the view is for.
        if scope == "completed":
            limit = min(limit, COMPLETED_LIMIT)
        args.append(limit)

        with self._lock:
            rows = self._conn.execute(" ".join(sql), args).fetchall()
        out = [dict(r) for r in rows]
        self._attach_tags(out)
        return out

    def reminder(self, reminder_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
            ).fetchone()
        if not row:
            return None
        out = [dict(row)]
        self._attach_tags(out)
        return out[0]

    def _attach_tags(self, rows: list[dict]) -> None:
        if not rows:
            return
        ids = [r["id"] for r in rows]
        marks = ",".join("?" * len(ids))
        with self._lock:
            trows = self._conn.execute(
                f"SELECT reminder_id, name FROM tags WHERE reminder_id IN ({marks})",
                ids,
            ).fetchall()
        by_id: dict[str, list[str]] = {}
        for t in trows:
            by_id.setdefault(t["reminder_id"], []).append(t["name"])
        for r in rows:
            r["tags"] = sorted(by_id.get(r["id"], []))

    def apply_local_edit(self, reminder_id: str, fields: dict) -> None:
        """Write a local edit straight to the cache so the UI updates now."""
        allowed = {
            "title",
            "description",
            "due_date",
            "priority",
            "completed",
            "flagged",
            "list_id",
            "deleted",
            # Without this the optimistic write drops it and the toggle looks
            # broken until the next sync brings the truth back.
            "all_day",
        }
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        assigns = ", ".join(f"{k} = ?" for k in sets)
        args = list(sets.values()) + [to_iso(utcnow()), reminder_id]
        with self._lock:
            self._conn.execute(
                f"UPDATE reminders SET {assigns}, modified = ?, dirty = 1 WHERE id = ?",
                args,
            )
            self._conn.commit()

    def insert_local_reminder(self, r: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO reminders("
                " id,list_id,title,description,due_date,priority,completed,"
                " flagged,all_day,deleted,created,modified,change_tag,notified,dirty"
                ") VALUES(?,?,?,?,?,?,?,?,?,0,?,?,NULL,0,1)",
                (
                    r["id"],
                    r["list_id"],
                    r.get("title") or "",
                    r.get("description") or "",
                    r.get("due_date"),
                    int(r.get("priority") or 0),
                    1 if r.get("completed") else 0,
                    1 if r.get("flagged") else 0,
                    1 if r.get("all_day") else 0,
                    to_iso(utcnow()),
                    to_iso(utcnow()),
                ),
            )
            self._conn.commit()

    def clear_dirty(self, reminder_id: str, change_tag: Optional[str]) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE reminders SET dirty = 0, change_tag = ? WHERE id = ?",
                (change_tag, reminder_id),
            )
            self._conn.commit()

    def replace_id(self, old_id: str, new_id: str) -> None:
        """Server assigns the real record name; swap the optimistic local one."""
        with self._lock:
            self._conn.execute(
                "UPDATE reminders SET id = ? WHERE id = ?", (new_id, old_id)
            )
            self._conn.execute(
                "UPDATE tags SET reminder_id = ? WHERE reminder_id = ?",
                (new_id, old_id),
            )
            self._conn.execute(
                "UPDATE outbox SET reminder_id = ? WHERE reminder_id = ?",
                (new_id, old_id),
            )
            self._conn.commit()

    # ------------------------------------------------------------------ tags
    def replace_tags_for(self, reminder_id: str, tags: Iterable[dict]) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM tags WHERE reminder_id = ?", (reminder_id,))
            self._conn.executemany(
                "INSERT OR REPLACE INTO tags(id,name,reminder_id) VALUES(?,?,?)",
                [(t["id"], t["name"], reminder_id) for t in tags],
            )
            self._conn.commit()

    def smart_counts(self, now: Optional[datetime] = None) -> dict:
        """Badge counts for the smart lists, in one pass each."""
        _start, end = self._day_bounds(now)
        with self._lock:
            q = lambda sql, a=(): self._conn.execute(sql, a).fetchone()[0]  # noqa: E731
            return {
                "today": q(
                    "SELECT COUNT(*) FROM reminders WHERE deleted=0 AND completed=0 "
                    "AND due_date IS NOT NULL AND due_date < ?", (end,)
                ),
                "upcoming": q(
                    "SELECT COUNT(*) FROM reminders WHERE deleted=0 AND completed=0 "
                    "AND due_date IS NOT NULL AND due_date >= ?", (end,)
                ),
                "completed": q(
                    "SELECT COUNT(*) FROM reminders WHERE deleted=0 AND completed=1"
                ),
                "deleted": q("SELECT COUNT(*) FROM reminders WHERE deleted=1"),
                "all": q(
                    "SELECT COUNT(*) FROM reminders WHERE deleted=0 AND completed=0"
                ),
            }

    # -------------------------------------------------------------- settings
    def get_settings(self) -> dict:
        """User preferences, with defaults filled in for anything unset."""
        defaults = {
            "theme": "system",              # system | light | dark
            "sync_minutes": 10,
            "notifications_enabled": True,
            "stale_after_minutes": 60,
            "max_individual_toasts": 3,
            "default_list_id": None,
            "search_scope": "list",         # list | global
            "onboarded": False,
            "print_group_by": "due",        # due | priority | none
            "print_include_notes": True,
            "print_include_completed": False,
            # Keep the Apple ID password in Windows Credential Manager so the
            # session can be rebuilt without a prompt when iCloud expires it.
            "remember_password": True,
            # Sort mode per list/tag/smart view, keyed "list:<id>" and friends.
            # Must be declared here: set_settings drops keys it has never seen,
            # so an undeclared setting silently fails to save.
            "sort_by": {},
        }
        raw = self.get_meta("settings")
        if raw:
            try:
                defaults.update(json.loads(raw))
            except ValueError:
                pass
        return defaults

    def set_settings(self, patch: dict) -> dict:
        merged = self.get_settings()
        merged.update({k: v for k, v in (patch or {}).items() if k in merged})
        self.set_meta("settings", json.dumps(merged))
        return merged

    def all_tags(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, COUNT(*) AS n FROM tags t "
                "JOIN reminders r ON r.id = t.reminder_id "
                "WHERE r.deleted = 0 AND r.completed = 0 "
                "GROUP BY name ORDER BY name"
            ).fetchall()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------- notifier
    def due_unnotified(self, now: datetime, horizon_seconds: int = 0) -> list[dict]:
        """Reminders whose due time has arrived and that we haven't toasted."""
        cutoff = to_iso(now)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM reminders "
                "WHERE deleted = 0 AND completed = 0 AND notified = 0 "
                "  AND due_date IS NOT NULL AND due_date <= ? "
                "ORDER BY due_date",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_notified(self, ids: Iterable[str]) -> None:
        ids = list(ids)
        if not ids:
            return
        marks = ",".join("?" * len(ids))
        with self._lock:
            self._conn.execute(
                f"UPDATE reminders SET notified = 1 WHERE id IN ({marks})", ids
            )
            self._conn.commit()

    # ---------------------------------------------------------------- outbox
    def enqueue(
        self, reminder_id: str, op: str, payload: dict, base_tag: Optional[str]
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO outbox(reminder_id,op,payload,base_tag,created_at) "
                "VALUES(?,?,?,?,?)",
                (reminder_id, op, json.dumps(payload), base_tag, to_iso(utcnow())),
            )
            self._conn.commit()

    def pending(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM outbox ORDER BY seq LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            out.append(d)
        return out

    def dequeue(self, seq: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM outbox WHERE seq = ?", (seq,))
            self._conn.commit()

    def record_failure(self, seq: int, error: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE outbox SET attempts = attempts + 1, last_error = ? WHERE seq = ?",
                (error, seq),
            )
            self._conn.commit()

    # ------------------------------------------------------------- conflicts
    def record_conflict(self, reminder_id: str, local: dict, remote: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO conflicts(reminder_id,local_json,remote_json,detected_at) "
                "VALUES(?,?,?,?)",
                (
                    reminder_id,
                    json.dumps(local, default=str),
                    json.dumps(remote, default=str),
                    to_iso(utcnow()),
                ),
            )
            self._conn.commit()

    def conflicts(self, unresolved_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM conflicts"
        if unresolved_only:
            sql += " WHERE resolved = 0"
        sql += " ORDER BY detected_at DESC"
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["local"] = json.loads(d.pop("local_json"))
            d["remote"] = json.loads(d.pop("remote_json"))
            out.append(d)
        return out

    def resolve_conflict(self, conflict_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE conflicts SET resolved = 1 WHERE id = ?", (conflict_id,)
            )
            self._conn.commit()
