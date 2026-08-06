from dataclasses import dataclass
from typing import Optional


@dataclass
class RiskLimit:
    name: str
    max_value: float
    current_value: float = 0.0
    unit: str = ""
    active: bool = True

    @property
    def utilization_pct(self) -> float:
        if self.max_value == 0:
            return 0.0
        return (self.current_value / self.max_value) * 100

    @property
    def is_breached(self) -> bool:
        return self.current_value >= self.max_value

    @property
    def is_approaching(self, threshold: float = 0.8) -> bool:
        return self.utilization_pct >= threshold * 100

    def remaining(self) -> float:
        return max(0.0, self.max_value - self.current_value)
