from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class OrderBookLevel:
    price: float
    size: float
    orders: int = 0


@dataclass(frozen=True)
class OrderBook:
    """Level 2 order book snapshot."""

    symbol: str
    timestamp: str
    bids: list[OrderBookLevel] = field(default_factory=list)
    asks: list[OrderBookLevel] = field(default_factory=list)

    @property
    def best_bid(self) -> Optional[OrderBookLevel]:
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> Optional[OrderBookLevel]:
        return self.asks[0] if self.asks else None

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return self.best_ask.price - self.best_bid.price
        return None

    @property
    def midpoint(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return (self.best_bid.price + self.best_ask.price) / 2.0
        return None

    @property
    def bid_volume(self) -> float:
        return sum(level.size for level in self.bids)

    @property
    def ask_volume(self) -> float:
        return sum(level.size for level in self.asks)
