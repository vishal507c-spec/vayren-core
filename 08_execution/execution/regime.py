"""Market regimes — pluggable interface, deterministic default.

No ML dependency: the statistical detector uses closed-form rolling
statistics only. Strategies consume regimes through the interface, never
the implementation.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class MarketRegime(Enum):
    TREND = "TREND"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    ILLIQUID = "ILLIQUID"
    UNKNOWN = "UNKNOWN"


class RegimeDetector(Protocol):
    """One-bar-at-a-time regime classification."""

    def update(self, close: float, volume: float) -> MarketRegime: ...


@dataclass
class StatisticalRegimeDetector:
    """Deterministic detector: SMA-slope trend + stdev-volatility bands.

    - needs `lookback` closes before leaving UNKNOWN
    - |slope| above `trend_threshold` (fraction of price) → TREND else RANGE
    - stdev/mean above `high_vol_threshold` → HIGH_VOLATILITY (overrides)
    - stdev/mean below `low_vol_threshold` → LOW_VOLATILITY (overrides)
    - median volume below `min_volume` → ILLIQUID (overrides all)
    """

    lookback: int = 30
    trend_threshold: float = 0.001
    high_vol_threshold: float = 0.02
    low_vol_threshold: float = 0.003
    min_volume: float = 1.0

    def __post_init__(self) -> None:
        self._closes: deque[float] = deque(maxlen=self.lookback)
        self._volumes: deque[float] = deque(maxlen=self.lookback)

    def update(self, close: float, volume: float) -> MarketRegime:
        self._closes.append(float(close))
        self._volumes.append(float(volume))
        if len(self._closes) < self.lookback:
            return MarketRegime.UNKNOWN
        closes = list(self._closes)
        volumes = sorted(self._volumes)
        if volumes[len(volumes) // 2] < self.min_volume:
            return MarketRegime.ILLIQUID
        mean = sum(closes) / len(closes)
        if mean <= 0:
            return MarketRegime.UNKNOWN
        var = sum((c - mean) ** 2 for c in closes) / len(closes)
        vol = (var**0.5) / mean
        if vol >= self.high_vol_threshold:
            return MarketRegime.HIGH_VOLATILITY
        if vol <= self.low_vol_threshold:
            return MarketRegime.LOW_VOLATILITY
        first = sum(closes[: len(closes) // 2]) / (len(closes) // 2)
        second = sum(closes[len(closes) // 2 :]) / (len(closes) - len(closes) // 2)
        slope = (second - first) / mean
        if abs(slope) >= self.trend_threshold:
            return MarketRegime.TREND
        return MarketRegime.RANGE
