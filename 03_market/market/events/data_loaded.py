"""DataLoaded — candles have been loaded from the database."""

from dataclasses import dataclass

from core.events.event import Event

from market.models.bar import Bar


@dataclass(frozen=True)
class DataLoaded(Event):
    """Candles for a symbol were loaded successfully."""

    symbol: str
    bars: tuple[Bar, ...]
