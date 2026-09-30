"""OHLCV bar payload with fail-closed construction-time validation."""

from __future__ import annotations

import math
import numbers
from dataclasses import dataclass
from datetime import datetime

# No cycle: market.native_timeframe only depends on core.native.loader, so the
# TIMEFRAME ladder is safe to consult from inside the payload module.
from market.native_timeframe import seconds_of


def _is_number(value: object) -> bool:
    """Real numbers only — bools are not prices, complex has no order."""
    return isinstance(value, numbers.Real) and not isinstance(value, bool)


@dataclass(frozen=True)
class Bar:
    """OHLCV bar data point.

    Represents aggregated market data for a symbol over a time period. Pure
    payload: the derived candle metrics live in the Rust market kernel and
    reach callers through `market.native_bar`.

    Construction is fail-closed: every field is validated in ``__post_init__``
    and any violation raises ``ValueError`` (never a half-built bar).
    """

    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    timestamp: str
    bar_size: str = "1d"
    vwap: float | None = None
    trades: int | None = None
    source: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError(f"symbol must be a non-empty string, got {self.symbol!r}")
        for field in ("open", "high", "low", "close"):
            value = getattr(self, field)
            if not _is_number(value):
                raise ValueError(f"{field} must be a real number, got {value!r}")
            if not math.isfinite(float(value)):
                raise ValueError(f"{field} must be finite, got {value!r}")
            if float(value) < 0:
                raise ValueError(f"{field} must be >= 0, got {value!r}")
        if float(self.high) < float(self.low):
            raise ValueError(f"high ({self.high}) must be >= low ({self.low})")
        for field in ("open", "close"):
            value = float(getattr(self, field))
            if not (float(self.low) <= value <= float(self.high)):
                raise ValueError(
                    f"{field} ({value}) must lie in [low, high] ([{self.low}, {self.high}])"
                )
        if isinstance(self.volume, bool) or not isinstance(self.volume, int):
            raise ValueError(f"volume must be an int, got {self.volume!r}")
        if self.volume < 0:
            raise ValueError(f"volume must be >= 0, got {self.volume!r}")
        if self.trades is not None:
            if isinstance(self.trades, bool) or not isinstance(self.trades, int):
                raise ValueError(f"trades must be an int or None, got {self.trades!r}")
            if self.trades < 0:
                raise ValueError(f"trades must be >= 0, got {self.trades!r}")
        if self.vwap is not None:
            if not _is_number(self.vwap):
                raise ValueError(f"vwap must be a real number or None, got {self.vwap!r}")
            if not math.isfinite(float(self.vwap)):
                raise ValueError(f"vwap must be finite, got {self.vwap!r}")
            if float(self.vwap) < 0:
                raise ValueError(f"vwap must be >= 0, got {self.vwap!r}")
        if not isinstance(self.timestamp, str):
            raise ValueError(f"timestamp must be a string, got {self.timestamp!r}")
        try:
            datetime.fromisoformat(self.timestamp)
        except ValueError as exc:
            raise ValueError(f"timestamp is not ISO-8601 parseable: {self.timestamp!r}") from exc
        if not isinstance(self.bar_size, str) or seconds_of(self.bar_size) is None:
            # seconds_of is the single TIMEFRAME authority: ladder match is
            # case-insensitive ("1d" == "1D") and generated grammar ("90m",
            # "2D") parses too; None means the kernel knows no such label.
            raise ValueError(f"bar_size is not a known TIMEFRAME label: {self.bar_size!r}")
        if not isinstance(self.source, str):
            raise ValueError(f"source must be a string, got {self.source!r}")
