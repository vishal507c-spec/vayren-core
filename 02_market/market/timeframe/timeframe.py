"""Timeframe ladder — canonical candidate granularities and their seconds.

The ladder is the universe of candidate timeframes. Whether a candidate
actually exists for a symbol is always decided from the SQLite database
(see ``available_timeframes``) — never hardcoded as a fixed available list.
"""

_TIMEFRAME_LADDER: tuple[tuple[str, int], ...] = (
    ("1m", 60),
    ("3m", 180),
    ("5m", 300),
    ("15m", 900),
    ("30m", 1800),
    ("45m", 2700),
    ("1h", 3600),
    ("2h", 7200),
    ("4h", 14400),
    ("1D", 86400),
    ("1W", 604800),
)

TIMEFRAME_LADDER: tuple[str, ...] = tuple(label for label, _ in _TIMEFRAME_LADDER)

_UNIT_SECONDS: dict[str, int] = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "D": 86400,
    "W": 604800,
}


def _parse_generated_label(name: str) -> int | None:
    """Parse labels like ``90m``, ``2D``, ``2W`` produced for non-ladder granularities."""
    if len(name) < 2:
        return None
    try:
        number = int(name[:-1])
    except ValueError:
        return None
    factor = _UNIT_SECONDS.get(name[-1])
    if factor is None or number <= 0:
        return None
    return number * factor


def timeframe_seconds(name: str) -> int | None:
    """Seconds for a timeframe label (ladder or generated), or None if invalid."""
    for label, seconds in _TIMEFRAME_LADDER:
        if label == name:
            return seconds
    return _parse_generated_label(name)


def timeframe_name(seconds: int) -> str | None:
    """Ladder label for a granularity, or None if it is not in the ladder."""
    for label, ladder_seconds in _TIMEFRAME_LADDER:
        if ladder_seconds == seconds:
            return label
    return None


def generate_label(seconds: int) -> str:
    """Human label for a granularity outside the ladder, derived from seconds."""
    if seconds % 604800 == 0:
        return f"{seconds // 604800}W"
    if seconds % 86400 == 0:
        return f"{seconds // 86400}D"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def available_timeframes(base_seconds: int) -> tuple[str, ...]:
    """Timeframes the database can produce from its detected base bar duration.

    Aggregation only ever merges real rows, so a candidate exists when it is
    a whole multiple of the base duration. The base itself is always included,
    under its ladder label or a generated one. Never cached — callers re-run
    detection against SQLite.
    """
    if base_seconds <= 0:
        return ()
    pairs = [
        (seconds, label)
        for label, seconds in _TIMEFRAME_LADDER
        if seconds >= base_seconds and seconds % base_seconds == 0
    ]
    if timeframe_name(base_seconds) is None:
        pairs.append((base_seconds, generate_label(base_seconds)))
    pairs.sort()
    return tuple(label for _, label in pairs)
