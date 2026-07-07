from typing import Optional

from signals.models.signal import Signal


class SignalRegistry:
    """Registry for tracking the latest signals per symbol."""

    def __init__(self) -> None:
        self._signals: dict[str, dict[str, Signal]] = {}

    def update(self, symbol: str, signal: Signal) -> None:
        if symbol not in self._signals:
            self._signals[symbol] = {}
        self._signals[symbol][signal.name] = signal

    def get(self, symbol: str, signal_name: str) -> Optional[Signal]:
        return self._signals.get(symbol, {}).get(signal_name)

    def get_all(self, symbol: str) -> dict[str, Signal]:
        return self._signals.get(symbol, {})

    def clear(self, symbol: str | None = None) -> None:
        if symbol:
            self._signals.pop(symbol, None)
        else:
            self._signals.clear()
