"""ExecutionSimulator — fill a signal against one bar.

Applies slippage and records a commission. Position sizing uses fractional
model (``quantity = available_equity / fill_price``) so metrics stay
deterministic without rounding artifacts; document this choice in UI text.
"""

from dataclasses import dataclass


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
        self._slippage_pct = max(0.0, float(slippage_pct))
        self._commission_pct = max(0.0, float(commission_pct))

    def fill(self, signal_side: str, bar_close: float, available_equity: float) -> Fill | None:
        """Return a Fill for ``bar_close``, or None when not affordable.

        A LONG signal buys; a SELL signal is only reached by the runner
        when a long position is already open and is handled as an exit
        through :class:`PositionManager` rather than a new Fill.
        Entry is rejected when quantity would be < 1 share equivalent worth
        of ``available_equity`` — prevents infinite-precision micro-fills.
        """
        if available_equity <= 0.0 or bar_close <= 0.0:
            return None
        slip = bar_close * (self._slippage_pct / 100.0)
        fill_price = bar_close + slip if signal_side == "LONG" else bar_close - slip
        if fill_price <= 0.0:
            return None
        quantity = available_equity / fill_price
        if quantity <= 0.0:
            return None
        commission = fill_price * quantity * (self._commission_pct / 100.0)
        return Fill(
            side=signal_side, fill_price=fill_price, quantity=quantity, commission=commission
        )
