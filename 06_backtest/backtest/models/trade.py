"""TradeRecord + related types — one closed position."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TradeRecord:
    """One closed position: entry → exit.

    Attributes:
        symbol: Traded symbol.
        side: Entry direction (``"LONG"`` or ``"SHORT"``).
        entry_index: Bar index of entry fill inside the replayed window.
        exit_index: Bar index of exit fill.
        entry_time: Bar timestamp of entry fill.
        exit_time: Bar timestamp of exit fill.
        entry_price: Fill price including slippage, without commission cost.
        exit_price: Fill price including slippage, without commission cost.
        quantity: Fractional shares filled (same for entry and exit).
        pnl: Realised P&L after commissions, in currency units.
        pnl_pct: ``(pnl / entry_cost) * 100``.
        commission: Total commission paid (entry + exit).
        bars_held: ``exit_index − entry_index``.
        exit_reason: Why the position closed (``SIGNAL``/``SL``/``TP``/``END``).
        r_multiple: P&L / initial risk when SL is set, else None.
    """

    symbol: str
    side: str
    entry_index: int
    exit_index: int
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    pnl_pct: float
    commission: float
    bars_held: int
    exit_reason: str
    r_multiple: float | None = None

    @property
    def winning(self) -> bool:
        """True when the trade profited after commissions."""
        return self.pnl > 0.0
