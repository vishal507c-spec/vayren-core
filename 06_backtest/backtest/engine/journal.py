"""TradeJournal — ordered accumulation of closed trades."""

from backtest.models.trade import TradeRecord


class TradeJournal:
    """Collects :class:`TradeRecord` objects in close order."""

    def __init__(self) -> None:
        self._trades: list[TradeRecord] = []

    def record(self, trade: TradeRecord) -> None:
        """Add one closed trade."""
        self._trades.append(trade)

    @property
    def trades(self) -> tuple[TradeRecord, ...]:
        """Closed trades in chronological close order."""
        return tuple(self._trades)

    def clear(self) -> None:
        """Remove all trades."""
        self._trades.clear()

    def __len__(self) -> int:
        return len(self._trades)
