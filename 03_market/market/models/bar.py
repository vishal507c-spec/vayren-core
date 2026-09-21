from dataclasses import dataclass


@dataclass(frozen=True)
class Bar:
    """OHLCV bar data point.

    Represents aggregated market data for a symbol over a time period. Pure
    payload: the derived candle metrics live in the Rust market kernel and
    reach callers through `market.native_bar`.
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
