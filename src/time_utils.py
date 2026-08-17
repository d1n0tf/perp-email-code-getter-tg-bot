"""Timezone helpers for the application UI and subscription calendar."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone


# Moscow has used UTC+03:00 without daylight saving time since 2014.  A fixed
# offset keeps this working on Windows deployments without the optional tzdata
# package, while retaining the correct current MSK offset.
MOSCOW_TZ = timezone(timedelta(hours=3), name="MSK")


def to_utc(value: datetime) -> datetime:
    """Normalize an aware datetime to the storage timezone (UTC)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=MOSCOW_TZ)
    return value.astimezone(timezone.utc)


def to_moscow(value: datetime) -> datetime:
    """Convert a stored UTC datetime to the UI timezone."""
    return to_utc(value).astimezone(MOSCOW_TZ)


def moscow_end_of_day(value: date) -> datetime:
    """Return the last instant of the specified Moscow calendar day in UTC."""
    return datetime.combine(value, time.max, tzinfo=MOSCOW_TZ).astimezone(timezone.utc)
