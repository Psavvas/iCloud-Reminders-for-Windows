"""
Sync and protocol tests against a fake iCloud client.

The fake mimics the behaviours Phase 1 actually observed, not an idealized API:
colour arrives as a JSON blob, deletes are soft, and a record's change tag moves
when the "phone" edits it.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from reminders_sidecar.db import Cache, to_iso, utcnow
from reminders_sidecar.icloud import ConflictError, NetworkError, parse_color
from reminders_sidecar.sync import CURSOR_KEY, SyncEngine


# --------------------------------------------------------------- colour ----
def test_parse_color_reads_the_json_blob_apple_stores():
    raw = json.dumps(
        {
            "daSymbolicColorName": "custom",
            "daHexString": "#5AC8FA",
            "alpha": 1,
            "red": 0.35294117647058826,
            "green": 0.78431372549019607,
            "blue": 0.98039215686274506,
            "ckSymbolicColorName": "lightBlue",
        }
    )
    assert parse_color(raw) == "#5AC8FA"
    assert parse_color(json.loads(raw)) == "#5AC8FA"


def test_parse_color_falls_back_to_rgb_when_hex_missing():
    assert parse_color({"red": 1, "green": 0, "blue": 0}) == "#FF0000"


def test_parse_color_handles_group_rows_and_junk():
    # Group rows genuinely have no colour.
    assert parse_color(None) is None
    assert parse_color("not json") is None
    assert parse_color("#ABCDEF") == "#ABCDEF"


# ----------------------------------------------------------- fake client ---
class FakeClient:
    def __init__(self):
        self.lists_data = [
            {"id": "List/A", "title": "Inbox", "color_hex": "#CC73E1", "count": 0},
            {"id": "List/G", "title": "School", "color_hex": None, "is_group": True},
        ]
        self.reminders_data = {"List/A": []}
        self.tags_data = {}
        self.cursor = "c0"
        self.changes: list[dict] = []
        self.created: list[dict] = []
        self.updates: list[tuple] = []
        self.deleted: list[str] = []
        self.remote_tags = {}
        self.fail_with = None
        self.connected = True

    def lists(self):
        return list(self.lists_data)

    # Part of the ICloudClient interface the sync engine leans on when a
    # session expires mid-pass. The default fake has nothing to restore from.
    def restore(self):
        return False

    def invalidate(self):
        self.connected = False

    def status(self):
        return {"authenticated": self.connected, "apple_id": "a@b.c"}

    def reminders_for(self, list_id):
        return list(self.reminders_data.get(list_id, [])), dict(
            self.tags_data.get(list_id, {})
        )

    def sync_cursor(self):
        return self.cursor

    def changes_since(self, cursor):
        return list(self.changes)

    def create(self, payload):
        rec = dict(payload)
        rec["id"] = f"Reminder/server-{len(self.created)}"
        rec["change_tag"] = "s1"
        self.created.append(rec)
        return rec

    def update(self, rid, payload, base_tag):
        if self.fail_with:
            raise self.fail_with
        self.updates.append((rid, payload, base_tag))
        return {"id": rid, "list_id": "List/A", "title": payload.get("title", ""),
                "change_tag": "s2"}

    def delete(self, rid):
        self.deleted.append(rid)

    def _service(self):
        raise RuntimeError("not needed")

    def tags_for(self, rem):
        return []


@pytest.fixture
def rig(tmp_path):
    cache = Cache(tmp_path / "s.db")
    client = FakeClient()
    events = []
    engine = SyncEngine(cache, client, emit=lambda e, d: events.append((e, d)))
    yield cache, client, engine, events
    cache.close()


# ------------------------------------------------------------- full sync ---
def test_full_sync_populates_cache_and_skips_groups(rig):
    cache, client, engine, events = rig
    client.reminders_data["List/A"] = [
        {"id": "Reminder/1", "list_id": "List/A", "title": "a", "change_tag": "t"},
        {"id": "Reminder/2", "list_id": "List/A", "title": "b", "change_tag": "t"},
    ]
    result = engine.full_sync()
    assert result["reminders"] == 2
    assert len(cache.lists()) == 2
    assert len(cache.reminders()) == 2
    # The group list is never fetched for reminders.
    assert "List/G" not in client.reminders_data
    assert cache.get_meta(CURSOR_KEY) == "c0"
    assert ("sync_finished", {"mode": "full", "reminders": 2}) in events


def test_full_sync_reports_progress(rig):
    _cache, client, engine, events = rig
    client.reminders_data["List/A"] = [
        {"id": "Reminder/1", "list_id": "List/A", "title": "a", "change_tag": "t"}
    ]
    engine.full_sync()
    stages = [d.get("stage") for e, d in events if e == "sync_progress"]
    assert "lists" in stages and "reminders" in stages


def test_progress_is_weighted_by_list_size_not_list_count(rig):
    """
    The bar has to move at the rate work is done. On a real account one list
    holds 1,219 of 2,900 reminders, so a percentage counting lists would sit at
    50% through the small one and then stall for the whole long download.
    """
    _cache, client, engine, events = rig
    client.lists_data = [
        {"id": "List/S", "title": "Small", "count": 10},
        {"id": "List/B", "title": "Big", "count": 990},
    ]
    client.reminders_data = {"List/S": [], "List/B": []}

    engine.full_sync()
    pcts = [d["percent"] for e, d in events if e == "sync_progress" and "percent" in d]

    assert pcts[0] == 0.0
    assert pcts[-1] == 100.0
    assert pcts == sorted(pcts)  # never goes backwards
    # One list of two done, but only 1% of the reminders.
    assert pcts[1] == pytest.approx(1.0, abs=0.1)


def test_progress_survives_lists_that_report_no_count(rig):
    """A zero-weight list would make the total zero and the percentage a
    division by zero, so every list counts for at least one unit."""
    _cache, client, engine, events = rig
    client.lists_data = [{"id": "List/A", "title": "Inbox", "count": 0}]
    client.reminders_data = {"List/A": []}

    engine.full_sync()
    pcts = [d["percent"] for e, d in events if e == "sync_progress" and "percent" in d]
    assert pcts[-1] == 100.0


def test_a_delta_sync_declares_itself_indeterminate(rig):
    """Its size isn't knowable up front, so the bar must not claim a figure."""
    _cache, client, engine, events = rig
    engine.full_sync()
    events.clear()
    engine.delta_sync()

    started = [d for e, d in events if e == "sync_started"]
    assert started == [{"mode": "delta", "determinate": False}]
    assert all(
        "percent" not in d for e, d in events if e == "sync_progress"
    )


