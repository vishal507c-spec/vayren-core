from abc import ABC, abstractmethod
from typing import Any


class PositionSizer(ABC):
    """Determines position size for a trade."""

    @abstractmethod
    def calculate_size(self, capital: float, price: float, confidence: float, **kwargs: Any) -> int:
        ...


class FixedPositionSizer(PositionSizer):
    """Fixed share quantity position sizer."""

    def __init__(self, shares: int = 100) -> None:
        self._shares = shares

    def calculate_size(self, capital: float = 0.0, price: float = 0.0, confidence: float = 1.0, **kwargs: Any) -> int:
        return self._shares


class PercentPositionSizer(PositionSizer):
    """Position sizer based on percent of capital."""

    def __init__(self, percent: float = 0.1) -> None:
        self._percent = percent

    def calculate_size(self, capital: float, price: float, confidence: float = 1.0, **kwargs: Any) -> int:
        allocated = capital * self._percent * confidence
        if price <= 0:
            return 0
        return int(allocated / price)
