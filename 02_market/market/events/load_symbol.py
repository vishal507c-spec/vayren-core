"""LoadSymbol — request to load candles for a symbol."""

from dataclasses import dataclass

from core.events.event import Event


@dataclass(frozen=True)
class LoadSymbol(Event):
    """Request to load the most recent candles for a symbol."""

    symbol: str
    limit: int = 5000
