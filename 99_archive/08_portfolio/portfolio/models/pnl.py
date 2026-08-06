from dataclasses import dataclass, field


@dataclass
class PnL:
    total: float = 0.0
    realized: float = 0.0
    unrealized: float = 0.0
    daily: float = 0.0
    fees: float = 0.0
    by_symbol: dict[str, float] = field(default_factory=dict)

    @property
    def net(self) -> float:
        return self.total - self.fees
