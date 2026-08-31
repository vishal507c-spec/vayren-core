"""SqliteCandleDatabase tests."""

import sqlite3
from pathlib import Path

import pytest

from market.database.sqlite import SqliteCandleDatabase


def test_connect_missing_file_raises(tmp_path: Path) -> None:
    database = SqliteCandleDatabase(tmp_path / "missing.db")
    with pytest.raises(FileNotFoundError):
        database.connect()


def test_connect_missing_table_raises(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()
    database = SqliteCandleDatabase(path)
    with pytest.raises(RuntimeError, match="no 'candles' table"):
        database.connect()


def test_fetch_requires_connection(candle_database_path: Path) -> None:
    database = SqliteCandleDatabase(candle_database_path)
    with pytest.raises(RuntimeError, match="not connected"):
        database.fetch_candles("SPY", 10)


def test_fetch_returns_ascending_most_recent(candle_database_path: Path) -> None:
    database = SqliteCandleDatabase(candle_database_path)
    database.connect()
    try:
        rows = database.fetch_candles("SPY", 5)
    finally:
        database.close()
    timestamps = [row["timestamp"] for row in rows]
    assert timestamps == ["2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-10"]
    assert rows[0]["symbol"] == "SPY"
    assert rows[0]["volume"] == 1006


def test_fetch_unknown_symbol_returns_empty(candle_database_path: Path) -> None:
    database = SqliteCandleDatabase(candle_database_path)
    database.connect()
    try:
        rows = database.fetch_candles("UNKNOWN", 10)
    finally:
        database.close()
    assert rows == []


def test_fetch_limit_capped(candle_database_path: Path) -> None:
    database = SqliteCandleDatabase(candle_database_path)
    database.connect()
    try:
        rows = database.fetch_candles("SPY", 3)
    finally:
        database.close()
    assert len(rows) == 3
    assert rows[0]["timestamp"] == "2026-01-08"


def test_close_is_idempotent(candle_database_path: Path) -> None:
    database = SqliteCandleDatabase(candle_database_path)
    database.connect()
    database.close()
    database.close()
