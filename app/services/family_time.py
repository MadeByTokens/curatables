"""The family's own calendar: where "today" starts for the stats.

Timestamps are stored as UTC text ("YYYY-MM-DD HH:MM:SS", SQLite's
datetime('now')) and the box itself runs on UTC, so "today" has to be
worked out in the family's time zone, then turned back into UTC to
compare with what is stored.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_FMT = "%Y-%m-%d %H:%M:%S"


def valid_timezone(name: str) -> bool:
    if not name or len(name) > 64 or name.startswith(("/", ".")) or ".." in name:
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def family_zone(name: str) -> tzinfo:
    """The named zone, or the box's own local zone when none is set."""
    if valid_timezone(name):
        return ZoneInfo(name)
    return datetime.now().astimezone().tzinfo or timezone.utc


def family_today(name: str, now: datetime | None = None) -> date:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(family_zone(name)).date()


def utc_start_of(day: date, name: str) -> str:
    """Midnight at the start of `day` in the family's zone, as stored UTC text."""
    local_midnight = datetime.combine(day, time.min, tzinfo=family_zone(name))
    return local_midnight.astimezone(timezone.utc).strftime(_FMT)


def since_days_ago(days: int, name: str, now: datetime | None = None) -> str:
    """UTC text for the family's midnight `days` days before today."""
    return utc_start_of(family_today(name, now) - timedelta(days=days), name)
