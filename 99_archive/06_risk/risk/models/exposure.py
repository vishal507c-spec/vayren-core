from dataclasses import dataclass, field


@dataclass
class Exposure:
    symbol: str
    quantity: int = 0
    market_value: float = 0.0
    notional: float = 0.0
    sector: str = ""
    asset_class: str = "stock"

    @property
    def exposure_pct(self, total_capital: float = 100000.0) -> float:
        if total_capital == 0:
            return 0.0
        return (self.market_value / total_capital) * 100
