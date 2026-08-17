"""OhlcvCandleDatabase tests — the real Zerodha per-stock schema."""

import sqlite3
from pathlib import Path

import pytest

from market.database.ohlcv import OhlcvCandleDatabase
from market.tests.conftest import seed_ohlcv_database


def test_connect_missing_file_raises(tmp_path: Path) -> None:
    database = OhlcvCandleDatabase(tmp_path / "missing.db")
    with pytest.raises(FileNotFoundError):
        database.connect()


def test_connect_missing_ohlcv_table_raises(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()
    database = OhlcvCandleDatabase(path)
    with pytest.raises(RuntimeError, match="no 'ohlcv' table"):
        database.connect()


def test_fetch_requires_connection(ohlcv_database_path: Path) -> None:
    database = OhlcvCandleDatabase(ohlcv_database_path)
    with pytest.raises(RuntimeError, match="not connected"):
        database.fetch_candles("AMBUJACEM", 10)


def test_fetch_returns_ascending_most_recent(ohlcv_database_path: Path) -> None:
    database = OhlcvCandleDatabase(ohlcv_database_path)
    database.connect()
    try:
        rows = database.fetch_candles("AMBUJACEM", 5)
    finally:
        database.close()
    timestamps = [row["timestamp"] for row in rows]
    assert timestamps == [
        "2026-01-06 09:15:00",
        "2026-01-07 09:15:00",
        "2026-01-08 09:15:00",
        "2026-01-09 09:15:00",
        "2026-01-10 09:15:00",
    ]
    assert rows[0]["symbol"] == "AMBUJACEM"
    assert rows[0]["open"] == 106.0
    assert rows[0]["volume"] == 1006


def test_fetch_limit_capped(ohlcv_database_path: Path) -> None:
    database = OhlcvCandleDatabase(ohlcv_database_path)
    database.connect()
    try:
        rows = database.fetch_candles("AMBUJACEM", 3)
    finally:
        database.close()
    assert len(rows) == 3
    assert rows[0]["timestamp"] == "2026-01-08 09:15:00"


def test_fetch_all_history_when_limit_none(ohlcv_database_path: Path) -> None:
    database = OhlcvCandleDatabase(ohlcv_database_path)
    database.connect()
    try:
        rows = database.fetch_candles("AMBUJACEM", None)
    finally:
        database.close()
    assert [row["timestamp"] for row in rows] == [
        f"2026-01-{day:02d} 09:15:00" for day in range(1, 11)
    ]


def test_close_is_idempotent(ohlcv_database_path: Path) -> None:
    database = OhlcvCandleDatabase(ohlcv_database_path)
    database.connect()
    database.close()
    database.close()


def test_reads_real_file(tmp_path: Path) -> None:
    path = seed_ohlcv_database(tmp_path / "REALSTOCK.db", count=200)
    database = OhlcvCandleDatabase(path)
    database.connect()
    try:
        rows = database.fetch_candles("REALSTOCK", 200)
    finally:
        database.close()
    assert len(rows) == 200
    assert rows[0]["symbol"] == "REALSTOCK"
