"""
Protocol contract tests: the exact JSON envelope the Rust side depends on.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from reminders_sidecar.icloud import AuthRequired
from reminders_sidecar.server import Server
from reminders_sidecar.sync import SyncEngine
from tests.test_sync_and_server import FakeClient


@pytest.fixture
def server(tmp_path):
    s = Server(db_path=str(tmp_path / "p.db"), apple_id="a@b.c", cookie_dir=None)
    s.client = FakeClient()
    s.sync = SyncEngine(s.cache, s.client, emit=s.emit)
    s._methods = s._build_methods()
    written: list[dict] = []
    s._write = written.append  # type: ignore[assignment]
    s.written = written  # type: ignore[attr-defined]
    # These fire background threads. Suppress them so the tests observe the
    # queue deterministically; the push path itself is covered in
    # test_sync_and_server.py.
    s._kick_push = lambda: None  # type: ignore[assignment]
    s._kick_sync = lambda full=False: None  # type: ignore[assignment]
    yield s
    s.cache.close()


def call(server, method, **params):
    fn = server._methods[method]
    return fn(params)


def test_ping(server):
    assert call(server, "ping") == {"pong": True}


def test_create_reminder_is_visible_immediately(server):
    """The UI must not wait on the network to see its own write."""
    out = call(
        server,
        "create_reminder",
        list_id="List/A",
        title="write me",
        priority=1,
    )
    assert out["title"] == "write me"
    assert out["dirty"] == 1
    assert out["id"].startswith("local/")
    assert len(server.cache.pending()) == 1


def test_due_date_accepts_iso_and_epoch_millis(server):
    iso = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc).isoformat()
    a = call(server, "create_reminder", list_id="List/A", title="a", due_date=iso)
    assert a["due_date"] == "2026-08-01T09:00:00+00:00"

    ms = 1785600000000
    b = call(server, "create_reminder", list_id="List/A", title="b", due_date=ms)
    assert b["due_date"].endswith("+00:00")


def test_naive_due_date_is_treated_as_local_not_utc(server):
    """
    Apple silently reads naive datetimes as UTC. A date picker hands us local
    wall-clock time, so we resolve it against the machine's zone instead.
    """
    out = call(
        server, "create_reminder", list_id="List/A", title="x",
        due_date="2026-08-01T09:00:00",
    )
    expected = (
        datetime(2026, 8, 1, 9, 0).astimezone().astimezone(timezone.utc).isoformat()
    )
    assert out["due_date"] == expected


def test_update_queues_with_base_change_tag(server):
    server.cache.upsert_reminders(
        [{"id": "Reminder/1", "list_id": "List/A", "title": "x", "change_tag": "t7"}]
    )
    call(server, "update_reminder", id="Reminder/1", title="edited")
    assert server.cache.reminder("Reminder/1")["title"] == "edited"
    assert server.cache.pending()[0]["base_tag"] == "t7"


def test_delete_marks_deleted_and_queues(server):
    server.cache.upsert_reminders(
        [{"id": "Reminder/1", "list_id": "List/A", "title": "x", "change_tag": "t"}]
    )
    call(server, "delete_reminder", id="Reminder/1")
    assert server.cache.reminder("Reminder/1")["deleted"] == 1
    assert server.cache.pending()[0]["op"] == "delete"


def test_reads_are_served_from_cache_without_touching_the_client(server):
    server.cache.replace_lists([{"id": "List/A", "title": "Inbox"}])
    server.cache.upsert_reminders(
        [{"id": "Reminder/1", "list_id": "List/A", "title": "cached"}]
    )
    server.client = None  # any network access would explode
    assert call(server, "lists")[0]["title"] == "Inbox"
    assert call(server, "reminders", list_id="List/A")[0]["title"] == "cached"


def test_due_notifications_returns_serializable_plan(server):
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    server.cache.upsert_reminders(
        [{"id": "Reminder/1", "list_id": "List/A", "title": "ring", "due_date": past}]
    )
    plan = call(server, "due_notifications")
    json.dumps(plan)  # must survive the wire
    assert plan["toasts"][0]["title"] == "ring"
    assert plan["notified_ids"] == ["Reminder/1"]


def test_sync_status_shape(server):
    st = call(server, "sync_status")
    assert set(st) == {
        "running", "last_sync", "has_cursor", "pending_pushes", "conflicts",
        "sync_minutes",
    }


def test_unknown_method_is_reported_not_raised(server):
    assert "no_such_method" not in server._methods


def test_errors_serialize_with_a_code(server):
    err = AuthRequired("expired", "detail here")
    assert err.to_dict() == {
        "code": "AUTH_REQUIRED", "message": "expired", "detail": "detail here"
    }


def test_resolve_conflict_keeping_local_requeues_the_edit(server):
    server.cache.upsert_reminders(
        [{"id": "Reminder/1", "list_id": "List/A", "title": "theirs", "change_tag": "t"}]
    )
    server.cache.record_conflict(
        "Reminder/1",
        {"id": "Reminder/1", "title": "mine"},
        {"id": "Reminder/1", "title": "theirs"},
    )
    cid = server.cache.conflicts()[0]["id"]
    call(server, "resolve_conflict", id=cid, keep="local")
    assert server.cache.conflicts() == []
    assert server.cache.reminder("Reminder/1")["title"] == "mine"
    assert server.cache.pending()[0]["op"] == "update"


def test_resolve_conflict_keeping_remote_changes_nothing(server):
    server.cache.upsert_reminders(
        [{"id": "Reminder/1", "list_id": "List/A", "title": "theirs", "change_tag": "t"}]
    )
    server.cache.record_conflict(
        "Reminder/1", {"id": "Reminder/1", "title": "mine"}, {"title": "theirs"}
    )
    cid = server.cache.conflicts()[0]["id"]
    call(server, "resolve_conflict", id=cid, keep="remote")
    assert server.cache.conflicts() == []
    assert server.cache.reminder("Reminder/1")["title"] == "theirs"
    assert server.cache.pending() == []
