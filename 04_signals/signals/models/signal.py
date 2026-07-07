from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Signal:
    """A computed trading signal.

    Signals are normalized values (typically 0-100 or -1 to 1)
    derived from indicators.
    """

    name: str
    value: float
    timestamp: str
    symbol: str = ""
    confidence: float = 1.0
    metadata: dict = field(default_factory=dict)

    @property
    def is_bullish(self) -> bool:
        return self.value > 50.0 if 0 <= self.value <= 100 else self.value > 0

    @property
    def is_bearish(self) -> bool:
        return self.value < 50.0 if 0 <= self.value <= 100 else self.value < 0

    @property
    def is_neutral(self) -> bool:
        return abs(self.value - 50.0) < 5.0 if 0 <= self.value <= 100 else abs(self.value) < 0.1
