"""DownloadCoverage — real coverage state derived from a candle DB scan."""

from dataclasses import dataclass

from core.events.event import Event


@dataclass(frozen=True)
class DownloadCoverage(Event):
    """Coverage facts for one symbol, derived from its database.

    ``state`` is the DLState name ("NOT_STARTED", "PARTIAL_DOWNLOAD",
    "DOWNLOAD_COMPLETE"). Dates are ``YYYY-MM-DD HH:MM:SS`` strings.
    """

    symbol: str
    interval: str
    state: str
    earliest: str | None
    latest: str | None
    row_count: int
    trading_days: int
    coverage_pct: float
    missing_head: bool
    missing_tail: bool
    listing_start_verified: bool
