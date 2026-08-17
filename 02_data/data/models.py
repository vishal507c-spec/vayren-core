"""Download state model — derived only, never stored.

Preserved from the original engine: download state is derived from a candle
database scan on every run. No progress files, no completion flags — candle
databases are the only source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class DLState(Enum):
    """Download-only states, derived from the candle DB scan.

    No audit / repair / verification states here — those belong to a
    separate gap-repair engine (out of scope for this domain).
    """

    NOT_STARTED = 1  # No .db file or file is empty
    PARTIAL_DOWNLOAD = 2  # Head or tail coverage missing
    DOWNLOAD_COMPLETE = 3  # Full historical range present


@dataclass
class SymbolInfo:
    symbol: str
    interval: str
    state: DLState
    earliest: datetime | None
    latest: datetime | None
    row_count: int
    trading_days: int
    missing_head: bool = False
    missing_tail: bool = False
    coverage_pct: float = 0.0
    listing_start_verified: bool = False  # True when LISTING_START boundary confirmed
