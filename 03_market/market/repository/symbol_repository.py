"""Symbol repository — real per-stock database discovery and candle access."""

from pathlib import Path

from market.database.ohlcv import OhlcvCandleDatabase
from market.models.bar import Bar
from market.models.symbol_quote import SymbolQuote
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

    def get_quotes(self, symbols: tuple[str, ...]) -> tuple[SymbolQuote, ...]:
        """Return the latest real quote per symbol, one candle read per stock.

        Each symbol's most recent candle is read (never the full history) and
        mapped to a quote. Symbols whose database file is missing are skipped
        gracefully — the snapshot stays complete for everything available.
        """
        quotes = []
        for symbol in symbols:
            path = self._directory / f"{symbol}.db"
            if not path.is_file():
                continue
            database = OhlcvCandleDatabase(path)
            database.connect()
            try:
                bars = CandleRepository(database).get_candles(symbol, 1)
            finally:
                database.close()
            if not bars:
                continue
            bar = bars[-1]
            quotes.append(
                SymbolQuote(
                    symbol=symbol,
                    price=bar.close,
                    change_pct=bar.return_pct,
                    timestamp=bar.timestamp,
                )
            )
        return tuple(quotes)

    def detect_timeframes(self, symbol: str) -> tuple[str, ...]:
        """Timeframes the symbol's database can produce, detected from SQLite."""
        database = OhlcvCandleDatabase(self._directory / f"{symbol}.db")
        database.connect()
        try:
            return CandleRepository(database).detect_timeframes(symbol)
        finally:
            database.close()

    def detect(self, symbol: str) -> tuple[int, int] | None:
        """One-shot (base_seconds, session_start) for polling callers."""
        database = OhlcvCandleDatabase(self._directory / f"{symbol}.db")
        database.connect()
        try:
            return CandleRepository(database).detect(symbol)
        finally:
            database.close()

    def get_candles_timeframe(
        self,
        symbol: str,
        timeframe: str,
        limit: int | None,
        start: str | None = None,
        end: str | None = None,
        detection: tuple[int, int] | None = None,
    ) -> list[Bar]:
        """Return candles for the symbol at a timeframe.

        ``limit`` of ``None`` requests the entire available history.
        ``start``/``end`` are optional inclusive ``YYYY-MM-DD HH:MM:SS``
        bounds on the aggregation window (detection stays unbounded).
        ``detection`` is an optional cached ``(base_seconds, session_start)``
        hint from :meth:`detect` that skips the per-call sample scan.
        """
        database = OhlcvCandleDatabase(self._directory / f"{symbol}.db")
        database.connect()
        try:
            return CandleRepository(database).get_candles_timeframe(
                symbol, timeframe, limit, start, end, detection
            )
        finally:
            database.close()
