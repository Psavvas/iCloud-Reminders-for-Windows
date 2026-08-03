"""
Apple's reminder clock, and how it differs from a real instant.

A reminder due "3 August at 8 PM" is a *wall-clock* fact, not a point on the
world's timeline. Apple stores it as a CloudKit TIMESTAMP -- milliseconds since
the epoch -- whose components are those wall-clock fields encoded as though the
zone were UTC. The record's separate `TimeZone` field names the zone those
components belong to; when it is null the reminder floats, meaning it fires at
that wall-clock time wherever the device happens to be.

Reading the timestamp as a genuine instant is therefore wrong by the local UTC
offset, and it is wrong in the direction that makes things look overdue early
west of Greenwich. An all-day reminder is the clearest case: its stored value is
midnight, so at UTC-4 it renders as 8:00 PM *the previous evening* and lands
under Overdue a full day ahead of time.

Everything downstream of this module -- the cache, the smart-list queries, the
notification scheduler -- works in true tz-aware UTC instants. This is the only
place the two representations meet.

`scripts/check-due-dates.py` re-checks the encoding against live data: it prints
the raw milliseconds beside both readings so they can be compared with what an
iPhone shows.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

LOGGER = logging.getLogger(__name__)

# Apple alerts an all-day reminder in the morning rather than at midnight.
# Matching that is the difference between a useful toast and one fired while
# nobody is awake to read it.
ALL_DAY_HOUR = 9

_LOCAL_ZONE = None


def local_zone():
    """
    The machine's zone, as a real zone rather than a fixed offset.

    `datetime.now().astimezone().tzinfo` is the offset in effect *right now*;
    using it to convert a January date in July is an hour wrong. tzlocal returns
    a zone that carries its own DST rules. It ships with pyicloud already.
    """
    global _LOCAL_ZONE
    if _LOCAL_ZONE is None:
        try:
            from tzlocal import get_localzone

            _LOCAL_ZONE = get_localzone()
        except Exception as exc:  # noqa: BLE001 - a fixed offset beats crashing
            LOGGER.warning(
                "tzlocal unavailable (%s); dates outside the current DST period "
                "may be off by an hour",
                exc,
            )
            _LOCAL_ZONE = datetime.now(timezone.utc).astimezone().tzinfo
    return _LOCAL_ZONE


def named_zone(name: Optional[str]):
    """Resolve an IANA name, or None if it is missing or unknown."""
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(str(name))
    except Exception as exc:  # noqa: BLE001
        # On Windows this is usually a missing tzdata package rather than a bad
        # name; either way the reminder still has to be readable.
        LOGGER.debug("time zone %r unusable (%s); using local", name, exc)
        return None


def floating_to_instant(
    dt: Optional[datetime], tz_name: Optional[str] = None
) -> Optional[datetime]:
    """
    Apple's stored timestamp -> the instant the reminder is actually due.

    Takes the UTC components as wall-clock fields and re-anchors them: in the
    reminder's own zone when it names one, otherwise in the machine's, which is
    what "floating" means for a reminder that travels with you.
    """
    if dt is None:
        return None
    zone = named_zone(tz_name) or local_zone()
    wall = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return wall.replace(tzinfo=zone).astimezone(timezone.utc)


def instant_to_floating(
    dt: Optional[datetime], tz_name: Optional[str] = None
) -> Optional[datetime]:
    """
    The inverse, for writes.

    Hands pyicloud a datetime whose UTC components are the wall-clock time we
    want stored, since pyicloud passes `.timestamp()` straight into the field.
    Without this a reminder set for 8 PM here reads back as midnight on the
    phone.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        raise ValueError("instant_to_floating requires a tz-aware datetime")
    zone = named_zone(tz_name) or local_zone()
    return dt.astimezone(zone).replace(tzinfo=timezone.utc)


def notify_at(due: Optional[datetime], all_day: bool = False) -> Optional[datetime]:
    """
    When a reminder should toast, which is not always when it is due.

    An all-day reminder's instant is local midnight. Firing then would put the
    toast on a locked screen at 00:00, so it moves to ALL_DAY_HOUR the same day
    -- the same thing Apple does.
    """
    if due is None:
        return None
    if not all_day:
        return due
    local = due.astimezone(local_zone())
    return local.replace(
        hour=ALL_DAY_HOUR, minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc)


def end_of_day(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Midnight at the end of `dt`'s local day.

    An all-day reminder is not late at 00:01; it is late once its day is over.
    """
    if dt is None:
        return None
    local = dt.astimezone(local_zone())
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return (start + timedelta(days=1)).astimezone(timezone.utc)
