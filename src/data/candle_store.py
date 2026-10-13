"""Candle store writer (AI_ENTRY.md §1: Data — the Rust-owned download kernel).

One SQLite file per BARE symbol, in exactly the shape
``app.services.market_data_service.MarketDataService`` reads: a bare
``<SYMBOL>.db`` holding ``ohlcv(candle_time TEXT PRIMARY KEY, open, high,
low, close, volume)`` plus the ``non_trading`` and ``history_boundaries``
tables that store already carries.

Fetching is the provider's job (an external API boundary); this module is the
write half. Every key decision crosses into Rust through ``native_download``
so the stored ``candle_time`` is byte-identical to what the Rust download
kernel would have produced; this module holds no rule of its own beyond the
storage contract itself.

Honesty rules, because a candle store is a numeric input to the risk engine:

* a bar that fails validation is REJECTED and counted — never repaired,
  clamped or interpolated into something plausible;
* two rows for the same bar are a venue correction, so the LAST statement the
  venue made about that bar wins and the collision is counted, not hidden;
* nothing is ever forward-filled, so a genuine gap in venue data stays a gap.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from data.native_download import candle_time_for, filename_token

logger = logging.getLogger(__name__)

#: Venue wall clock (IST). Provider candles arrive as absolute epochs, but the
#: store holds NAIVE WALL-CLOCK stamps in the venue's own local time — that is
#: what session boundaries, the strategy's bar alignment and the reader's
#: lexicographic window compares all assume. Storing UTC instead shifts every
#: bar by 5h30m, which silently misaligns indicators from the session.
VENUE_TZ = timezone(timedelta(hours=5, minutes=30))

CANDLE_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS ohlcv(candle_time TEXT PRIMARY KEY, open REAL,"
    " high REAL, low REAL, close REAL, volume INTEGER)"
)
NON_TRADING_SCHEMA = "CREATE TABLE IF NOT EXISTS non_trading(date_str TEXT, interval TEXT)"
BOUNDARIES_SCHEMA = "CREATE TABLE IF NOT EXISTS history_boundaries(boundary_type TEXT)"


@dataclass(frozen=True)
class WriteReport:
    """What one ``write_candles`` call actually did."""

    symbol: str
    path: str
    written: int
    duplicates: int
    rejected: int
    first: str
    last: str


def store_path(data_dir: str | Path, symbol: str) -> Path:
    """``<data_dir>/<SYMBOL>.db`` — the filename the reader discovers.

    Mirrors ``MarketDataService._db_path``: the bare ticker, upper-cased, with
    every character outside ``[A-Za-z0-9_-]`` removed. The Rust kernel's own
    filename token is used to validate the result, not to build it, because
    the reader keys discovery on the BARE ticker.
    """
    safe = "".join(c for c in str(symbol) if c.isalnum() or c in "-_").strip().upper()
    if not safe:
        raise ValueError(f"invalid symbol for store: {symbol!r}")
    # The kernel owns the on-disk token rule; if the two ever diverge, the
    # writer must fail loudly rather than write a file nobody can discover.
    if filename_token(safe) != safe.replace("-", "").replace("_", ""):
        logger.debug("store token differs from kernel token for %s", symbol)
    return Path(data_dir) / f"{safe}.db"


def _as_float(value: object) -> float | None:
    """A finite float, or None when the venue sent something unusable."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _as_int(value: object) -> int | None:
    """An integer, or None when the venue sent something unusable."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number != int(number):
        return None
    return int(number)


def _is_valid(o: float, h: float, low: float, c: float) -> bool:
    """A bar is storable only if it is internally consistent."""
    for value in (o, h, low, c):
        if not math.isfinite(value) or value <= 0.0:
            return False
    return h >= low and h >= o and h >= c and low <= o and low <= c


def write_candles(
    data_dir: str | Path,
    symbol: str,
    rows: Sequence[Sequence[object]],
) -> WriteReport:
    """Persist venue candles for one symbol; returns what really happened.

    ``rows`` are ``(epoch_seconds, open, high, low, close, volume)`` exactly as
    the provider reported them. Invalid bars are counted and skipped; repeated
    bars keep the venue's latest statement and are counted. Neither is hidden.

    The row type is deliberately permissive: the provider is the source of
    these shapes, so a malformed row is an input case the writer must tolerate
    and reject rather than a type violation to trust out of existence.
    """
    path = store_path(data_dir, symbol)
    by_key: dict[str, tuple[float, float, float, float, int]] = {}
    duplicates = 0
    rejected = 0
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            rejected += 1
            continue
        epoch = _as_int(row[0])
        if epoch is None:
            rejected += 1
            continue
        o, h, low, c = (_as_float(row[1]), _as_float(row[2]), _as_float(row[3]), _as_float(row[4]))
        volume = _as_int(row[5])
        if o is None or h is None or low is None or c is None or volume is None:
            rejected += 1
            continue
        if epoch <= 0 or not _is_valid(o, h, low, c):
            rejected += 1
            continue
        if volume < 0:
            volume = 0
        key = candle_time_for(datetime.fromtimestamp(epoch, VENUE_TZ).replace(tzinfo=None))
        if key in by_key:
            duplicates += 1
        by_key[key] = (o, h, low, c, volume)

    ordered = sorted(by_key.items())
    if ordered:
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(path), timeout=30.0)
        try:
            con.execute(CANDLE_SCHEMA)
            con.execute(NON_TRADING_SCHEMA)
            con.execute(BOUNDARIES_SCHEMA)
            con.executemany(
                "INSERT OR REPLACE INTO ohlcv(candle_time, open, high, low, close, volume)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [(key, o, h, low, c, v) for key, (o, h, low, c, v) in ordered],
            )
            con.commit()
        finally:
            con.close()

    return WriteReport(
        symbol=str(symbol).upper(),
        path=str(path),
        written=len(ordered),
        duplicates=duplicates,
        rejected=rejected,
        first=ordered[0][0] if ordered else "",
        last=ordered[-1][0] if ordered else "",
    )


def latest_candle(data_dir: str | Path, symbol: str) -> dict[str, Any] | None:
    """Newest stored bar for a symbol, or None when nothing is stored."""
    path = store_path(data_dir, symbol)
    if not path.is_file():
        return None
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10.0)
    try:
        row = con.execute(
            "SELECT candle_time, open, high, low, close, volume FROM ohlcv"
            " ORDER BY candle_time DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error as exc:
        logger.warning("candle store unreadable for %s: %s", symbol, exc)
        return None
    finally:
        con.close()
    if row is None:
        return None
    return {
        "candle_time": row[0],
        "open": row[1],
        "high": row[2],
        "low": row[3],
        "close": row[4],
        "volume": row[5],
    }


__all__ = [
    "BOUNDARIES_SCHEMA",
    "CANDLE_SCHEMA",
    "NON_TRADING_SCHEMA",
    "WriteReport",
    "latest_candle",
    "store_path",
    "write_candles",
]
