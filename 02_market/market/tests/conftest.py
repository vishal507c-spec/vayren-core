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


@pytest.fixture
def candle_database_path(tmp_path: Path) -> Path:
    """Path to a seeded candle database."""
    return seed_database(tmp_path / "test.db")