# ------------------------------------------------------------ delta sync ---
def test_delta_sync_applies_updates_and_deletes(rig):
    cache, client, engine, _ = rig
    engine.full_sync()
    client.changes = [
        {"id": "Reminder/new", "list_id": "List/A", "title": "fresh", "change_tag": "t"},
        {"id": "Reminder/old", "deleted": True},
    ]
    cache.upsert_reminders(
        [{"id": "Reminder/old", "list_id": "List/A", "title": "doomed"}]
    )
    client.cursor = "c1"
    out = engine.delta_sync()
    assert out == {"mode": "delta", "updated": 1, "deleted": 1}
    assert cache.reminder("Reminder/new")["title"] == "fresh"
    assert cache.reminder("Reminder/old")["deleted"] == 1
    assert cache.get_meta(CURSOR_KEY) == "c1"


def test_delta_sync_always_refreshes_lists(rig):
    """List changes never come through the delta stream, so they're re-read."""
    cache, client, engine, _ = rig
    engine.full_sync()
    client.lists_data.append(
        {"id": "List/NEW", "title": "Added on phone", "color_hex": "#FF0000"}
    )
    engine.delta_sync()
    assert any(l["title"] == "Added on phone" for l in cache.lists())


def test_delta_sync_falls_back_to_full_when_no_cursor(rig):
    cache, client, engine, _ = rig
    client.reminders_data["List/A"] = [
        {"id": "Reminder/1", "list_id": "List/A", "title": "a", "change_tag": "t"}
    ]
    out = engine.delta_sync()
    assert out["mode"] == "full"


# ---------------------------------------------------------------- outbox ---
def test_outbox_create_swaps_local_id_for_server_id(rig):
    cache, client, engine, _ = rig
    row = {"id": "local/1", "list_id": "List/A", "title": "new thing"}
    cache.insert_local_reminder(row)
    cache.enqueue("local/1", "create", row, None)
    out = engine.flush_outbox()
    assert out["pushed"] == 1
    assert cache.reminder("local/1") is None
    assert cache.reminder("Reminder/server-0") is not None
    assert cache.reminder("Reminder/server-0")["dirty"] == 0


def test_outbox_update_clears_dirty(rig):
    cache, client, engine, _ = rig
    cache.upsert_reminders(
        [{"id": "Reminder/1", "list_id": "List/A", "title": "x", "change_tag": "t1"}]
    )
    cache.apply_local_edit("Reminder/1", {"title": "mine"})
    cache.enqueue("Reminder/1", "update", {"title": "mine"}, "t1")
    engine.flush_outbox()
    assert client.updates[0][2] == "t1"
    assert cache.reminder("Reminder/1")["dirty"] == 0
    assert cache.pending() == []


