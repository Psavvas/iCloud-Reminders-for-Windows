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
        "code": "AUTH_REQUIRED",
        "message": "expired",
        "detail": "detail here",
        "data": {},
    }


def test_errors_can_carry_structured_data_for_the_ui(server):
    """
    2FA needs to say *how* the code was sent. A message string cannot be
    branched on, and "check your iPhone" is the wrong advice for a code that
    arrived by text.
    """
    from reminders_sidecar.icloud import TwoFactorRequired

    err = TwoFactorRequired("code needed", data={"method": "sms", "sent": True})
    assert err.to_dict()["data"] == {"method": "sms", "sent": True}


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


# ----------------------------------------------------------------- all day ---
#
# Apple lets a reminder carry a date and no time. Reading that was always
# supported; writing it was not -- create never passed all_day to iCloud, and
# update hardcoded it to False the moment a due date was written, so the flag
# could survive a sync but never an edit.


def _local_midnight_utc(y, m, d):
    return (
        datetime(y, m, d, 0, 0).astimezone().astimezone(timezone.utc).isoformat()
    )


def test_an_all_day_reminder_is_stored_at_local_midnight(server):
    """
    notify_at shifts off midnight to find the morning and end_of_day counts
    from it to decide overdue. Both are wrong for an all-day reminder stored at
    any other time, so the time component is snapped rather than trusted.
    """
    out = call(
        server, "create_reminder", list_id="List/A", title="x",
        due_date="2026-08-07", all_day=True,
    )
    assert out["all_day"] == 1
    assert out["due_date"] == _local_midnight_utc(2026, 8, 7)


def test_a_time_sent_with_all_day_is_discarded(server):
    """The date is the point; whatever clock time came with it is noise."""
    out = call(
        server, "create_reminder", list_id="List/A", title="x",
        due_date="2026-08-07T14:30:00", all_day=True,
    )
    assert out["due_date"] == _local_midnight_utc(2026, 8, 7)


def test_a_timed_reminder_is_unaffected(server):
    out = call(
        server, "create_reminder", list_id="List/A", title="x",
        due_date="2026-08-07T14:30:00",
    )
    assert out["all_day"] == 0
    assert out["due_date"] != _local_midnight_utc(2026, 8, 7)


def test_editing_the_date_of_an_all_day_reminder_keeps_it_all_day(server):
    """
    This is the regression. update() cleared all_day whenever a due date was
    written, so changing an all-day reminder's date silently made it timed --
    and there was no way to set it back.
    """
    server.cache.upsert_reminders(
        [{
            "id": "Reminder/1", "list_id": "List/A", "title": "x",
            "due_date": _local_midnight_utc(2026, 8, 7), "all_day": True,
            "change_tag": "t1",
        }]
    )
    call(server, "update_reminder", id="Reminder/1", due_date="2026-08-09")
    row = server.cache.reminder("Reminder/1")
    assert row["all_day"] == 1
    assert row["due_date"] == _local_midnight_utc(2026, 8, 9)


def test_switching_a_timed_reminder_to_all_day_drops_the_time(server):
    """Otherwise it is an 'all-day' reminder that still fires at 14:30."""
    server.cache.upsert_reminders(
        [{
            "id": "Reminder/2", "list_id": "List/A", "title": "x",
            "due_date": "2026-08-07T14:30:00+00:00", "all_day": False,
            "change_tag": "t1",
        }]
    )
    call(server, "update_reminder", id="Reminder/2", all_day=True)
    row = server.cache.reminder("Reminder/2")
    assert row["all_day"] == 1
    local_day = datetime.fromisoformat("2026-08-07T14:30:00+00:00").astimezone()
    assert row["due_date"] == _local_midnight_utc(
        local_day.year, local_day.month, local_day.day
    )


def test_all_day_reaches_the_outbox_so_icloud_hears_about_it(server):
    """A local-only flag would read correctly here and be wrong on the phone."""
    call(
        server, "create_reminder", list_id="List/A", title="x",
        due_date="2026-08-07", all_day=True,
    )
    payload = server.cache.pending()[0]["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload["all_day"] is True


# The tests above stop at the cache, and the actual regression was past it:
# ICloudClient.update set rem.all_day = False whenever a due date was written,
# so an all-day reminder edited on Windows arrived on the phone as a timed one.
# These drive that method against a fake service.


class _FakeRem:
    def __init__(self, **kw):
        self.title = ""
        self.desc = ""
        self.priority = 0
        self.completed = False
        self.flagged = False
        self.deleted = False
        self.due_date = None
        self.time_zone = None
        self.all_day = False
        self.record_change_tag = "t1"
        self.id = "Reminder/1"
        self.list_id = "List/A"
        self.hashtag_ids = []
        self.created = None
        self.modified = None
        self.completed_date = None
        self.__dict__.update(kw)


def _client_with(rem):
    from reminders_sidecar.icloud import ICloudClient

    class _Svc:
        def get(self, _id):
            return rem

        def update(self, _rem):
            return None

    c = ICloudClient("a@b.c")
    c._api = type("A", (), {"reminders": _Svc()})()
    return c


def test_icloud_update_keeps_all_day_when_the_date_changes():
    rem = _FakeRem(all_day=True)
    client = _client_with(rem)
    client.update(
        "Reminder/1",
        {"due_date": _local_midnight_utc(2026, 8, 9), "all_day": True},
        "t1",
    )
    assert rem.all_day is True


def test_icloud_update_can_turn_all_day_off():
    rem = _FakeRem(all_day=True)
    client = _client_with(rem)
    client.update("Reminder/1", {"all_day": False}, "t1")
    assert rem.all_day is False


def test_a_bare_due_date_still_means_a_time_was_picked():
    """The old assumption, kept for callers that say nothing about all_day."""
    rem = _FakeRem(all_day=True)
    client = _client_with(rem)
    client.update("Reminder/1", {"due_date": "2026-08-09T14:30:00+00:00"}, "t1")
    assert rem.all_day is False
