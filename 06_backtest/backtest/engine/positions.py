"""PositionManager — tracks the one open position and closes trades."""

from dataclasses import dataclass, field

from backtest.models.trade import TradeRecord


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
        if self._open is None:
            return None
        exit_price: float | None = None
        exit_reason = "SIGNAL"
        if exit_signal:
            slip = bar_close * 0.0002
            exit_price = bar_close - slip
            exit_reason = "SIGNAL"
        elif self._open.side == "LONG":
            if self._open.sl_price is not None and bar_low <= self._open.sl_price:
                exit_price = self._open.sl_price
                exit_reason = "SL"
            elif self._open.tp_price is not None and bar_high >= self._open.tp_price:
                exit_price = self._open.tp_price
                exit_reason = "TP"
        else:  # SHORT
            if self._open.sl_price is not None and bar_high >= self._open.sl_price:
                exit_price = self._open.sl_price
                exit_reason = "SL"
            elif self._open.tp_price is not None and bar_low <= self._open.tp_price:
                exit_price = self._open.tp_price
                exit_reason = "TP"
        if exit_price is None:
            return None
        return self._close_position(exit_index, exit_time, exit_price, commission_pct, exit_reason)

    def close_signal(
        self,
        exit_index: int,
        exit_time: str,
        exit_price: float,
        commission_pct: float,
    ) -> TradeRecord | None:
        if self._open is None:
            return None
        return self._close_position(exit_index, exit_time, exit_price, commission_pct, "SIGNAL")

    def close_end(
        self,
        exit_index: int,
        exit_time: str,
        exit_price: float,
        commission_pct: float,
    ) -> TradeRecord | None:
        if self._open is None:
            return None
        return self._close_position(exit_index, exit_time, exit_price, commission_pct, "END")

    def _close_position(
        self,
        exit_index: int,
        exit_time: str,
        exit_price: float,
        commission_pct: float,
        exit_reason: str,
    ) -> TradeRecord:
        assert self._open is not None
        entry = self._open
        commission_exit = exit_price * entry.quantity * (commission_pct / 100.0)
        commission = entry.commission_entry + commission_exit
        if entry.side == "LONG":
            gross = (exit_price - entry.entry_price) * entry.quantity
        else:
            gross = (entry.entry_price - exit_price) * entry.quantity
        pnl = gross - commission
        entry_cost = entry.entry_price * entry.quantity
        pnl_pct = (pnl / entry_cost * 100.0) if entry_cost else 0.0
        risk = abs(entry.entry_price - entry.sl_price) * entry.quantity if entry.sl_price else None
        r_multiple = (pnl / risk) if risk and risk != 0 else None
        trade = TradeRecord(
            symbol=entry.symbol,
            side=entry.side,
            entry_index=entry.entry_index,
            exit_index=exit_index,
            entry_time=entry.entry_time,
            exit_time=exit_time,
            entry_price=entry.entry_price,
            exit_price=exit_price,
            quantity=entry.quantity,
            pnl=pnl,
            pnl_pct=pnl_pct,
            commission=commission,
            bars_held=exit_index - entry.entry_index,
            exit_reason=exit_reason,
            r_multiple=r_multiple,
        )
        self._open = None
        return trade
