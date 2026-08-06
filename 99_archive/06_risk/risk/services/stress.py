from typing import Any

from risk.models.stress_test import StressTest


class StressTestService:
    def run_test(self, name: str, scenario: dict[str, Any], positions: dict[str, Any]) -> StressTest:
        impact = 0.0
        for symbol, pos in positions.items():
            shock = scenario.get("shock_pct", 0)
            pos_value = pos.get("market_value", 0)
            impact += pos_value * shock
        return StressTest(name=name, scenario=scenario, impact=impact, passed=abs(impact) < scenario.get("max_loss", float("inf")))
