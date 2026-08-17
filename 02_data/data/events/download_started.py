"""DownloadStarted — a download run has begun."""

from dataclasses import dataclass

from core.events.event import Event


@dataclass(frozen=True)
class DownloadStarted(Event):
    """The engine started a download for one symbol/interval/range."""

    symbol: str
    interval: str
    from_date: str
    to_date: str
    total_chunks: int
