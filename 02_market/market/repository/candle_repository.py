"""Candle repository — maps database rows to domain models. No SQL, no events."""

from market.database.sqlite import SqliteCandleDatabase
from market.models.bar import Bar


class CandleRepository:
    """Reads candle domain models from the database.

    The only layer that knows how to map a database row to a Bar.
    """

    def __init__(self, database: SqliteCandleDatabase) -> None:
        self._database = database

    def get_candles(self, symbol: str, limit: int) -> list[Bar]:
        """Return the most recent `limit` candles for `symbol`, ascending by timestamp."""
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
