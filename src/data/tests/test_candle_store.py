"""Candle store writer: the store must hold what the venue actually said."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from data.candle_store import latest_candle, store_path, write_candles
from data.native_download import candle_time_for


def _epoch(day: str, hour: int, minute: int) -> int:
    """A venue-local bar time, as a provider epoch (the realistic shape)."""
    ist = timezone(timedelta(hours=5, minutes=30))
    return int(
        datetime.strptime(f"{day} {hour:02d}:{minute:02d}:00", "%Y-%m-%d %H:%M:%S")
        .replace(tzinfo=ist)
        .timestamp()
    )


def _bar(day: str, hour: int, minute: int, close: float, o: float | None = None) -> tuple:
    return (
        _epoch(day, hour, minute),
        o if o is not None else close,
        close + 5.0,
        close - 5.0,
        close,
        1000,
    )


def test_writes_a_readable_candle_file(tmp_path: Path) -> None:
    rows = [_bar("2026-10-13", 9, 15, 100.0), _bar("2026-10-13", 9, 45, 101.0)]
    report = write_candles(tmp_path, "KAYNES", rows)

    assert report.written == 2
    assert report.rejected == 0
    assert report.duplicates == 0
    assert report.first < report.last

    latest = latest_candle(tmp_path, "KAYNES")
    assert latest is not None
    assert latest["close"] == 101.0
    assert latest["candle_time"] == "2026-10-13 09:45:00"


def test_symbol_file_matches_the_reader_naming_rule(tmp_path: Path) -> None:
    assert store_path(tmp_path, "KAYNES").name == "KAYNES.db"
    assert store_path(tmp_path, "sbin").name == "SBIN.db"


def test_invalid_bars_are_rejected_never_repaired(tmp_path: Path) -> None:
    good = _bar("2026-10-13", 9, 15, 100.0)
    inverted_high = (good[0], 100.0, 90.0, 95.0, 100.0, 10)  # high < low
    zero_close = (good[0], 1.0, 2.0, 0.5, 0.0, 10)
    negative_open = (good[0], -1.0, 2.0, 0.5, 1.0, 10)

    # A malformed (short) row is deliberately typed as a row here: the writer
    # must tolerate and reject it, since the venue is the source of these
    # shapes and this test proves it does not crash on one.
    malformed = [(good[0], 1.0, 2.0)]

    report = write_candles(
        tmp_path, "KAYNES", [good, inverted_high, zero_close, negative_open, *malformed]
    )

    assert report.written == 1
    assert report.rejected == 4
    assert report.rejected == 4
    latest = latest_candle(tmp_path, "KAYNES")
    assert latest is not None
    assert latest["close"] == 100.0


def test_repeated_bar_keeps_the_latest_venue_statement_and_counts_it(tmp_path: Path) -> None:
    first = _bar("2026-10-13", 9, 15, 100.0, o=95.0)
    corrected = _bar("2026-10-13", 9, 15, 100.0, o=96.5)

    report = write_candles(tmp_path, "KAYNES", [first, corrected])

    assert report.written == 1
    assert report.duplicates == 1
    latest = latest_candle(tmp_path, "KAYNES")
    assert latest is not None
    assert latest["open"] == 96.5


def test_rewriting_is_idempotent(tmp_path: Path) -> None:
    rows = [_bar("2026-10-13", 9, 15, 100.0), _bar("2026-10-13", 9, 45, 101.0)]
    write_candles(tmp_path, "KAYNES", rows)
    write_candles(tmp_path, "KAYNES", rows)

    con = sqlite3.connect(str(store_path(tmp_path, "KAYNES")))
    try:
        count = con.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
    finally:
        con.close()
    assert count == 2


def test_empty_input_writes_no_file(tmp_path: Path) -> None:
    report = write_candles(tmp_path, "KAYNES", [])
    assert report.written == 0
    assert not store_path(tmp_path, "KAYNES").exists()
    assert latest_candle(tmp_path, "KAYNES") is None


def test_unknown_symbol_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        store_path(tmp_path, ":::")


def test_stamps_are_venue_wall_clock_not_utc(tmp_path: Path) -> None:
    """A provider epoch must land on the venue's local bar, not a UTC one.

    15:15 IST is 09:45 UTC. Storing the UTC reading shifts every bar by 5h30m
    and silently misaligns the strategy's session windows, so this pins the
    offset rather than trusting it.
    """
    ist_close = int(
        datetime(2026, 10, 13, 15, 15, tzinfo=timezone(timedelta(hours=5, minutes=30))).timestamp()
    )
    write_candles(tmp_path, "KAYNES", [(ist_close, 100.0, 105.0, 95.0, 101.0, 10)])

    latest = latest_candle(tmp_path, "KAYNES")
    assert latest is not None
    assert latest["candle_time"] == "2026-10-13 15:15:00"
    assert latest["candle_time"] != "2026-10-13 09:45:00"


def test_session_bounds_survive_the_round_trip(tmp_path: Path) -> None:
    """A full session must read 09:15 -> 15:15 in venue time."""
    ist = timezone(timedelta(hours=5, minutes=30))
    rows = [
        (int(datetime(2026, 10, 13, h, m, tzinfo=ist).timestamp()), 100.0, 105.0, 95.0, 101.0, 1)
        for h, m in ((9, 15), (12, 45), (15, 15))
    ]
    report = write_candles(tmp_path, "KAYNES", rows)
    assert report.first == "2026-10-13 09:15:00"
    assert report.last == "2026-10-13 15:15:00"


def test_keys_come_from_the_rust_kernel_not_python(tmp_path: Path) -> None:
    """A key shape that drifts from the kernel's would be undiscoverable."""
    rows = [_bar("2026-10-13", 9, 15, 100.0)]
    write_candles(tmp_path, "KAYNES", rows)

    con = sqlite3.connect(str(store_path(tmp_path, "KAYNES")))
    try:
        stored = con.execute("SELECT candle_time FROM ohlcv").fetchone()[0]
    finally:
        con.close()
    assert stored == candle_time_for(
        datetime.fromtimestamp(rows[0][0], timezone(timedelta(hours=5, minutes=30))).replace(
            tzinfo=None
        )
    )
