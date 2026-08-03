"""
The sleep/wake policy is the part most likely to misbehave in the wild and the
hardest to test on a real machine, so it gets covered properly here.
"""

from datetime import timedelta

import pytest

from reminders_sidecar.db import Cache, to_iso, utcnow
from reminders_sidecar.notifications import plan_notifications


@pytest.fixture
def cache(tmp_path):
    c = Cache(tmp_path / "n.db")
    yield c
    c.close()


def due(cache, n, offset_minutes, prefix="r"):
    now = utcnow()
    cache.upsert_reminders(
        [
            {
                "id": f"Reminder/{prefix}{i}",
                "list_id": "List/A",
                "title": f"task {prefix}{i}",
                "due_date": to_iso(now + timedelta(minutes=offset_minutes)),
                "change_tag": "t",
            }
            for i in range(n)
        ]
    )


def test_nothing_due_produces_no_toasts(cache):
    due(cache, 3, offset_minutes=120)  # all in the future
    plan = plan_notifications(cache)
    assert plan.toasts == []
    assert plan.notified_ids == []


def test_single_due_reminder_fires_individually(cache):
    due(cache, 1, offset_minutes=-1)
    plan = plan_notifications(cache)
    assert len(plan.toasts) == 1
    assert plan.toasts[0].kind == "reminder"
    assert plan.toasts[0].title == "task r0"


def test_burst_of_fresh_dues_collapses_to_one_summary(cache):
    """Five things due at once must not produce five toasts."""
    due(cache, 5, offset_minutes=-1)
    plan = plan_notifications(cache, max_individual=3)
    assert len(plan.toasts) == 1
    assert plan.toasts[0].kind == "summary"
    assert "5 reminders are due" in plan.toasts[0].title
    assert len(plan.notified_ids) == 5


def test_slept_through_due_times_gives_one_summary_not_a_flood(cache):
    """The machine-was-asleep case: six overdue items, one toast."""
    due(cache, 6, offset_minutes=-600)  # ten hours ago
    plan = plan_notifications(cache, stale_after_minutes=60)
    assert len(plan.toasts) == 1
    assert plan.toasts[0].kind == "summary"
    assert "were due while you were away" in plan.toasts[0].title
    assert len(plan.notified_ids) == 6


def test_missed_and_fresh_are_reported_separately(cache):
    due(cache, 4, offset_minutes=-600, prefix="old")
    due(cache, 1, offset_minutes=-1, prefix="new")
    plan = plan_notifications(cache, stale_after_minutes=60)
    kinds = sorted(t.kind for t in plan.toasts)
    assert kinds == ["reminder", "summary"]
    assert len(plan.notified_ids) == 5


def test_notified_reminders_never_fire_twice(cache):
    due(cache, 2, offset_minutes=-1)
    first = plan_notifications(cache)
    assert first.notified_ids
    second = plan_notifications(cache)
    assert second.toasts == []
    assert second.notified_ids == []


def test_completed_and_deleted_are_never_notified(cache):
    now = utcnow()
    cache.upsert_reminders(
        [
            {
                "id": "Reminder/done",
                "list_id": "List/A",
                "title": "done",
                "due_date": to_iso(now - timedelta(minutes=5)),
                "completed": True,
            },
            {
                "id": "Reminder/gone",
                "list_id": "List/A",
                "title": "gone",
                "due_date": to_iso(now - timedelta(minutes=5)),
                "deleted": True,
            },
        ]
    )
    plan = plan_notifications(cache)
    assert plan.toasts == []


def test_undated_reminders_are_never_notified(cache):
    cache.upsert_reminders(
        [{"id": "Reminder/x", "list_id": "List/A", "title": "someday"}]
    )
    assert plan_notifications(cache).toasts == []


# ------------------------------------------------------ all-day reminders ---
#
# An all-day reminder's instant is local midnight. Firing a toast then would put
# it on a locked screen at 00:00, and treating midnight as its due time made
# every all-day reminder look like something missed overnight.


def add_all_day(cache, day_offset=0, prefix="ad"):
    """One all-day reminder, due at local midnight `day_offset` days from now."""
    local_midnight = (
        (utcnow() + timedelta(days=day_offset))
        .astimezone()
        .replace(hour=0, minute=0, second=0, microsecond=0)
    )
    cache.upsert_reminders(
        [
            {
                "id": f"Reminder/{prefix}",
                "list_id": "List/A",
                "title": "pay the water bill",
                "due_date": to_iso(local_midnight),
                "all_day": True,
                "change_tag": "t",
            }
        ]
    )
    return local_midnight


def test_an_all_day_reminder_does_not_toast_at_midnight(cache):
    midnight = add_all_day(cache)
    plan = plan_notifications(cache, now=midnight + timedelta(minutes=1))
    assert plan.toasts == []
    # Crucially it is not marked notified either, or the 9am toast would be lost.
    assert plan.notified_ids == []


def test_an_all_day_reminder_toasts_in_the_morning(cache):
    from reminders_sidecar.timeutil import ALL_DAY_HOUR

    midnight = add_all_day(cache)
    plan = plan_notifications(cache, now=midnight + timedelta(hours=ALL_DAY_HOUR))
    assert len(plan.toasts) == 1
    assert plan.toasts[0].kind == "reminder"
    # No invented clock time in the body -- it has a date and nothing else.
    assert ":" not in plan.toasts[0].body


def test_an_all_day_reminder_is_not_reported_as_missed_overnight(cache):
    """
    Before this, the 00:00 instant was already hours stale by breakfast, so an
    ordinary all-day reminder arrived as "were due while you were away".
    """
    from reminders_sidecar.timeutil import ALL_DAY_HOUR

    midnight = add_all_day(cache)
    plan = plan_notifications(
        cache,
        now=midnight + timedelta(hours=ALL_DAY_HOUR, minutes=5),
        stale_after_minutes=60,
    )
    assert [t.kind for t in plan.toasts] == ["reminder"]


def test_a_genuinely_old_all_day_reminder_still_collapses(cache):
    midnight = add_all_day(cache, day_offset=-3)
    plan = plan_notifications(cache, now=utcnow(), stale_after_minutes=60)
    assert [t.kind for t in plan.toasts] == ["summary"]
