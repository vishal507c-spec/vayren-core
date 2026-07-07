from typing import Any

from strategies.models.strategy import Strategy, StrategyMetadata
from strategies.events.order_requested import OrderRequested


class MeanReversionStrategy(Strategy):
    """Mean reversion strategy - buys when RSI < threshold."""

    def __init__(self, rsi_threshold: float = 30.0, shares: int = 100) -> None:
        self._rsi_threshold = rsi_threshold
        self._shares = shares
        self._metadata = StrategyMetadata(
            name="mean_reversion", version="1.0.0", description="Mean reversion strategy",
            tags=["mean-reversion", "rsi", "contrarian"], params={"rsi_threshold": rsi_threshold, "shares": shares},
        )

    @property
    def name(self) -> str:
        return self._metadata.name

    def should_enter(self, context: Any) -> bool:
        return getattr(context, "signal_value", 100) < self._rsi_threshold

    def should_exit(self, context: Any) -> bool:
        return getattr(context, "signal_value", 0) > 70.0

    def generate_orders(self, context: Any) -> list[OrderRequested]:
        if self.should_enter(context):
            return [OrderRequested(symbol=getattr(context, "symbol", ""), side="buy", quantity=self._shares, strategy=self.name, reason="mean_reversion_entry")]
        return []
