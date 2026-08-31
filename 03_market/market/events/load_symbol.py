"""LoadSymbol — request to load candles for a symbol."""

from dataclasses import dataclass

from core.events.event import Event


@dataclass(frozen=True)
class LoadSymbol(Event):
    """Request to load candles for a symbol.

    ``limit`` is optional: ``None`` (the default) requests the entire
    available history.
    """

    symbol: str
    limit: int | None = None
