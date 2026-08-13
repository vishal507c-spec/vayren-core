"""Timeframe inference — derive a human-readable candle period from bar spacing.

Given a sequence of bars, this infers the most likely timeframe string
(e.g. "1d", "30m", "1h") from the median gap between consecutive candles.
The median is computed over a sampled prefix of the series (the first
``_INFERENCE_SAMPLE`` gaps) — identical for regular candle series, and
bounded work for very long histories (a 60k-bar series no longer parses
every timestamp). This is a pure-function helper with no Qt, no SQL, no
events.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from statistics import median

from market.models.bar import Bar

_INFERENCE_SAMPLE = 2048

_SECONDS_LADDER = (
    60,
    300,
    900,
    1800,
    3600,
    7200,
    10800,
    21600,
    43200,
    86400,
)

_FORMATTERS: dict[int, Callable[[int], str]] = {
    60: lambda s: f"{s // 60}m",
    300: lambda s: f"{s // 60}m",
    900: lambda s: f"{s // 60}m",
    1800: lambda s: f"{s // 60}m",
    3600: lambda s: f"{s // 3600}h",
    7200: lambda s: f"{s // 3600}h",
    10800: lambda s: f"{s // 3600}h",
    21600: lambda s: f"{s // 3600}h",
    43200: lambda s: f"{s // 43200}d",
    86400: lambda s: f"{s // 86400}d",
}


def infer_timeframe(bars: tuple[Bar, ...]) -> str:
    """Return a timeframe string inferred from the median bar-to-bar gap.

    Falls back to ``"1d"`` when there are fewer than two bars or timestamps
    do not parse.
    """
    if len(bars) < 2:
        return "1d"
    gaps = _median_gaps(bars)
    if not gaps:
        return "1d"
    median_gap = median(gaps)
    rounded = min(_SECONDS_LADDER, key=lambda step: abs(step - median_gap))
    return _format_seconds(rounded)


def _median_gaps(bars: tuple[Bar, ...]) -> list[float]:
    times = _parse_times(bars)
    if len(times) < 2:
        return []
    return [t2 - t1 for t1, t2 in zip(times, times[1:], strict=False) if t2 > t1]


def _parse_times(bars: tuple[Bar, ...]) -> list[float]:
    times: list[float] = []
    for bar in bars[: _INFERENCE_SAMPLE + 1]:
        dt = _parse_ts(bar.timestamp)
        if dt is not None:
            times.append(dt.timestamp())
    return times


def _parse_ts(timestamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat(timestamp)
    except ValueError:
        return None


def _format_seconds(seconds: int) -> str:
    fmt = _FORMATTERS.get(seconds)
    if fmt is not None:
        return fmt(seconds)
    return f"{seconds // 86400}d"
