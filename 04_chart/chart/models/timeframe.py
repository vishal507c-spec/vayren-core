"""Timeframe inference — derive a human-readable candle period from bar spacing.

Given a sequence of bars, this infers the most likely timeframe string
(e.g. "1D", "30m", "1h") from the dominant gap between consecutive candles.
The dominant (most common) gap is computed over a sampled prefix of the
series (the first ``_INFERENCE_SAMPLE`` gaps) — robust against overnight
and weekend gaps that skew the median for sparse intraday timeframes
(e.g. 4h). This is a pure-function helper with no Qt, no SQL, no events.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import datetime

from market.models.bar import Bar

_INFERENCE_SAMPLE = 2048

_SECONDS_LADDER = (
    60,
    180,
    300,
    900,
    1800,
    2700,
    3600,
    7200,
    10800,
    14400,
    21600,
    43200,
    86400,
    604800,
)

_FORMATTERS: dict[int, Callable[[int], str]] = {
    60: lambda s: f"{s // 60}m",
    180: lambda s: f"{s // 60}m",
    300: lambda s: f"{s // 60}m",
    900: lambda s: f"{s // 60}m",
    1800: lambda s: f"{s // 60}m",
    2700: lambda s: f"{s // 60}m",
    3600: lambda s: f"{s // 3600}h",
    7200: lambda s: f"{s // 3600}h",
    10800: lambda s: f"{s // 3600}h",
    14400: lambda s: f"{s // 3600}h",
    21600: lambda s: f"{s // 3600}h",
    43200: lambda s: f"{s // 3600}h",
    86400: lambda s: f"{s // 86400}D",
    604800: lambda _: "1W",
}


def infer_timeframe(bars: tuple[Bar, ...]) -> str:
    """Return a timeframe string inferred from the dominant bar-to-bar gap.

    Uses the most common gap (mode) to be robust against overnight/weekend
    gaps that skew the median for sparse intraday timeframes (e.g. 4h has
    2 bars/day, median of [14400,72000] would be 43200 -> "1D" bug). Falls
    back to ``"1D"`` when there are fewer than two bars or timestamps
    do not parse.
    """
    if len(bars) < 2:
        return "1D"
    gaps = _gaps(bars)
    if not gaps:
        return "1D"
    counter = Counter(gaps)
    most_common = counter.most_common()
    max_count = most_common[0][1]
    candidates = [gap for gap, cnt in most_common if cnt == max_count]
    mode_gap = min(candidates)
    rounded = min(_SECONDS_LADDER, key=lambda step: abs(step - mode_gap))
    return _format_seconds(rounded)


def _gaps(bars: tuple[Bar, ...]) -> list[float]:
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
    return f"{seconds // 86400}D"
