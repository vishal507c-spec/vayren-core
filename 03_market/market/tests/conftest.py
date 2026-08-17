"""Shared fixtures for market tests."""

import sqlite3
from pathlib import Path

import pytest

SCHEMA = """
CREATE TABLE candles (
    symbol    TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    open      REAL NOT NULL,
    high      REAL NOT NULL,
    low       REAL NOT NULL,
    close     REAL NOT NULL,
    volume    INTEGER NOT NULL
);
CREATE INDEX idx_candles_symbol_timestamp ON candles (symbol, timestamp);
"""

OHLCV_SCHEMA = """
CREATE TABLE ohlcv (
    candle_time TEXT PRIMARY KEY,
    open        REAL NOT NULL,
    high        REAL NOT NULL,
    low         REAL NOT NULL,
    close       REAL NOT NULL,
    volume      INTEGER NOT NULL
);
"""


def seed_database(path: Path, symbol: str = "SPY", count: int = 10) -> Path:
    """Create a test candle database with `count` ascending daily bars."""
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA)
        rows = [
            (
                symbol,
                f"2026-01-{day:02d}",
                float(100 + day),
                float(102 + day),
                float(99 + day),
                float(101 + day),
                1000 + day,
            )
            for day in range(1, count + 1)
        ]
        connection.executemany(
            "INSERT INTO candles (symbol, timestamp, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()
    finally:
        connection.close()
    return path


def seed_ohlcv_database(path: Path, count: int = 10) -> Path:
    """Create a real-schema per-stock database with `count` ascending bars."""
    connection = sqlite3.connect(path)
    try:
        connection.executescript(OHLCV_SCHEMA)
        rows = [
            (
                f"2026-01-{day:02d} 09:15:00",
                float(100 + day),
                float(102 + day),
                float(99 + day),
                float(101 + day),
                1000 + day,
            )
            for day in range(1, count + 1)
        ]
        connection.executemany(
            "INSERT INTO ohlcv (candle_time, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()
    finally:
        connection.close()
    return path


def seed_ohlcv_intraday_database(path: Path, days: int = 2, bars_per_day: int = 25) -> Path:
    """Real-schema DB with regular intraday bars: 09:15, 09:30, ... per day.

    OHLC/volume increase deterministically with day and bar so aggregation
    correctness is easy to assert.
    """
    connection = sqlite3.connect(path)
    try:
        connection.executescript(OHLCV_SCHEMA)
        rows = []
        for day in range(0, days):
            for bar in range(bars_per_day):
                total_minutes = 9 * 60 + 15 + bar * 15
                hour, minute = divmod(total_minutes, 60)
                open_price = float(100 + day * 100 + bar * 2)
                rows.append(
                    (
                        f"2026-01-{day + 1:02d} {hour:02d}:{minute:02d}:00",
                        open_price,
                        open_price + 1,
                        open_price - 1,
                        open_price + 0.5,
                        1000 + day * 100 + bar,
                    )
                )
        connection.executemany(
            "INSERT INTO ohlcv (candle_time, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()
    finally:
        connection.close()
    return path


def seed_symbol_directory(directory: Path, symbols: dict[str, int]) -> Path:
    """Create a data directory with one per-stock database per symbol."""
    directory.mkdir(parents=True, exist_ok=True)
    for symbol, count in symbols.items():
        seed_ohlcv_database(directory / f"{symbol}.db", count=count)
    return directory


@pytest.fixture
def candle_database_path(tmp_path: Path) -> Path:
    """Path to a seeded candle database."""
    return seed_database(tmp_path / "test.db")


@pytest.fixture
def ohlcv_database_path(tmp_path: Path) -> Path:
    """Path to a seeded real-schema per-stock database."""
    return seed_ohlcv_database(tmp_path / "AMBUJACEM.db")


@pytest.fixture
def ohlcv_intraday_path(tmp_path: Path) -> Path:
    """Path to a real-schema database with regular intraday bars."""
    return seed_ohlcv_intraday_database(tmp_path / "TATASTEEL.db")


@pytest.fixture
def symbol_directory(tmp_path: Path) -> Path:
    """Path to a data directory with multiple per-stock databases."""
    return seed_symbol_directory(tmp_path / "data", {"AMBUJACEM": 12, "BPCL": 8, "RELIANCE": 5})
