"""PaperTradeRequested — request: start paper trading the active strategy."""

from dataclasses import dataclass

from core.events.event import Event


@dataclass(frozen=True)
class PaperTradeRequested(Event):
    """The user asked to paper trade; requires a live execution engine."""

    strategy_id: str
    symbol: str
    timeframe: str
