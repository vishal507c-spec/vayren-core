"""ExecutionSimulator — ask the Rust kernel to fill a signal against one bar.

The rule (slippage direction, affordability rejection, fractional position
sizing ``quantity = available_equity / fill_price``, commission) lives in
`rust/vayren-core/src/backtest.rs`. This class keeps the domain shape —
percentages in, `Fill` out — and holds no pricing logic of its own.
"""

from dataclasses import dataclass

from backtest.native_positions import fill as _native_fill


@dataclass(frozen=True)
class Fill:
    """One execution: direction, price, quantity and fee."""

    side: str  # "LONG" or "SHORT" for the entry leg
    fill_price: float
    quantity: float
    commission: float
    sl_price: float | None = None
    tp_price: float | None = None


class ExecutionSimulator:
    """Deterministic filler: signal → fill at this bar's close ± slippage."""

    def __init__(
        self,
        slippage_pct: float = 0.02,
        commission_pct: float = 0.03,
    ) -> None:
        self._slippage_pct = float(slippage_pct)
        self._commission_pct = float(commission_pct)

    def fill(self, signal_side: str, bar_close: float, available_equity: float) -> Fill | None:
        """Return a Fill for ``bar_close``, or None when not affordable.

        A LONG signal buys; a SELL signal is only reached by the runner
        when a long position is already open and is handled as an exit
        through :class:`PositionManager` rather than a new Fill.
        """
        filled = _native_fill(
            signal_side,
            bar_close,
            available_equity,
            self._slippage_pct,
            self._commission_pct,
        )
        if filled is None:
            return None
        return Fill(
            side=filled.side,
            fill_price=filled.fill_price,
            quantity=filled.quantity,
            commission=filled.commission,
        )
