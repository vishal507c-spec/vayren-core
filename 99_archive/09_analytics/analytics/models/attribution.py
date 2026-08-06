from dataclasses import dataclass, field


@dataclass
class Attribution:
    strategy_contributions: dict[str, float] = field(default_factory=dict)
    symbol_contributions: dict[str, float] = field(default_factory=dict)
    total_return: float = 0.0

    @property
    def top_strategy(self) -> str:
        if not self.strategy_contributions:
            return ""
        return max(self.strategy_contributions, key=self.strategy_contributions.get)  # type: ignore[arg-type]
