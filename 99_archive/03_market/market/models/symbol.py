from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Symbol:
    """Trading symbol metadata."""

    ticker: str
    name: str = ""
    exchange: str = ""
    asset_type: str = "stock"
    currency: str = "USD"
    tick_size: float = 0.01
    lot_size: int = 1
    is_active: bool = True

    @property
    def is_etf(self) -> bool:
        return self.asset_type == "etf"

    @property
    def is_stock(self) -> bool:
        return self.asset_type == "stock"

    def __str__(self) -> str:
        return self.ticker
