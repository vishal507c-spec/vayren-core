"""Indicator helpers for native Python strategies — pure Python, no DSL."""

from __future__ import annotations

from collections import deque
from itertools import islice


def _require_period(period: int, name: str) -> int:
    if isinstance(period, bool) or not isinstance(period, int):
        raise ValueError(f"{name} period must be an int >= 1, got {period!r}")
    if period < 1:
        raise ValueError(f"{name} period must be >= 1, got {period!r}")
    return period


def calc_rsi(closes: deque[float], period: int = 14) -> float:
    """RSI over `closes` using a simple average of gains/losses (not Wilder's smoothing).

    Kept as a simple average deliberately: callers (RSI mean-reversion) depend
    on this exact formulation, so it is documented here rather than renamed.
    """
    _require_period(period, "RSI")
    if len(closes) < period + 1:
        return 50.0
    vals = list(closes)[-period - 1 :]
    gains = sum(max(vals[i] - vals[i - 1], 0) for i in range(1, len(vals)))
    losses = sum(max(vals[i - 1] - vals[i], 0) for i in range(1, len(vals)))
    if losses == 0:
        return 100.0
    rs = gains / losses if losses else 0
    return 100 - (100 / (1 + rs))


def calc_sma(closes: deque[float], period: int = 14) -> float:
    _require_period(period, "SMA")
    if len(closes) < period:
        return closes[-1] if closes else 0.0
    return sum(islice(reversed(closes), period)) / period


def calc_range(highs: deque[float], lows: deque[float], period: int = 14) -> float:
    _require_period(period, "range")
    if len(highs) < period or len(lows) < period:
        return (highs[-1] - lows[-1]) if highs and lows else 0.0
    return max(islice(reversed(highs), period)) - min(islice(reversed(lows), period))
