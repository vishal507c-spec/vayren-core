from datetime import date, datetime
from typing import Optional

from market.models.bar import Bar


class MarketDataQuery:
    """Query interface for historical and current market data."""

    def __init__(self) -> None:
        self._bars: dict[str, list[Bar]] = {}

    def add_bars(self, symbol: str, bars: list[Bar]) -> None:
        if symbol not in self._bars:
            self._bars[symbol] = []
        self._bars[symbol].extend(bars)

    def get_bars(self, symbol: str, start: Optional[str] = None, end: Optional[str] = None, limit: int = 100) -> list[Bar]:
        bars = self._bars.get(symbol, [])
        result = bars[-limit:] if limit else bars
        if start:
            result = [b for b in result if b.timestamp >= start]
        if end:
            result = [b for b in result if b.timestamp <= end]
        return result

    def latest_bar(self, symbol: str) -> Optional[Bar]:
        bars = self._bars.get(symbol, [])
        return bars[-1] if bars else None
