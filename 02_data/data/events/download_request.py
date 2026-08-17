"""DownloadRequest — request to download a symbol/interval/date range."""

from dataclasses import dataclass

from core.events.event import Event


@dataclass(frozen=True)
class DownloadRequest(Event):
    """Request to download historical candles.

    Dates are ``YYYY-MM-DD`` strings (the engine treats them as the start of
    the day and the end of the day respectively).
    """

    symbol: str
    interval: str
    from_date: str
    to_date: str
