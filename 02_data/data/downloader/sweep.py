"""forward_sweep — chunked download driver (preserved).

Chunks the sweep range into CHUNK_DAYS windows, upserts each chunk
immediately, reports real progress through the reporter, and detects
all-chunks-zero head sweeps for the LISTING_START boundary.

Integration change: a ``should_abort`` callback is checked between chunks so
the UI can cancel a long download; behaviour is identical when nothing
aborts. The data call is a ``fetch_chunk(start, end)`` callable returning
normalized candles or a contract sentinel — the engine wires it to the
provider, so no broker token/interval id reaches this layer.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from datetime import datetime

from data.native_download import chunk_count, chunk_windows, head_sweep_eligible
from data.provider.contract import RATE_LIMITED, TOKEN_EXPIRED

log = logging.getLogger("HistDownloadEngine")


def forward_sweep(
    fetch_chunk: Callable[[datetime, datetime], list[dict] | object],
    candle_db,
    symbol: str,
    interval: str,
    sweep_start: datetime,
    sweep_end: datetime,
    settings,
    reporter=None,
    is_head_sweep: bool = False,
    should_abort: Callable[[], bool] | None = None,
) -> tuple[bool, int, bool]:
    """Returns (ok, total_new_candles, all_chunks_zero).

    all_chunks_zero is True only when is_head_sweep=True and every chunk
    returned 0 new candles AND the earliest candle in DB did not change.
    The caller uses this to write the LISTING_START boundary.
    """
    # The kernel plans the sweep; this loop only drives it.
    total_chunks = chunk_count(sweep_start, sweep_end, settings.chunk_days)
    windows = chunk_windows(sweep_start, sweep_end, settings.chunk_days)

    # Record earliest before sweep (used for boundary verification)
    earliest_before: datetime | None = None
    if is_head_sweep:
        earliest_before = candle_db.earliest()

    total_new = 0

    for n, (chunk, end) in enumerate(windows, start=1):
        if should_abort is not None and should_abort():
            log.info(f"[Sweep] {symbol}/{interval}: aborted by user.")
            return False, total_new, False

        result = fetch_chunk(chunk, end)

        if result is TOKEN_EXPIRED or result is RATE_LIMITED:
            return False, total_new, False

        new = 0
        if result:
            new = candle_db.upsert(result)
            total_new += new
            log.info(f"  [{symbol}] chunk {n}: {chunk.date()}→{end.date()} +{new} rows")

        db_total = candle_db.count()
        if reporter is not None:
            reporter.on_chunk(
                symbol=symbol,
                interval=interval,
                chunk=n,
                total_chunks=total_chunks,
                chunk_start=chunk,
                chunk_end=end,
                new_rows=new,
                db_total=db_total,
            )

        _jitter_sleep(settings.chunk_delay_min, settings.chunk_delay_max)

    # Determine whether the entire head sweep yielded nothing
    all_chunks_zero = is_head_sweep and head_sweep_eligible(
        total_new, earliest_before, candle_db.earliest()
    )
    if all_chunks_zero:
        if earliest_before is None:
            log.info(
                f"[Sweep] {symbol}/{interval}: head sweep returned 0 new candles "
                "and the database has no candles. Eligible for LISTING_START boundary."
            )
        else:
            log.info(
                f"[Sweep] {symbol}/{interval}: head sweep returned 0 new candles. "
                f"Earliest unchanged at {earliest_before.date()}. "
                "Eligible for LISTING_START boundary."
            )

    return True, total_new, all_chunks_zero


def _jitter_sleep(lo: float, hi: float) -> None:
    if hi > 0:
        time.sleep(random.uniform(lo, hi))
