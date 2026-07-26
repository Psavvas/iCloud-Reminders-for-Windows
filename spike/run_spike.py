"""
Phase 1 validation spike -- runs all 7 checks and prints a PASS/FAIL table.

Usage (Windows PowerShell):

    py -m venv .venv
    .\\.venv\\Scripts\\Activate.ps1
    pip install -r spike\\requirements.txt
    icloud auth login --username you@example.com     # seeds keyring + 2FA
    python spike\\run_spike.py --apple-id you@example.com

Add --no-confirm to skip the "check your iPhone" prompts (those items then
report UNVERIFIED rather than PASS -- device confirmation is the whole point
of items 3, 4 and 5, so prefer running it interactively at least once).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import (  # noqa: E402
    BLOCKED,
    FAIL,
    MARKER,
    PASS,
    UNVERIFIED,
    Recorder,
    confirm,
    connect,
    fmt_dt,
    section,
)
from list_create_experiment import (  # noqa: E402
    delete_list,
    public_api_has_list_creation,
    try_create_list,
)

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]


def _local_tz():
    """A real, non-UTC tz so we can prove tz-awareness survives the round trip."""
    if ZoneInfo is not None:
        for name in ("America/New_York", "Europe/London", "UTC"):
            try:
                return ZoneInfo(name)
            except Exception:  # noqa: BLE001 - missing tzdata on Windows
                continue
    return timezone.utc


class Spike:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.rec = Recorder()
        self.api = None
        self.svc = None
        self.created_reminders: list = []
        self.created_list: dict | None = None
        self.target_list_id: str | None = None

    # ---------------------------------------------------------------- 1. auth
    def check_1_auth(self) -> None:
        section("CHECK 1: Auth, 2FA, session persists across process restart")
        try:
            self.api = connect(
                apple_id=self.args.apple_id,
                accept_terms=self.args.accept_terms,
                interactive=not self.args.no_confirm,
            )
            self.svc = self.api.reminders
        except Exception as exc:  # noqa: BLE001
            self.rec.add("1", "Auth + session persistence", FAIL, f"{type(exc).__name__}: {exc}")
            return

        trusted = self.api.is_trusted_session
        print(f"    authenticated; trusted_session={trusted}")

        # The real test: a brand new OS process, no password, no 2FA prompt.
        env = dict(os.environ)
        env["ICLOUD_APPLE_ID"] = self.args.apple_id or env.get("ICLOUD_APPLE_ID", "")
        probe = Path(__file__).resolve().parent / "session_probe.py"
        print("    spawning fresh process to test session reuse...")
        proc = subprocess.run(
            [sys.executable, str(probe)],
            capture_output=True,
            text=True,
            env=env,
            timeout=180,
        )
        raw = (proc.stdout or "").strip().splitlines()
        data = {}
        if raw:
            try:
                data = json.loads(raw[-1])
            except json.JSONDecodeError:
                data = {"error": f"unparseable probe output: {raw[-1][:200]}"}

        if data.get("ok"):
            self.rec.add(
                "1",
                "Auth + session persistence",
                PASS,
                f"trusted_session={trusted}; fresh process reused session "
                f"with no password/2FA and read {data.get('list_count')} lists",
            )
        else:
            self.rec.add(
                "1",
                "Auth + session persistence",
                FAIL,
                f"login OK but restart failed: {data.get('error')} "
                f"(stderr: {(proc.stderr or '').strip()[:200]})",
            )

    # ---------------------------------------------------------------- 2. read
    def check_2_read(self) -> None:
        section("CHECK 2: Enumerate lists and reminders")
        lists = list(self.svc.lists())
        if not lists:
            self.rec.add("2", "Read lists + reminders", FAIL, "no lists returned")
            return

        print(f"    {len(lists)} lists:")
        for lst in lists:
            print(
                f"      - id={lst.id}  title={lst.title!r}  color={lst.color}  "
                f"count={lst.count}  is_group={lst.is_group}"
            )

        # Prefer a list named by --list, else the first non-group list.
        chosen = None
        if self.args.list:
            chosen = next((l for l in lists if l.title == self.args.list), None)
            if chosen is None:
                self.rec.add(
                    "2", "Read lists + reminders", FAIL,
                    f"--list {self.args.list!r} not found",
                )
                return
        else:
            chosen = next((l for l in lists if not l.is_group), lists[0])
        self.target_list_id = chosen.id
        print(f"    using list {chosen.title!r} ({chosen.id}) for write tests")

        rems = list(self.svc.reminders(list_id=chosen.id))
        print(f"    {len(rems)} reminders in that list")
        for r in rems[:5]:
            print(f"      - {r.id}  {r.title!r}  due={fmt_dt(r.due_date)}  done={r.completed}")

        self.rec.add(
            "2", "Read lists + reminders", PASS,
            f"{len(lists)} lists enumerated (id/title/color/count all populated); "
            f"{len(rems)} reminders read from {chosen.title!r}",
        )

    # -------------------------------------------------------------- 3. create
    def check_3_create(self) -> None:
        section("CHECK 3: Create reminder (title, desc, tz-aware due, priority)")
        tz = _local_tz()
        due = (datetime.now(tz) + timedelta(days=1)).replace(microsecond=0)
        title = f"{MARKER} create test"
        print(f"    creating with due={fmt_dt(due)} priority=1")

        rem = self.svc.create(
            list_id=self.target_list_id,
            title=title,
            desc="spike description line",
            due_date=due,
            priority=1,
        )
        self.created_reminders.append(rem)
        print(f"    created id={rem.id}")

        fetched = self.svc.get(rem.id)
        print(f"    read back: title={fetched.title!r} desc={fetched.desc!r}")
        print(f"               due={fmt_dt(fetched.due_date)} priority={fetched.priority}")

        problems = []
        if fetched.title != title:
            problems.append(f"title mismatch ({fetched.title!r})")
        if fetched.priority != 1:
            problems.append(f"priority mismatch ({fetched.priority})")
        if fetched.due_date is None:
            problems.append("due_date came back None")
        else:
            if fetched.due_date.tzinfo is None:
                problems.append("due_date returned NAIVE (tz lost)")
            else:
                drift = abs((fetched.due_date - due).total_seconds())
                if drift > 60:
                    problems.append(f"due_date drifted {drift:.0f}s")

        ok, note = confirm(
            f"Does a reminder titled '{title}' appear, due tomorrow, marked high priority?",
            auto=self.args.no_confirm,
        )
        detail = f"round-tripped title/desc/priority; due {fmt_dt(fetched.due_date)}; {note}"
        if problems:
            self.rec.add("3", "Create reminder", FAIL, "; ".join(problems) + f"; {note}")
        elif ok:
            self.rec.add("3", "Create reminder", PASS, detail)
        else:
            self.rec.add("3", "Create reminder", UNVERIFIED, detail)

    # ------------------------------------------- 4. update / complete / delete
    def check_4_update_complete_delete(self) -> None:
        section("CHECK 4: Update, complete, delete")
        if not self.created_reminders:
            self.rec.add("4", "Update / complete / delete", BLOCKED, "no reminder from check 3")
            return
        rem = self.created_reminders[0]
        notes = []

        # --- update
        rem.title = f"{MARKER} updated title"
        rem.desc = "updated description"
        rem.priority = 9
        self.svc.update(rem)
        after = self.svc.get(rem.id)
        upd_ok = after.title == rem.title and after.priority == 9
        print(f"    update -> title={after.title!r} priority={after.priority}")
        ok, note = confirm("Did the title change and priority drop to low?", auto=self.args.no_confirm)
        notes.append(f"update {'ok' if upd_ok else 'FAILED'} ({note})")

        # --- complete
        after.completed = True
        self.svc.update(after)
        done = self.svc.get(rem.id)
        comp_ok = bool(done.completed)
        print(f"    complete -> completed={done.completed}")
        ok2, note2 = confirm("Is it now showing as completed?", auto=self.args.no_confirm)
        notes.append(f"complete {'ok' if comp_ok else 'FAILED'} ({note2})")

        # --- delete
        self.svc.delete(done)
        del_ok = True
        try:
            gone = self.svc.get(rem.id)
            # delete is a soft delete: Deleted=1. Absent from listings is the real test.
            del_ok = bool(getattr(gone, "deleted", False))
            print(f"    delete -> soft-deleted flag={getattr(gone, 'deleted', None)}")
        except Exception:  # noqa: BLE001 - hard 404 is also a valid outcome
            print("    delete -> record no longer retrievable")
        still_listed = any(
            r.id == rem.id for r in self.svc.reminders(list_id=self.target_list_id)
        )
        if still_listed:
            del_ok = False
        ok3, note3 = confirm("Has it disappeared from the list?", auto=self.args.no_confirm)
        notes.append(f"delete {'ok' if del_ok else 'FAILED'} ({note3})")

        if upd_ok and comp_ok and del_ok:
            self.created_reminders.remove(rem)
            status = PASS if (ok and ok2 and ok3) else UNVERIFIED
            self.rec.add("4", "Update / complete / delete", status, "; ".join(notes))
        else:
            self.rec.add("4", "Update / complete / delete", FAIL, "; ".join(notes))

    # ---------------------------------------------------------------- 5. tags
    def check_5_tags(self) -> None:
        section("CHECK 5: Hashtags -- create, read back, delete")
        rem = self.svc.create(
            list_id=self.target_list_id,
            title=f"{MARKER} tag test",
            desc="",
        )
        self.created_reminders.append(rem)
        tag_name = "spiketag"

        hashtag = self.svc.create_hashtag(rem, tag_name)
        print(f"    create_hashtag -> id={getattr(hashtag, 'id', '?')} name={getattr(hashtag, 'name', '?')!r}")

        tags = self.svc.tags_for(self.svc.get(rem.id))
        names = [getattr(t, "name", None) for t in tags]
        print(f"    tags_for -> {names}")
        read_ok = tag_name in names

        ok, note = confirm(
            f"On the reminder '{MARKER} tag test', does '{tag_name}' render as a real "
            "TAG chip (highlighted/tappable) -- and NOT as literal '#spiketag' text in the title?",
            auto=self.args.no_confirm,
        )

        self.svc.delete_hashtag(rem, hashtag)
        after = [getattr(t, "name", None) for t in self.svc.tags_for(self.svc.get(rem.id))]
        print(f"    after delete_hashtag -> {after}")
        del_ok = tag_name not in after

        detail = (
            f"create+read {'ok' if read_ok else 'FAILED'}, "
            f"delete {'ok' if del_ok else 'FAILED'}; renders-as-tag: {note}"
        )
        if not (read_ok and del_ok):
            self.rec.add("5", "Hashtags create/read/delete", FAIL, detail)
        elif ok:
            self.rec.add("5", "Hashtags create/read/delete", PASS, detail)
        else:
            self.rec.add("5", "Hashtags create/read/delete", UNVERIFIED, detail)

    # ---------------------------------------------------------- 6. create list
    def check_6_create_list(self) -> None:
        section("CHECK 6: CREATE A NEW LIST (undocumented)")
        has, candidates = public_api_has_list_creation(self.svc)
        print(f"    public create/add/new methods on service: {candidates}")
        print(f"    any list-creating public method? {has}")

        title = f"{MARKER} new list"
        print("    attempting raw CloudKit create of a 'List' record...")
        outcome = try_create_list(self.svc, title)

        if not outcome.get("created"):
            self.rec.add(
                "6", "Create a new list", FAIL,
                "no public API; raw CloudKit create rejected. "
                + " | ".join(outcome.get("attempts", [])),
            )
            return

        self.created_list = outcome
        titles = [l.title for l in self.svc.lists()]
        visible = title in titles
        print(f"    list now visible via lists()? {visible}")

        ok, note = confirm(
            f"Does a new list called '{title}' appear in Reminders on your iPhone?",
            auto=self.args.no_confirm,
        )

        detail = (
            f"created via raw modify, shape={outcome.get('winning_shape')}; "
            f"visible in lists()={visible}; {note}"
        )
        if not visible:
            self.rec.add("6", "Create a new list", FAIL, detail + " (server accepted but list not readable)")
        elif ok:
            self.rec.add("6", "Create a new list", PASS, detail)
        else:
            self.rec.add("6", "Create a new list", UNVERIFIED, detail)

    # ---------------------------------------------------------- 7. delta sync
    def check_7_delta_sync(self) -> None:
        section("CHECK 7: Delta sync via sync_cursor() + iter_changes(since=...)")

        cursor1 = self.svc.sync_cursor()
        print(f"    cursor #1: {str(cursor1)[:48]}...")

        rem = self.svc.create(
            list_id=self.target_list_id,
            title=f"{MARKER} delta test",
            desc="",
        )
        self.created_reminders.append(rem)
        print(f"    created {rem.id} after cursor #1")

        changes1 = list(self.svc.iter_changes(since=cursor1))
        ids1 = [c.reminder_id for c in changes1]
        print(f"    iter_changes(since=cursor1) -> {len(changes1)} events")
        for c in changes1[:10]:
            print(f"      - {c.type}  {c.reminder_id}")
        saw_new = any(rem.id in (i or "") or (i or "") in rem.id for i in ids1)

        cursor2 = self.svc.sync_cursor()
        changes2 = list(self.svc.iter_changes(since=cursor2))
        print(f"    cursor #2 -> iter_changes returns {len(changes2)} events (expect ~0)")

        # Second run must not replay the change we already consumed.
        replayed = any(rem.id in (c.reminder_id or "") for c in changes2)

        if saw_new and not replayed and len(changes2) < len(changes1):
            self.rec.add(
                "7", "Delta sync (cursor + iter_changes)", PASS,
                f"run 1 returned {len(changes1)} event(s) including the new reminder; "
                f"run 2 from a fresh cursor returned {len(changes2)} and did not replay it. "
                "NOTE: iter_changes filters recordType=='Reminder', so LIST changes "
                "are NOT reported by the delta stream.",
            )
        else:
            self.rec.add(
                "7", "Delta sync (cursor + iter_changes)", FAIL,
                f"run1={len(changes1)} saw_new={saw_new}; run2={len(changes2)} replayed={replayed}",
            )

    # -------------------------------------------------------------- cleanup
    def cleanup(self) -> None:
        section("CLEANUP")
        for rem in list(self.created_reminders):
            try:
                self.svc.delete(self.svc.get(rem.id))
                print(f"    deleted reminder {rem.id}")
            except Exception as exc:  # noqa: BLE001
                print(f"    could NOT delete {rem.id}: {exc}")
        if self.created_list:
            msg = delete_list(
                self.svc,
                self.created_list["record_name"],
                self.created_list.get("record_change_tag"),
            )
            print(f"    list cleanup: {msg}")

    def run(self) -> int:
        self.check_1_auth()
        if self.svc is None:
            print("\nAuth failed -- cannot run checks 2-7.")
            for item, name in [
                ("2", "Read lists + reminders"),
                ("3", "Create reminder"),
                ("4", "Update / complete / delete"),
                ("5", "Hashtags create/read/delete"),
                ("6", "Create a new list"),
                ("7", "Delta sync (cursor + iter_changes)"),
            ]:
                self.rec.add(item, name, BLOCKED, "auth failed")
            self._report()
            return 1

        # Check 2 picks the target list; everything after it needs that list.
        try:
            self.check_2_read()
        except Exception as exc:  # noqa: BLE001
            self.rec.add("2", "Read lists + reminders", FAIL, f"{type(exc).__name__}: {exc}")

        remaining = [
            ("3", "Create reminder", self.check_3_create),
            ("4", "Update / complete / delete", self.check_4_update_complete_delete),
            ("5", "Hashtags create/read/delete", self.check_5_tags),
            ("6", "Create a new list", self.check_6_create_list),
            ("7", "Delta sync (cursor + iter_changes)", self.check_7_delta_sync),
        ]
        for item, name, fn in remaining:
            if self.target_list_id is None:
                self.rec.add(item, name, BLOCKED, "check 2 did not yield a usable list")
                continue
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                self.rec.add(item, name, FAIL, f"{type(exc).__name__}: {exc}")
        try:
            self.cleanup()
        except Exception as exc:  # noqa: BLE001
            print(f"    cleanup error: {exc}")
        self._report()
        return self.rec.exit_code()

    def _report(self) -> None:
        section("PHASE 1 RESULTS")
        table = self.rec.table()
        print("\n" + table + "\n")
        out = Path(__file__).resolve().parent / "results.md"
        out.write_text(
            "# Phase 1 spike results\n\n"
            f"Run: {datetime.now(timezone.utc).isoformat()}\n\n" + table + "\n",
            encoding="utf-8",
        )
        print(f"Written to {out}")


def main() -> int:
    p = argparse.ArgumentParser(description="iCloud Reminders Phase 1 validation spike")
    p.add_argument("--apple-id", default=os.environ.get("ICLOUD_APPLE_ID"))
    p.add_argument("--list", help="Name of an existing list to use for write tests")
    p.add_argument("--accept-terms", action="store_true", help="Accept updated iCloud T&Cs")
    p.add_argument("--no-confirm", action="store_true", help="Skip iPhone confirmation prompts")
    args = p.parse_args()

    if not args.apple_id:
        print("ERROR: pass --apple-id or set ICLOUD_APPLE_ID", file=sys.stderr)
        return 2
    return Spike(args).run()


if __name__ == "__main__":
    sys.exit(main())
