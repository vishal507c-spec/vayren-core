"""ChartModel — immutable, chart-ready candle data."""

from dataclasses import dataclass

from market.models.bar import Bar


@dataclass(frozen=True)
class ChartModel:
    """Pure data prepared for rendering: candles, ascending by timestamp.

    Contains no viewport state — the widget owns the viewport.

    Attributes:
        symbol: The traded symbol (e.g. "TCS").
        bars: Candles sorted ascending by timestamp.
        timeframe: Human-readable candle period derived from the bars
            (e.g. "30m", "1d"). Populated by ChartEngine at model time.
        exchange: The exchange the data originates from (e.g. "NSE").
    """

    symbol: str
    bars: tuple[Bar, ...]
    timeframe: str
    exchange: str
