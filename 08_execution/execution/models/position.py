"""Position and account snapshots — execution-owned ledger state."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Position:
    """Net position in one symbol held by one strategy context."""

    symbol: str
    quantity: float = 0.0  # signed: +long / -short / 0 flat
    avg_price: float = 0.0
    realized_pnl: float = 0.0

    @property
    def flat(self) -> bool:
        return self.quantity == 0.0

    def unrealized(self, mark_price: float) -> float:
        if self.flat:
            return 0.0
        return (mark_price - self.avg_price) * self.quantity


@dataclass(frozen=True)
class AccountSnapshot:
    """Capital view handed to risk and reconciliation."""

    equity: float
    available_capital: float
    day_pnl: float = 0.0
    currency: str = "INR"
