"""Timezone utilities -- all timestamps in Indian Standard Time (IST)."""

from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> datetime:
    """Return the current time as a timezone-aware IST datetime."""
    return datetime.now(IST)


def to_naive_ist(dt: datetime) -> datetime:
    """Convert any datetime to a naive datetime in IST.

    This is what gets stored in MySQL (DATETIME columns are timezone-unaware,
    so we store IST values directly).
    """
    if dt.tzinfo is None:
        # Assume UTC if naive
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).replace(tzinfo=None)


def make_aware(dt: datetime) -> datetime:
    """Ensure a datetime is timezone-aware.  Assumes IST if naive."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST)
    return dt
