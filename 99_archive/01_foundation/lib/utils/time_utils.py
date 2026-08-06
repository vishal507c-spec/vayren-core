from datetime import UTC, datetime, time, timedelta


def now_utc() -> datetime:
    """Returns current UTC datetime."""
    return datetime.now(UTC)


def to_iso(dt: datetime | None = None) -> str:
    """Format datetime as ISO 8601 string."""
    if dt is None:
        dt = now_utc()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def parse_iso(value: str) -> datetime:
    """Parse ISO 8601 string to datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_market_hours(dt: datetime | None = None, market_open: time = time(9, 30), market_close: time = time(16, 0)) -> bool:
    """Check if current time is within market hours (default US equities)."""
    if dt is None:
        dt = now_utc()
    if dt.weekday() >= 5:
        return False
    current = dt.time()
    return market_open <= current <= market_close


def days_between(start: datetime, end: datetime) -> int:
    """Returns number of calendar days between two datetimes."""
    return abs((end.date() - start.date()).days)


def floor_to_interval(dt: datetime, seconds: int = 60) -> datetime:
    """Floor a datetime to the nearest interval."""
    ts = dt.timestamp()
    return datetime.fromtimestamp(ts - (ts % seconds), tz=UTC)
