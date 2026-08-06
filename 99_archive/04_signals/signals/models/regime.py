from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Regime:
    """Market regime classification."""

    name: str
    value: float
    timestamp: str
    symbol: str = ""
    volatility: Optional[float] = None
    trend: Optional[str] = None
    confidence: float = 1.0

    @property
    def is_bull(self) -> bool:
        return self.trend == "bullish"

    @property
    def is_bear(self) -> bool:
        return self.trend == "bearish"

    @property
    def is_sideways(self) -> bool:
        return self.trend in ("neutral", "sideways", None)
