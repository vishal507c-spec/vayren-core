from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Trade:
    """Individual trade data point."""

    symbol: str
    price: float
    size: int
    timestamp: str
    exchange: str = ""
    trade_id: str = ""
    conditions: list[str] = None

    def __post_init__(self) -> None:
        if self.conditions is None:
            object.__setattr__(self, "conditions", [])

    @property
    def dollar_volume(self) -> float:
        return self.price * self.size
