from typing import Any, Optional

from market.models.bar import Bar
from market.models.trade import Trade


class MarketDataIngestion:
    """Ingests raw market data and normalizes into domain models."""

    def normalize_bar(self, raw: dict[str, Any], source: str = "") -> Bar:
        """Convert raw API response into Bar model."""
        return Bar(
            symbol=raw.get("symbol", raw.get("S", "")),
            open=float(raw.get("open", raw.get("o", 0))),
            high=float(raw.get("high", raw.get("h", 0))),
            low=float(raw.get("low", raw.get("l", 0))),
            close=float(raw.get("close", raw.get("c", 0))),
            volume=int(raw.get("volume", raw.get("v", 0))),
            timestamp=str(raw.get("timestamp", raw.get("t", ""))),
            source=source,
        )

    def normalize_trade(self, raw: dict[str, Any], source: str = "") -> Trade:
        return Trade(
            symbol=raw.get("symbol", raw.get("S", "")),
            price=float(raw.get("price", raw.get("p", 0))),
            size=int(raw.get("size", raw.get("s", 0))),
            timestamp=str(raw.get("timestamp", raw.get("t", ""))),
            exchange=raw.get("exchange", raw.get("x", "")),
            trade_id=str(raw.get("trade_id", raw.get("i", ""))),
        )
