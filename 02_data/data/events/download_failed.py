"""DownloadFailed — a download run failed."""

from dataclasses import dataclass

from core.events.event import Event


@dataclass(frozen=True)
class DownloadFailed(Event):
    """The download failed (auth, unknown symbol, rate limit, lock, cancel)."""

    symbol: str
    interval: str
    reason: str
