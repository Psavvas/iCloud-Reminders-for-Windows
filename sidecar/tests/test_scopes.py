"""
Smart lists and settings.

"Today" is defined against the machine's local calendar day, not UTC's, so
these tests build fixtures relative to local midnight rather than hardcoding
timestamps.
"""

from datetime import datetime, timedelta, timezone

import pytest

from reminders_sidecar.db import Cache, to_iso, utcnow


@pytest.fixture
def cache(tmp_path):
    c = Cache(tmp_path / "sc.db")
    yield c
    c.close()


def local_midnight():
    return utcnow().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)


def mk(rid, **kw):
    base = {"id": rid, "list_id": "List/A", "title": rid, "change_tag": "t"}
    base.update(kw)
    return base


@pytest.fixture
def seeded(cache):
    mid = local_midnight()
    cache.upsert_reminders([
        mk("overdue", due_date=to_iso(mid - timedelta(days=2))),
        mk("today", due_date=to_iso(mid + timedelta(hours=10))),
        mk("tomorrow", due_date=to_iso(mid + timedelta(days=1, hours=3))),
        mk("next_week", due_date=to_iso(mid + timedelta(days=7))),
        mk("undated"),
        mk("done", completed=True, due_date=to_iso(mid + timedelta(hours=2))),
        mk("gone", deleted=True),
    ])
    return cache


def ids(rows):
    return {r["id"] for r in rows}


def test_today_includes_overdue(seeded):
    """Apple's Today shows anything still outstanding, not just today's date."""
    assert ids(seeded.reminders(scope="today")) == {"overdue", "today"}


def test_today_excludes_completed_and_deleted(seeded):
    got = ids(seeded.reminders(scope="today"))
    assert "done" not in got and "gone" not in got


def test_upcoming_starts_tomorrow(seeded):
    assert ids(seeded.reminders(scope="upcoming")) == {"tomorrow", "next_week"}


def test_upcoming_excludes_undated(seeded):
    assert "undated" not in ids(seeded.reminders(scope="upcoming"))


def test_completed_scope(seeded):
    assert ids(seeded.reminders(scope="completed")) == {"done"}


def test_deleted_scope_is_the_only_one_showing_soft_deletes(seeded):
    assert ids(seeded.reminders(scope="deleted")) == {"gone"}
    for scope in (None, "today", "upcoming", "completed", "all"):
        assert "gone" not in ids(seeded.reminders(scope=scope))


def test_all_scope_is_everything_outstanding(seeded):
    assert ids(seeded.reminders(scope="all")) == {
        "overdue", "today", "tomorrow", "next_week", "undated"
    }


def test_smart_counts_match_the_queries(seeded):
    counts = seeded.smart_counts()
    for scope in ("today", "upcoming", "completed", "deleted", "all"):
        assert counts[scope] == len(seeded.reminders(scope=scope)), scope


def test_search_applies_within_a_scope(seeded):
    assert ids(seeded.reminders(scope="upcoming", search="tomorrow")) == {"tomorrow"}


def test_day_boundary_uses_local_time_not_utc(cache):
    """
    A reminder at 23:00 local today belongs to Today even when that instant is
    already tomorrow in UTC.
    """
    mid = local_midnight()
    cache.upsert_reminders([mk("late", due_date=to_iso(mid + timedelta(hours=23)))])
    assert ids(cache.reminders(scope="today")) == {"late"}
    assert ids(cache.reminders(scope="upcoming")) == set()


def test_settings_defaults_and_merge(cache):
    s = cache.get_settings()
    assert s["theme"] == "system"
    assert s["sync_minutes"] == 10
    assert s["notifications_enabled"] is True

    cache.set_settings({"theme": "dark", "sync_minutes": 5})
    s = cache.get_settings()
    assert s["theme"] == "dark" and s["sync_minutes"] == 5
    # untouched keys survive
    assert s["notifications_enabled"] is True


def test_settings_ignores_unknown_keys(cache):
    cache.set_settings({"nonsense": 1})
    assert "nonsense" not in cache.get_settings()
