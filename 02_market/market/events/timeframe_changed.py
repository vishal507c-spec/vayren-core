"""TimeframeChanged — request to load a symbol at a given timeframe."""

from dataclasses import dataclass

from core.events.event import Event


@dataclass(frozen=True)
class TimeframeChanged(Event):
    """Request to load candles for a symbol at a timeframe.

    ``limit`` is optional: ``None`` (the default) requests the entire
    available history.
    """

    symbol: str
    timeframe: str
    limit: int | None = None
