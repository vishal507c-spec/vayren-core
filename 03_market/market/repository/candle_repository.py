"""Candle repository — maps database rows to domain models. No SQL, no events."""

from market.database.ohlcv import OhlcvCandleDatabase
from market.database.sqlite import SqliteCandleDatabase
from market.models.bar import Bar
from market.timeframe.aggregate import aggregate_bars, detect_bar_duration, detect_session_start
from market.timeframe.timeframe import available_timeframes, timeframe_seconds

_DETECTION_SAMPLE = 5000


class CandleRepository:
    """Reads candle domain models from the database.

    The only layer that knows how to map a database row to a Bar — including
    rows merged into higher timeframes. Accepts any SQLite database layer
    exposing `fetch_candles(symbol, limit)`.
    """

    def __init__(self, database: SqliteCandleDatabase | OhlcvCandleDatabase) -> None:
        self._database = database

    def get_candles(self, symbol: str, limit: int | None) -> list[Bar]:
        """Return candles for `symbol`, ascending by timestamp.

        ``limit`` of ``None`` requests the entire available history.
        """
        rows = self._database.fetch_candles(symbol, limit)
        return [
            Bar(
                symbol=row["symbol"],
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
                timestamp=row["timestamp"],
            )
            for row in rows
        ]

    def detect_bar_duration(self, symbol: str, sample: int = 5000) -> int | None:
        """Dominant bar duration in seconds, detected from the database's own rows."""
        rows = self._database.fetch_candles(symbol, sample)
        return detect_bar_duration([row["timestamp"] for row in rows])

    def detect_timeframes(self, symbol: str) -> tuple[str, ...]:
        """Timeframes the database can produce, detected from its own rows.

        Always queried from SQLite — never a cached list.
        """
        duration = self.detect_bar_duration(symbol)
        if duration is None:
            return ()
        return available_timeframes(duration)

    def get_candles_timeframe(self, symbol: str, timeframe: str, limit: int | None) -> list[Bar]:
        """Return candles for `symbol` at `timeframe`, ascending by timestamp.

        ``limit`` of ``None`` requests the entire available history.

        Timeframes at or below the detected base duration fall back to the
        plain fetch. Larger timeframes are aggregated from real rows only.
        The session anchor is detected from a full sample — never from the
        (possibly partial) aggregation window.
        """
        seconds = timeframe_seconds(timeframe)
        sample_rows = self._database.fetch_candles(symbol, _DETECTION_SAMPLE)
        base = detect_bar_duration([row["timestamp"] for row in sample_rows])
        if seconds is None or base is None or seconds <= base:
            return self.get_candles(symbol, limit)
        session_start = detect_session_start(sample_rows)
        ratio = seconds // base
        if limit is None:
            rows = self._database.fetch_candles(symbol, None)
            return aggregate_bars(rows, seconds, timeframe, session_start)
        rows = self._database.fetch_candles(symbol, (limit + 1) * ratio)
        bars = aggregate_bars(rows, seconds, timeframe, session_start)
        return bars[-limit:]
