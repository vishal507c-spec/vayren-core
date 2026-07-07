from typing import Any

from signals.models.signal import Signal
from signals.models.indicator import Indicator


class SignalComputeService:
    """Computes signals from market data using registered indicators."""

    def __init__(self) -> None:
        self._indicators: dict[str, Indicator] = {}

    def register_indicator(self, indicator: Indicator) -> None:
        self._indicators[indicator.name] = indicator

    def compute(self, name: str, data: Any, symbol: str = "", timestamp: str = "") -> Signal:
        indicator = self._indicators.get(name)
        if indicator is None:
            msg = f"Unknown indicator: {name}"
            raise ValueError(msg)
        value = indicator.compute(data)
        return Signal(name=name, value=value, timestamp=timestamp, symbol=symbol)
