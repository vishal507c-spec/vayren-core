"""Position ledger — fills in, positions and PnL out. Deterministic."""

from __future__ import annotations

from execution.models.order import Fill
from execution.models.position import AccountSnapshot, Position


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
        direction = 1.0 if fill.side == "BUY" else -1.0
        signed = direction * fill.fill_qty
        new_qty = pos.quantity + signed
        if pos.flat or (pos.quantity > 0) == (signed > 0):
            total_cost = pos.avg_price * abs(pos.quantity) + fill.fill_price * fill.fill_qty
            denom = abs(new_qty)
            avg = total_cost / denom if denom > 0 else 0.0
            updated = Position(
                symbol=fill.symbol, quantity=new_qty, avg_price=avg, realized_pnl=pos.realized_pnl
            )
        else:
            closing = min(abs(pos.quantity), fill.fill_qty)
            pnl = (fill.fill_price - pos.avg_price) * closing * (1.0 if pos.quantity > 0 else -1.0)
            pnl -= fill.commission * (closing / fill.fill_qty if fill.fill_qty else 0.0)
            realized = pos.realized_pnl + pnl
            self._realized[fill.symbol] = self._realized.get(fill.symbol, 0.0) + pnl
            self._day_pnl += pnl
            if abs(new_qty) > 0:
                updated = Position(
                    symbol=fill.symbol,
                    quantity=new_qty,
                    avg_price=fill.fill_price,
                    realized_pnl=realized,
                )
            else:
                updated = Position(
                    symbol=fill.symbol, quantity=0.0, avg_price=0.0, realized_pnl=realized
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
        unrealized = sum(
            p.unrealized(marks.get(p.symbol, p.avg_price)) for p in self._positions.values()
        )
        realized = sum(self._realized.values())
        equity = self._starting_capital + realized + unrealized
        return AccountSnapshot(
            equity=equity, available_capital=equity, day_pnl=self._day_pnl + unrealized
        )

    def strategy_state_for(self, symbol: str) -> tuple[float, str | None, float | None]:
        """(signed qty, side, avg entry) for BarView position state."""
        pos = self.position(symbol)
        if pos.flat:
            return 0.0, None, None
        return pos.quantity, ("LONG" if pos.quantity > 0 else "SHORT"), pos.avg_price
