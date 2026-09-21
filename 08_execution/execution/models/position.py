"""Position and account snapshots — execution-owned ledger state.

Plain data only. The sign verdict (``flat``) and the mark-to-market rule
are decided by the Rust execution kernel; this module asks, it does not
calculate.
"""

from __future__ import annotations

from dataclasses import dataclass

from execution.native_execution import native_position_state, native_position_unrealized


@dataclass(frozen=True)
class Position:
    """Net position in one symbol held by one strategy context."""

    symbol: str
    quantity: float = 0.0  # signed: +long / -short / 0 flat
    avg_price: float = 0.0
    realized_pnl: float = 0.0

    @property
    def flat(self) -> bool:
        return native_position_state(self.quantity)[0]

    def unrealized(self, mark_price: float) -> float:
        return native_position_unrealized(self.quantity, self.avg_price, mark_price)


@dataclass(frozen=True)
class AccountSnapshot:
    """Capital view handed to risk and reconciliation.

    ``account_id``/``environment`` complete the typed account identity
    (FINAL §H); both default empty so ledger-constructed snapshots keep
    working unchanged. Identity only — never secret values.
    """

    equity: float
    available_capital: float
    day_pnl: float = 0.0
    currency: str = "INR"
    account_id: str = ""
    environment: str = ""
