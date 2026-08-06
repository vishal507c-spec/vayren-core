"""SQLite candle database layer. Raw SQL access only, no domain logic."""

import sqlite3
from pathlib import Path

_CANDLES_TABLE = "candles"


class SqliteCandleDatabase:
    """Lowest SQLite layer: connection management and parameterized queries.

    Knows the schema, nothing about the domain. No events, no models.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._connection: sqlite3.Connection | None = None

    @property
    def path(self) -> Path:
        return self._path

    def connect(self) -> None:
        """Open the database and verify the candles table exists."""
        if not self._path.is_file():
            raise FileNotFoundError(f"Candle database not found: {self._path}")
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (_CANDLES_TABLE,),
        ).fetchone()
        if table is None:
            connection.close()
            raise RuntimeError(f"Candle database has no '{_CANDLES_TABLE}' table: {self._path}")
        self._connection = connection

    def fetch_candles(self, symbol: str, limit: int) -> list[sqlite3.Row]:
        """Return the most recent `limit` candles for `symbol`, ascending by timestamp."""
        connection = self._require_connection()
        cursor = connection.execute(
            "SELECT symbol, timestamp, open, high, low, close, volume "
            "FROM ("
            "  SELECT * FROM candles WHERE symbol = ? ORDER BY timestamp DESC LIMIT ?"
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
