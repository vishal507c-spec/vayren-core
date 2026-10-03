"""Position ledger — fills in, positions and PnL out. Deterministic."""

from __future__ import annotations

from execution.models.order import Fill
from execution.models.position import AccountSnapshot, Position
from execution.native_execution import (
    native_ledger_apply_fill,
    native_ledger_snapshot,
    native_position_state,
)


class PositionLedger:
    """Execution-owned book. One writer (the session); no shared state."""

    def __init__(self, starting_capital: float) -> None:
        if starting_capital <= 0:
            raise ValueError("starting capital must be positive")
        self._starting_capital = float(starting_capital)
        self._positions: dict[str, Position] = {}
        self._realized: dict[str, float] = {}
        self._day_pnl = 0.0

    def apply_fill(self, fill: Fill) -> Position:
        """Fold a fill into the ledger; returns the updated position."""
        pos = self._positions.get(fill.symbol, Position(symbol=fill.symbol))
        new_qty, avg_price, realized_pnl, pnl_delta = native_ledger_apply_fill(
            pos.quantity,
            pos.avg_price,
            pos.realized_pnl,
            fill.side,
            fill.fill_qty,
            fill.fill_price,
            fill.commission,
        )
        if pnl_delta != 0.0:
            self._realized[fill.symbol] = self._realized.get(fill.symbol, 0.0) + pnl_delta
            self._day_pnl += pnl_delta
        updated = Position(
            symbol=fill.symbol,
            quantity=new_qty,
            avg_price=avg_price,
            realized_pnl=realized_pnl,
        )
        if updated.flat:
            self._positions.pop(fill.symbol, None)
        else:
            self._positions[fill.symbol] = updated
        return self._positions.get(fill.symbol, updated)

    def position(self, symbol: str) -> Position:
        return self._positions.get(symbol, Position(symbol=symbol))

    def all_positions(self) -> tuple[Position, ...]:
        return tuple(self._positions.values())

    def snapshot(self, marks: dict[str, float] | None = None) -> AccountSnapshot:
        """Account view: starting capital + realized + unrealized at marks."""
        marks = marks or {}
        pos_tuples = [
            (p.quantity, p.avg_price, marks.get(p.symbol, p.avg_price))
            for p in self._positions.values()
        ]
        realized = sum(self._realized.values())
        equity, available, day_pnl = native_ledger_snapshot(
            self._starting_capital,
            realized,
            self._day_pnl,
            pos_tuples,
        )
        return AccountSnapshot(equity=equity, available_capital=available, day_pnl=day_pnl)

    def strategy_state_for(self, symbol: str) -> tuple[float, str | None, float | None]:
        """(signed qty, side, avg entry) for BarView position state."""
        pos = self.position(symbol)
        flat, side = native_position_state(pos.quantity)
        if flat:
            return 0.0, None, None
        return pos.quantity, side, pos.avg_price
