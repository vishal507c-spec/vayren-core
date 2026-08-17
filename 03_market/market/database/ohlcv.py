"""OHLCV SQLite candle database — reads Zerodha per-stock database files.

One database file = one stock. Schema:

    CREATE TABLE ohlcv (
        candle_time TEXT PRIMARY KEY,
        open  REAL NOT NULL,
        high  REAL NOT NULL,
        low   REAL NOT NULL,
        close REAL NOT NULL,
        volume INTEGER NOT NULL
    );

Rows are exposed with the same keys the repository layer expects
(symbol, timestamp, open, high, low, close, volume) so the mapping
layer stays schema-agnostic.
"""

import sqlite3
from pathlib import Path

_OHLCV_TABLE = "ohlcv"


class OhlcvCandleDatabase:
    """Lowest SQLite layer for per-stock OHLCV files.

    Knows the real data schema only. No domain logic, no events, no models.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._connection: sqlite3.Connection | None = None

    @property
    def path(self) -> Path:
        return self._path

    def connect(self) -> None:
        """Open the file and verify the ohlcv table exists."""
        if not self._path.is_file():
            raise FileNotFoundError(f"Candle database not found: {self._path}")
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (_OHLCV_TABLE,),
        ).fetchone()
        if table is None:
            connection.close()
            raise RuntimeError(f"Candle database has no '{_OHLCV_TABLE}' table: {self._path}")
        self._connection = connection

    def fetch_candles(self, symbol: str, limit: int | None) -> list[sqlite3.Row]:
        """Return candles ascending by candle_time.

        ``limit`` of ``None`` requests the entire available history.
        """
        connection = self._require_connection()
        if limit is None:
            cursor = connection.execute(
                "SELECT ? AS symbol, candle_time AS timestamp, open, high, low, close, volume "
                "FROM ohlcv ORDER BY candle_time ASC",
                (symbol,),
            )
        else:
            cursor = connection.execute(
                "SELECT ? AS symbol, candle_time AS timestamp, open, high, low, close, volume "
                "FROM ("
                "  SELECT candle_time, open, high, low, close, volume "
                "  FROM ohlcv ORDER BY candle_time DESC LIMIT ?"
                ") ORDER BY timestamp ASC",
                (symbol, limit),
            )
        return list(cursor.fetchall())

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Database is not connected")
        return self._connection
