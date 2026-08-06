from dataclasses import dataclass
from market.models.trade import Trade


@dataclass(frozen=True)
class TradeReceived:
    """Emitted when a new trade is ingested."""

    trade: Trade
    source: str = ""
