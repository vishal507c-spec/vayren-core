"""DownloadCompleted — a download run finished successfully."""

from dataclasses import dataclass

from core.events.event import Event


@dataclass(frozen=True)
class DownloadCompleted(Event):
    """The download finished; counts are real database values."""

    symbol: str
    interval: str
    new_rows: int
    db_total: int
    trading_days: int
