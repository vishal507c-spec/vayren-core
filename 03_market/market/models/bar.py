from dataclasses import dataclass


@dataclass(frozen=True)
class Bar:
    """OHLCV bar data point.

    Represents aggregated market data for a symbol over a time period.
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

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def typical_price(self) -> float:
        return (self.high + self.low + self.close) / 3.0

    @property
    def midpoint(self) -> float:
        return (self.high + self.low) / 2.0

    @property
    def is_bullish(self) -> bool:
        return self.close >= self.open

    @property
    def return_pct(self) -> float:
        if self.open == 0:
            return 0.0
        return ((self.close - self.open) / self.open) * 100.0
