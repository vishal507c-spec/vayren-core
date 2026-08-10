"""Shared test fixtures for app tests."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

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


def seed_symbol_directory(directory: Path, symbols: dict[str, int]) -> Path:
    """Create a data directory with one per-stock database per symbol."""
    directory.mkdir(parents=True, exist_ok=True)
    for symbol, count in symbols.items():
        seed_ohlcv_database(directory / f"{symbol}.db", count=count)
    return directory


@pytest.fixture
def qt_app() -> QApplication:
    """Return the process-wide QApplication, creating it if needed."""
    instance = QApplication.instance()
    if isinstance(instance, QApplication):
        return instance
    return QApplication([])