def test_conflict_is_recorded_and_server_copy_wins_in_cache(rig):
    """The phone edited the same reminder; we must not clobber it."""
    cache, client, engine, events = rig
    cache.upsert_reminders(
        [{"id": "Reminder/1", "list_id": "List/A", "title": "orig", "change_tag": "t1"}]
    )
    cache.apply_local_edit("Reminder/1", {"title": "my version"})
    cache.enqueue("Reminder/1", "update", {"title": "my version"}, "t1")

    remote = {"id": "Reminder/1", "list_id": "List/A", "title": "phone version",
              "change_tag": "t2"}
    client.fail_with = ConflictError("changed elsewhere", json.dumps(remote))

    out = engine.flush_outbox()
    assert out["conflicts"] == 1

    conflicts = cache.conflicts()
    assert len(conflicts) == 1
    assert conflicts[0]["local"]["title"] == "my version"
    assert conflicts[0]["remote"]["title"] == "phone version"
    # Cache reflects the server, and our edit is preserved in the conflict row.
    assert cache.reminder("Reminder/1")["title"] == "phone version"
    assert cache.pending() == []
    assert any(e == "conflict" for e, _ in events)


def _queue_three(cache):
    for i in range(3):
        rid = f"Reminder/{i}"
        cache.upsert_reminders([{"id": rid, "list_id": "List/A", "title": "x"}])
        cache.enqueue(rid, "update", {"title": "y"}, None)


def test_auth_failure_stops_draining_the_queue(rig):
    """
    One expired session shouldn't burn through every queued edit.

    It surfaces rather than being counted as a failure and swallowed: an
    expired session is recoverable, and the caller can only retry something it
    is told about.
    """
    cache, client, engine, _ = rig
    from reminders_sidecar.icloud import AuthRequired

    _queue_three(cache)
    client.fail_with = AuthRequired("session expired")

    with pytest.raises(AuthRequired):
        engine.flush_outbox()
    assert len(cache.pending()) == 3  # nothing dropped
    assert cache.pending()[0]["attempts"] == 1  # only the first was tried


def test_a_push_recovers_from_an_expired_session(rig):
    """The queued edits go through on the retry, without the user doing anything."""
    cache, client, engine, _ = rig
    from reminders_sidecar.icloud import AuthRequired

    _queue_three(cache)
    client.fail_with = AuthRequired("session expired")

    def restore():
        client.fail_with = None
        return True

    client.restore = restore  # type: ignore[assignment]

    out = engine.push_now()
    assert out["pushed"] == 3
    assert cache.pending() == []


def test_terms_acceptance_is_not_retried(rig):
    """A restore cannot fix this one, so it must not loop -- the user has to act."""
    cache, client, engine, _ = rig
    from reminders_sidecar.icloud import TermsRequired

    _queue_three(cache)
    client.fail_with = TermsRequired("accept the terms")
    client.restore = lambda: True  # type: ignore[assignment]

    out = engine.push_now()
    assert out["failed"] == 1
    assert len(cache.pending()) == 3


def test_transient_failure_keeps_item_queued_for_retry(rig):
    cache, client, engine, _ = rig
    cache.upsert_reminders([{"id": "Reminder/1", "list_id": "List/A", "title": "x"}])
    cache.enqueue("Reminder/1", "update", {"title": "y"}, None)
    client.fail_with = NetworkError("offline")
    engine.flush_outbox()
    pending = cache.pending()
    assert len(pending) == 1
    assert pending[0]["attempts"] == 1
    assert "offline" in pending[0]["last_error"]


def test_sync_now_pushes_before_pulling(rig):
    """Pulling first would overwrite the very edit we're about to push."""
    cache, client, engine, _ = rig
    order = []
    orig_update, orig_lists = client.update, client.lists
    client.update = lambda *a, **k: (order.append("push"), orig_update(*a, **k))[1]
    client.lists = lambda: (order.append("pull"), orig_lists())[1]

    cache.upsert_reminders([{"id": "Reminder/1", "list_id": "List/A", "title": "x"}])
    cache.enqueue("Reminder/1", "update", {"title": "y"}, None)
    engine.sync_now(full=True)
    assert order[0] == "push"


def test_concurrent_flushes_do_not_double_push(rig):
    """Every write kicks a push; overlapping drains must not push twice."""
    import threading

    cache, client, engine, _ = rig
    cache.upsert_reminders([{"id": "Reminder/1", "list_id": "List/A", "title": "x"}])
    cache.enqueue("Reminder/1", "update", {"title": "y"}, None)

    barrier = threading.Barrier(2, timeout=5)
    orig = client.update

    def slow_update(*a, **k):
        barrier.wait()  # hold the first drain inside the push
        return orig(*a, **k)

    client.update = slow_update
    t = threading.Thread(target=engine.flush_outbox)
    t.start()
    barrier.wait()
    second = engine.flush_outbox()  # runs while the first is mid-push
    t.join(timeout=5)

    assert second.get("skipped") is True
    assert len(client.updates) == 1
