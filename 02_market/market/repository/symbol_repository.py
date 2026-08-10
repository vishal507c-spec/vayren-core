"""Symbol repository — real per-stock database discovery and candle access."""

from pathlib import Path

from market.database.ohlcv import OhlcvCandleDatabase
from market.models.bar import Bar
from market.repository.candle_repository import CandleRepository


class SymbolRepository:
    """Reads candles from the real data directory, one database file per stock.

    `list_symbols` scans the directory; `get_candles` opens the matching
    file, reusing CandleRepository for the row → Bar mapping. Timeframe
    detection and aggregation go through the same file-open pattern. No events.
    """

    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)

    @property
    def directory(self) -> Path:
        return self._directory

    def list_symbols(self) -> tuple[str, ...]:
        """Return every available stock symbol (file stem), sorted."""
        return tuple(sorted(path.stem for path in self._directory.glob("*.db")))

    def get_candles(self, symbol: str, limit: int | None) -> list[Bar]:
        """Return candles for the symbol's database file.

        ``limit`` of ``None`` requests the entire available history.
        """
        database = OhlcvCandleDatabase(self._directory / f"{symbol}.db")
        database.connect()
        try:
            return CandleRepository(database).get_candles(symbol, limit)
        finally:
            database.close()

    def detect_timeframes(self, symbol: str) -> tuple[str, ...]:
        """Timeframes the symbol's database can produce, detected from SQLite."""
        database = OhlcvCandleDatabase(self._directory / f"{symbol}.db")
        database.connect()
        try:
            return CandleRepository(database).detect_timeframes(symbol)
        finally:
            database.close()

    def get_candles_timeframe(self, symbol: str, timeframe: str, limit: int | None) -> list[Bar]:
        """Return candles for the symbol at a timeframe.

        ``limit`` of ``None`` requests the entire available history.
        """
        database = OhlcvCandleDatabase(self._directory / f"{symbol}.db")
        database.connect()
        try:
            return CandleRepository(database).get_candles_timeframe(symbol, timeframe, limit)
        finally:
            database.close()
