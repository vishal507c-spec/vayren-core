from dataclasses import dataclass
from risk.models.limit import RiskLimit


@dataclass(frozen=True)
class LimitBreached:
    limit: RiskLimit
    current_value: float = 0.0
    max_value: float = 0.0
