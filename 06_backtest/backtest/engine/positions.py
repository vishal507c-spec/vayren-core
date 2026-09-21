"""PositionManager — tracks the one open position and closes trades.

State and trade identity live here; every closing decision (whether this bar
exits the leg, at what price, with what PnL and risk) is the Rust kernel's,
reached through `backtest.native_positions` (constitution §1: Backtesting).
"""

from dataclasses import dataclass, field

from backtest.models.trade import TradeRecord
from backtest.native_positions import ClosedTrade
from backtest.native_positions import close_trade as _kernel_close
from backtest.native_positions import try_close as _kernel_try_close


@dataclass
class _OpenPosition:
    symbol: str
    side: str
    entry_index: int
    entry_time: str
    entry_price: float
    quantity: float
    commission_entry: float
    sl_price: float | None = None
    tp_price: float | None = None


@dataclass
class PositionManager:
    """One position at a time (long or short).

    Holds at most one open leg; new entry accepted only when flat.
    Closes on opposite signal, SL/TP, or end. Every close produces TradeRecord.
    """

    symbol: str = ""
    _open: _OpenPosition | None = field(default=None, init=False, repr=False)

    @property
    def flat(self) -> bool:
        return self._open is None

    @property
    def open_position(self) -> _OpenPosition | None:
        return self._open

    def open_long(
        self,
        symbol: str,
        entry_index: int,
        entry_time: str,
        fill_price: float,
        quantity: float,
        commission: float,
        sl_price: float | None = None,
        tp_price: float | None = None,
    ) -> bool:
        if self._open is not None:
            return False
        self.symbol = symbol
        self._open = _OpenPosition(
            symbol=symbol,
            side="LONG",
            entry_index=entry_index,
            entry_time=entry_time,
            entry_price=fill_price,
            quantity=quantity,
            commission_entry=commission,
            sl_price=sl_price,
            tp_price=tp_price,
        )
        return True

    def open_short(
        self,
        symbol: str,
        entry_index: int,
        entry_time: str,
        fill_price: float,
        quantity: float,
        commission: float,
        sl_price: float | None = None,
        tp_price: float | None = None,
    ) -> bool:
        if self._open is not None:
            return False
        self.symbol = symbol
        self._open = _OpenPosition(
            symbol=symbol,
            side="SHORT",
            entry_index=entry_index,
            entry_time=entry_time,
            entry_price=fill_price,
            quantity=quantity,
            commission_entry=commission,
            sl_price=sl_price,
            tp_price=tp_price,
        )
        return True

    def try_close(
        self,
        exit_index: int,
        exit_time: str,
        bar_open: float,  # noqa: ARG002
        bar_high: float,
        bar_low: float,
        bar_close: float,
        commission_pct: float,
        exit_signal: bool = False,
    ) -> TradeRecord | None:
        entry = self._open
        if entry is None:
            return None
        closed = _kernel_try_close(
            side=entry.side,
            sl_price=entry.sl_price,
            tp_price=entry.tp_price,
            bar_high=bar_high,
            bar_low=bar_low,
            bar_close=bar_close,
            entry_price=entry.entry_price,
            quantity=entry.quantity,
            commission_entry=entry.commission_entry,
            commission_pct=commission_pct,
            exit_signal=exit_signal,
        )
        if closed is None:
            return None
        return self._record(entry, exit_index, exit_time, closed)

    def close_signal(
        self,
        exit_index: int,
        exit_time: str,
        exit_price: float,
        commission_pct: float,
    ) -> TradeRecord | None:
        return self._close_at(exit_index, exit_time, exit_price, commission_pct, "SIGNAL")

    def close_end(
        self,
        exit_index: int,
        exit_time: str,
        exit_price: float,
        commission_pct: float,
    ) -> TradeRecord | None:
        return self._close_at(exit_index, exit_time, exit_price, commission_pct, "END")

    def _close_at(
        self,
        exit_index: int,
        exit_time: str,
        exit_price: float,
        commission_pct: float,
        exit_reason: str,
    ) -> TradeRecord | None:
        entry = self._open
        if entry is None:
            return None
        closed = _kernel_close(
            side=entry.side,
            exit_reason=exit_reason,
            entry_price=entry.entry_price,
            quantity=entry.quantity,
            commission_entry=entry.commission_entry,
            exit_price=exit_price,
            commission_pct=commission_pct,
            sl_price=entry.sl_price,
        )
        return self._record(entry, exit_index, exit_time, closed)

    def _record(
        self,
        entry: _OpenPosition,
        exit_index: int,
        exit_time: str,
        closed: ClosedTrade,
    ) -> TradeRecord:
        trade = TradeRecord(
            symbol=entry.symbol,
            side=entry.side,
            entry_index=entry.entry_index,
            exit_index=exit_index,
            entry_time=entry.entry_time,
            exit_time=exit_time,
            entry_price=entry.entry_price,
            exit_price=closed.exit_price,
            quantity=entry.quantity,
            pnl=closed.pnl,
            pnl_pct=closed.pnl_pct,
            commission=closed.commission,
            bars_held=exit_index - entry.entry_index,
            exit_reason=closed.exit_reason,
            r_multiple=closed.r_multiple,
        )
        self._open = None
        return trade
