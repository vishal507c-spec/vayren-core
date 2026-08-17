"""ListSymbols — request to list every available stock symbol."""

from dataclasses import dataclass

from core.events.event import Event


@dataclass(frozen=True)
class ListSymbols(Event):
    """Request to scan the data directory for all stock symbols."""
