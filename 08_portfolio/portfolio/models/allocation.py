from dataclasses import dataclass, field


@dataclass
class Allocation:
    symbol: str
    target_weight: float
    current_weight: float = 0.0
    current_value: float = 0.0
    strategy: str = ""

    @property
    def drift(self) -> float:
        return abs(self.target_weight - self.current_weight)

    @property
    def needs_rebalance(self, threshold: float = 0.05) -> bool:
        return self.drift > threshold
