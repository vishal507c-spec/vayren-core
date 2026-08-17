"""DownloadProgress — real per-chunk progress from the engine."""

from dataclasses import dataclass

from core.events.event import Event


@dataclass(frozen=True)
class DownloadProgress(Event):
    """One chunk finished: real row counts from the engine, no fabricated
    progress."""

    symbol: str
    interval: str
    chunk: int
    total_chunks: int
    chunk_start: str
    chunk_end: str
    new_rows: int
    db_total: int
