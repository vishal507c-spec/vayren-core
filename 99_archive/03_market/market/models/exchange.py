from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Exchange:
    """Exchange metadata."""

    name: str
    mic: str
    country: str = ""
    timezone: str = "US/Eastern"
    opens_at: str = "09:30"
    closes_at: str = "16:00"
    is_open: bool = True
    currency: str = "USD"

    @property
    def market_hours(self) -> str:
        return f"{self.opens_at}-{self.closes_at} {self.timezone}"
