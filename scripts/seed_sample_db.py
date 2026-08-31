"""Seed a sample candle database at data/vayren.db.

Generates realistic daily OHLCV bars (random walk) for the configured symbol.
Replace data/vayren.db with the real candle database when it becomes available.

Usage:
    python scripts/seed_sample_db.py [--symbol SPY] [--bars 1200] [--output data/vayren.db]
"""

import argparse
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    symbol    TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    open      REAL NOT NULL,
    high      REAL NOT NULL,
    low       REAL NOT NULL,
    close     REAL NOT NULL,
    volume    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_candles_symbol_timestamp ON candles (symbol, timestamp);
"""


def generate_bars(
    symbol: str, count: int, seed: int = 42
) -> list[tuple[str, str, float, float, float, float, int]]:
    """Generate a deterministic random-walk series of daily OHLCV bars."""
    rng = random.Random(seed)
    price = 100.0
    day = datetime(2024, 1, 1)
    bars: list[tuple[str, str, float, float, float, float, int]] = []
    for index in range(count):
        if index % 5 == 0:
            day += timedelta(days=3)
        open_price = price
        drift = rng.uniform(-0.02, 0.02)
        close_price = max(1.0, open_price * (1.0 + drift))
        high_price = max(open_price, close_price) * (1.0 + rng.uniform(0.0, 0.015))
        low_price = min(open_price, close_price) * (1.0 - rng.uniform(0.0, 0.015))
        volume = rng.randint(1_000_000, 20_000_000)
        bars.append(
            (
                symbol,
                day.date().isoformat(),
                round(open_price, 2),
                round(high_price, 2),
                round(low_price, 2),
                round(close_price, 2),
                volume,
            )
        )
        price = close_price
        day += timedelta(days=1)
    return bars


def seed(database_path: Path, symbol: str, bars: int) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(SCHEMA)
        rows = generate_bars(symbol, bars)
        connection.executemany(
            "INSERT INTO candles (symbol, timestamp, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()
    finally:
        connection.close()
    print(f"Seeded {bars} bars for {symbol} -> {database_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed a sample candle database")
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--bars", type=int, default=1200)
    parser.add_argument("--output", default="data/vayren.db")
    args = parser.parse_args()
    seed(Path(args.output), args.symbol, args.bars)


if __name__ == "__main__":
    main()
