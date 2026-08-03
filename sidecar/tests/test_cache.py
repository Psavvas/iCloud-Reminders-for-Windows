from datetime import datetime, timedelta, timezone

import pytest

from reminders_sidecar.db import Cache, from_iso, to_iso, utcnow


@pytest.fixture
def cache(tmp_path):
    c = Cache(tmp_path / "t.db")
    yield c
    c.close()


def mk(rid="Reminder/1", **kw):
    base = {
        "id": rid,
        "list_id": "List/A",
        "title": "thing",
        "description": "",
        "due_date": None,
        "priority": 0,
        "completed": False,
        "deleted": False,
        "change_tag": "t1",
    }
    base.update(kw)
    return base


def test_naive_datetime_is_rejected():
    # The whole point of the tz landmine: never silently guess.
    with pytest.raises(ValueError, match="naive datetime"):
        to_iso(datetime(2026, 1, 1, 12, 0))


def test_to_iso_normalizes_to_utc():
    dt = datetime(2026, 1, 1, 12, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert to_iso(dt) == "2026-01-01T17:00:00+00:00"
    assert from_iso(to_iso(dt)) == dt.astimezone(timezone.utc)


def test_lists_roundtrip_and_open_count(cache):
    cache.replace_lists(
        [
            {"id": "List/A", "title": "Inbox", "color_hex": "#CC73E1", "count": 2},
            {"id": "List/G", "title": "School", "color_hex": None, "is_group": True},
        ]
    )
    cache.upsert_reminders([mk("Reminder/1"), mk("Reminder/2", completed=True)])
    rows = {l["title"]: l for l in cache.lists()}
    assert rows["Inbox"]["color_hex"] == "#CC73E1"
    assert rows["School"]["is_group"] == 1
    # open_count counts only incomplete, undeleted reminders
    assert rows["Inbox"]["open_count"] == 1


def test_upsert_preserves_notified_flag(cache):
    """A re-sync must never cause a toast to fire twice."""
    cache.upsert_reminders([mk()])
    cache.mark_notified(["Reminder/1"])
    cache.upsert_reminders([mk(title="renamed")])
    row = cache.reminder("Reminder/1")
    assert row["title"] == "renamed"
    assert row["notified"] == 1


def test_upsert_does_not_clobber_unpushed_local_edit(cache):
    cache.upsert_reminders([mk(title="server")])
    cache.apply_local_edit("Reminder/1", {"title": "mine"})
    # Server copy arrives while our edit is still queued.
    cache.upsert_reminders([mk(title="server again")])
    assert cache.reminder("Reminder/1")["title"] == "mine"


def test_filtering_by_list_completed_and_search(cache):
    cache.upsert_reminders(
        [
            mk("Reminder/1", title="buy milk"),
            mk("Reminder/2", title="done thing", completed=True),
            mk("Reminder/3", title="other list", list_id="List/B"),
            mk("Reminder/4", title="deleted", deleted=True),
        ]
    )
    assert {r["id"] for r in cache.reminders(list_id="List/A")} == {"Reminder/1"}
    assert len(cache.reminders(list_id="List/A", include_completed=True)) == 2
    assert {r["id"] for r in cache.reminders(search="milk")} == {"Reminder/1"}
    # soft-deleted rows never surface
    assert all(r["id"] != "Reminder/4" for r in cache.reminders())


def test_due_ordering_puts_undated_last(cache):
    soon = to_iso(utcnow() + timedelta(hours=1))
    later = to_iso(utcnow() + timedelta(days=1))
    cache.upsert_reminders(
        [
            mk("Reminder/none", title="no date"),
            mk("Reminder/later", due_date=later),
            mk("Reminder/soon", due_date=soon),
        ]
    )
    assert [r["id"] for r in cache.reminders()] == [
        "Reminder/soon",
        "Reminder/later",
        "Reminder/none",
    ]


def test_tag_filtering(cache):
    cache.upsert_reminders([mk("Reminder/1"), mk("Reminder/2")])
    cache.replace_tags_for("Reminder/1", [{"id": "H/1", "name": "errands"}])
    cache.replace_tags_for("Reminder/2", [{"id": "H/2", "name": "school"}])
    assert {r["id"] for r in cache.reminders(tag="errands")} == {"Reminder/1"}
    assert cache.reminder("Reminder/1")["tags"] == ["errands"]
    assert [t["name"] for t in cache.all_tags()] == ["errands", "school"]


def test_replace_tags_is_wholesale(cache):
    """The phone rewrites HashtagIDs wholesale; mirror that, don't merge."""
    cache.upsert_reminders([mk()])
    cache.replace_tags_for("Reminder/1", [{"id": "H/1", "name": "old"}])
    cache.replace_tags_for("Reminder/1", [{"id": "H/2", "name": "new"}])
    assert cache.reminder("Reminder/1")["tags"] == ["new"]


def test_outbox_roundtrip(cache):
    cache.upsert_reminders([mk()])
    cache.enqueue("Reminder/1", "update", {"title": "x"}, "t1")
    pending = cache.pending()
    assert len(pending) == 1
    assert pending[0]["payload"] == {"title": "x"}
    assert pending[0]["base_tag"] == "t1"
    cache.record_failure(pending[0]["seq"], "boom")
    assert cache.pending()[0]["attempts"] == 1
    cache.dequeue(pending[0]["seq"])
    assert cache.pending() == []


def test_replace_id_moves_tags_and_outbox(cache):
    cache.insert_local_reminder(mk("local/x"))
    cache.replace_tags_for("local/x", [{"id": "H/1", "name": "t"}])
    cache.enqueue("local/x", "create", {}, None)
    cache.replace_id("local/x", "Reminder/9")
    assert cache.reminder("Reminder/9") is not None
    assert cache.reminder("Reminder/9")["tags"] == ["t"]
    assert cache.pending()[0]["reminder_id"] == "Reminder/9"


def test_conflicts_roundtrip(cache):
    cache.record_conflict("Reminder/1", {"title": "mine"}, {"title": "theirs"})
    rows = cache.conflicts()
    assert len(rows) == 1
    assert rows[0]["local"]["title"] == "mine"
    assert rows[0]["remote"]["title"] == "theirs"
    cache.resolve_conflict(rows[0]["id"])
    assert cache.conflicts() == []


# ------------------------------------------------------------- settings ----
def test_per_list_sort_actually_persists(cache):
    """
    set_settings drops keys it has never seen, so a setting missing from the
    defaults silently fails to save. sort_by was in that state: choosing a sort
    order appeared to work and then reverted on the next read.
    """
    saved = cache.set_settings({"sort_by": {"list:List/A": "priority"}})
    assert saved["sort_by"] == {"list:List/A": "priority"}
    assert cache.get_settings()["sort_by"] == {"list:List/A": "priority"}


def test_remember_password_defaults_to_on(cache):
    assert cache.get_settings()["remember_password"] is True
    assert cache.set_settings({"remember_password": False})["remember_password"] is False


def test_unknown_settings_are_still_rejected(cache):
    # The filtering itself is deliberate -- it stops the UI inventing keys.
    assert "nonsense" not in cache.set_settings({"nonsense": 1})
