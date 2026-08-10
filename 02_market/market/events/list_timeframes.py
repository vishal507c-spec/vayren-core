"""ListTimeframes — request to detect the timeframes available for a symbol."""

from dataclasses import dataclass

from core.events.event import Event


@dataclass(frozen=True)
class ListTimeframes(Event):
    """Request to detect which timeframes exist in the symbol's database."""

    symbol: str
