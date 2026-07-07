from typing import Any

from risk.models.limit import RiskLimit


class PreTradeRisk:
    def __init__(self) -> None:
        self._limits: list[RiskLimit] = []

    def add_limit(self, limit: RiskLimit) -> None:
        self._limits.append(limit)

    def check_order(self, symbol: str, quantity: int, price: float, capital: float) -> tuple[bool, list[str]]:
        order_value = quantity * price
        failures: list[str] = []
        for limit in self._limits:
            if limit.is_breached:
                failures.append(f"Limit breached: {limit.name} ({limit.current_value}/{limit.max_value})")
            if order_value > limit.remaining() and limit.name == "max_order_value":
                failures.append(f"Order value {order_value} exceeds remaining limit {limit.remaining()}")
        return len(failures) == 0, failures

    def all_limits(self) -> list[RiskLimit]:
        return self._limits
