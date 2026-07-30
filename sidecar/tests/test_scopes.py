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


def order(rows):
    return [r["id"] for r in rows]


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


# ------------------------------------------------------------------ sorting
@pytest.fixture
def sortable(cache):
    mid = local_midnight()
    cache.upsert_reminders([
        mk("b_none", title="banana", priority=0, due_date=to_iso(mid + timedelta(days=3))),
        mk("a_high", title="apple", priority=1, due_date=to_iso(mid + timedelta(days=2))),
        mk("c_med", title="cherry", priority=5, due_date=to_iso(mid + timedelta(days=1))),
        mk("d_low", title="date", priority=9),
    ])
    return cache


def test_sort_by_title_is_case_insensitive(sortable):
    sortable.upsert_reminders([mk("Z_upper", title="Apricot")])
    got = order(sortable.reminders(sort="title"))
    assert got.index("Z_upper") < got.index("b_none")  # Apricot before banana


def test_sort_by_due_puts_undated_last(sortable):
    got = order(sortable.reminders(sort="due"))
    assert got == ["c_med", "a_high", "b_none", "d_low"]


def test_sort_by_priority_uses_apple_ranking_not_the_raw_number(sortable):
    """Apple's values are 1 high, 5 medium, 9 low, 0 none -- not ordinal."""
    assert order(sortable.reminders(sort="priority")) == [
        "a_high", "c_med", "d_low", "b_none"
    ]


def test_unknown_sort_falls_back_to_the_default(sortable):
    assert order(sortable.reminders(sort="nonsense")) == order(
        sortable.reminders(sort="manual")
    )


def test_sort_survives_a_scope_filter(sortable):
    got = order(sortable.reminders(scope="upcoming", sort="priority"))
    assert got == ["a_high", "c_med", "b_none"]  # d_low has no due date


# ---------------------------------------------------------- completed limit
def test_completed_is_capped_and_most_recent_first(cache):
    from reminders_sidecar.db import COMPLETED_LIMIT

    now = utcnow()
    cache.upsert_reminders([
        mk(f"c{i:03d}", completed=True, completed_date=to_iso(now - timedelta(hours=i)))
        for i in range(COMPLETED_LIMIT + 40)
    ])
    rows = cache.reminders(scope="completed")
    assert len(rows) == COMPLETED_LIMIT
    # Newest first: c000 was completed an hour ago, c089 ninety hours ago.
    assert rows[0]["id"] == "c000"
    assert rows[-1]["id"] == f"c{COMPLETED_LIMIT - 1:03d}"


def test_completed_cap_is_not_raised_by_a_bigger_limit(cache):
    from reminders_sidecar.db import COMPLETED_LIMIT

    now = utcnow()
    cache.upsert_reminders([
        mk(f"c{i}", completed=True, completed_date=to_iso(now - timedelta(hours=i)))
        for i in range(120)
    ])
    assert len(cache.reminders(scope="completed", limit=5000)) == COMPLETED_LIMIT


def test_other_scopes_are_not_capped(cache):
    cache.upsert_reminders([mk(f"r{i}") for i in range(120)])
    assert len(cache.reminders(scope="all", limit=1000)) == 120


def test_completed_without_dates_still_returns_rows(cache):
    """Rows cached before completed_date existed have NULL there."""
    cache.upsert_reminders([mk("old", completed=True)])
    assert order(cache.reminders(scope="completed")) == ["old"]


def test_migration_adds_completed_date_to_an_existing_cache(tmp_path):
    """A cache from the previous schema must open, not crash."""
    import sqlite3

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE reminders (id TEXT PRIMARY KEY, list_id TEXT NOT NULL,"
        " title TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',"
        " due_date TEXT, priority INTEGER NOT NULL DEFAULT 0,"
        " completed INTEGER NOT NULL DEFAULT 0, flagged INTEGER NOT NULL DEFAULT 0,"
        " all_day INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0,"
        " created TEXT, modified TEXT, change_tag TEXT,"
        " notified INTEGER NOT NULL DEFAULT 0, dirty INTEGER NOT NULL DEFAULT 0);"
        "INSERT INTO reminders(id, list_id, title) VALUES('R/1','L','kept');"
    )
    con.commit()
    con.close()

    c = Cache(path)
    try:
        assert c.reminder("R/1")["title"] == "kept"
        assert "completed_date" in c.reminder("R/1")
    finally:
        c.close()
