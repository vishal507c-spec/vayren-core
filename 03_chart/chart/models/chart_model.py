"""ChartModel — immutable, chart-ready candle data."""

from dataclasses import dataclass

from market.models.bar import Bar


@dataclass(frozen=True)
class ChartModel:
    """Pure data prepared for rendering: candles, ascending by timestamp.

    Contains no viewport state — the widget owns the viewport.
    """

    symbol: str
    bars: tuple[Bar, ...]
