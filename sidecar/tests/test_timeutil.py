"""
Due dates are wall-clock times, not instants.

These tests are written against a UTC-4 zone on purpose: the bug they pin only
appears west of Greenwich, and it was reported from there. Reading Apple's
stored timestamp as a real instant made an all-day reminder due 3 August show as
"8:00 PM" on 2 August and sort under Overdue a day early.

The container these run in is UTC, so every test names its zone explicitly
rather than relying on the machine's.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from reminders_sidecar import timeutil
from reminders_sidecar.timeutil import (
    ALL_DAY_HOUR,
    end_of_day,
    floating_to_instant,
    instant_to_floating,
    notify_at,
)

NY = ZoneInfo("America/New_York")


@pytest.fixture
def eastern(monkeypatch):
    """Pin the 'machine zone' to US Eastern for the duration of a test."""
    monkeypatch.setattr(timeutil, "_LOCAL_ZONE", NY)
    return NY


def utc(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


# ------------------------------------------------------- the reported bug ---
def test_all_day_reminder_stays_on_its_own_day(eastern):
    """
    The exact failure: an all-day reminder due 3 August is stored as midnight.

    Read as an instant that is 2 August, 8:00 PM in New York -- which is what
    the app displayed, and why it landed under Overdue a day early.
    """
    stored = utc(2026, 8, 3)

    wrong = stored.astimezone(NY)
    assert (wrong.day, wrong.hour) == (2, 20)  # the bug, reproduced

    right = floating_to_instant(stored).astimezone(NY)
    assert (right.month, right.day) == (8, 3)
    assert (right.hour, right.minute) == (0, 0)


def test_timed_reminder_keeps_the_hour_it_was_set_for(eastern):
    # "5 August at 3:30 PM" is stored with those components, encoded as UTC.
    stored = utc(2026, 8, 5, 15, 30)
    local = floating_to_instant(stored).astimezone(NY)
    assert (local.hour, local.minute) == (15, 30)
    # Read as an instant it would have been 11:30 -- four hours early.
    assert stored.astimezone(NY).hour == 11


def test_a_reminder_due_later_today_is_not_overdue_yet(eastern):
    """An 8 PM reminder must not read as overdue at 5 PM."""
    stored = utc(2026, 8, 3, 20, 0)
    due = floating_to_instant(stored)
    five_pm = datetime(2026, 8, 3, 17, 0, tzinfo=NY)
    assert due > five_pm
    # The old reading put it at 4 PM, i.e. already past.
    assert stored < five_pm


# ------------------------------------------------------------ round trips ---
def test_write_then_read_is_the_identity(eastern):
    for wall in (utc(2026, 1, 9, 6, 15), utc(2026, 7, 4, 23, 59), utc(2026, 8, 3)):
        assert floating_to_instant(instant_to_floating(floating_to_instant(wall))) == (
            floating_to_instant(wall)
        )


def test_writing_encodes_the_local_wall_clock(eastern):
    """
    pyicloud puts `.timestamp()` straight into the field, so what we hand it
    must already carry the wall-clock fields in its UTC components.
    """
    instant = datetime(2026, 8, 3, 20, 0, tzinfo=NY)
    encoded = instant_to_floating(instant)
    assert (encoded.hour, encoded.minute) == (20, 0)
    assert encoded.tzinfo == timezone.utc


def test_writing_rejects_a_naive_datetime(eastern):
    with pytest.raises(ValueError):
        instant_to_floating(datetime(2026, 8, 3, 20, 0))


# -------------------------------------------------------------------- DST ---
def test_dst_is_resolved_per_date_not_by_todays_offset(eastern):
    """
    A January date must use January's offset even when converted in July.

    This is why the zone comes from tzlocal rather than
    `datetime.now().astimezone().tzinfo`, which freezes whatever offset is in
    effect right now.
    """
    winter = floating_to_instant(utc(2026, 1, 15, 9, 0))
    summer = floating_to_instant(utc(2026, 7, 15, 9, 0))
    assert winter.hour == 14  # EST, UTC-5
    assert summer.hour == 13  # EDT, UTC-4
    for got in (winter, summer):
        assert got.astimezone(NY).hour == 9


# ---------------------------------------------------------- pinned zones ---
def test_a_reminder_pinned_to_a_zone_uses_that_zone(eastern):
    """
    TimeZone names the zone the components belong to. A 9 AM meeting reminder
    pinned to Paris is 3 AM in New York, not 9.
    """
    stored = utc(2026, 8, 3, 9, 0)
    due = floating_to_instant(stored, "Europe/Paris")
    assert due.astimezone(ZoneInfo("Europe/Paris")).hour == 9
    assert due.astimezone(NY).hour == 3


def test_an_unknown_zone_falls_back_to_local_rather_than_failing(eastern):
    # Missing tzdata on Windows looks exactly like a bad name from here.
    due = floating_to_instant(utc(2026, 8, 3, 9, 0), "Mars/Olympus_Mons")
    assert due.astimezone(NY).hour == 9


# ------------------------------------------------------------ alert times ---
def test_all_day_reminders_alert_in_the_morning(eastern):
    due = floating_to_instant(utc(2026, 8, 3))  # local midnight
    when = notify_at(due, all_day=True).astimezone(NY)
    assert (when.day, when.hour) == (3, ALL_DAY_HOUR)


def test_a_timed_reminder_alerts_when_it_is_due(eastern):
    due = floating_to_instant(utc(2026, 8, 3, 20, 0))
    assert notify_at(due, all_day=False) == due


def test_an_all_day_reminder_is_late_only_once_its_day_is_over(eastern):
    due = floating_to_instant(utc(2026, 8, 3))
    deadline = end_of_day(due).astimezone(NY)
    assert (deadline.day, deadline.hour) == (4, 0)
    assert deadline > datetime(2026, 8, 3, 23, 59, tzinfo=NY)


def test_none_passes_through_everywhere(eastern):
    assert floating_to_instant(None) is None
    assert instant_to_floating(None) is None
    assert notify_at(None) is None
    assert end_of_day(None) is None


# ------------------------------------------------- through the iCloud layer ---
class FakeReminder:
    """Shaped like pyicloud's Reminder, only with the fields the mapper reads."""

    def __init__(self, due, all_day=False, time_zone=None):
        self.id = "R/1"
        self.list_id = "List/A"
        self.title = "Reset CSM Email"
        self.desc = ""
        self.due_date = due
        self.priority = 0
        self.completed = False
        self.completed_date = None
        self.flagged = False
        self.all_day = all_day
        self.deleted = False
        self.time_zone = time_zone
        self.created = utc(2026, 7, 1, 12)
        self.modified = utc(2026, 7, 1, 12)
        self.record_change_tag = "tag1"
        self.hashtag_ids = []


def test_the_mapper_converts_due_dates_but_not_timestamps(eastern):
    from reminders_sidecar.icloud import ICloudClient

    d = ICloudClient._reminder_to_dict(FakeReminder(utc(2026, 8, 3), all_day=True))

    # Due date: wall clock, so it stays on 3 August in local terms.
    assert datetime.fromisoformat(d["due_date"]).astimezone(NY).day == 3
    assert d["all_day"] is True

    # CreationDate and LastModifiedDate are real server instants and must NOT
    # be shifted -- 12:00 UTC is 08:00 in New York and that is correct.
    assert datetime.fromisoformat(d["created"]).astimezone(NY).hour == 8


def test_the_mapper_honours_a_records_own_zone(eastern):
    from reminders_sidecar.icloud import ICloudClient

    d = ICloudClient._reminder_to_dict(
        FakeReminder(utc(2026, 8, 3, 9, 0), time_zone="Europe/Paris")
    )
    assert d["time_zone"] == "Europe/Paris"
    assert datetime.fromisoformat(d["due_date"]).astimezone(NY).hour == 3
