from typing import Optional

from risk.models.limit import RiskLimit
from risk.models.exposure import Exposure
from risk.events.limit_approaching import LimitApproaching
from risk.events.limit_breached import LimitBreached


class RiskMonitor:
    def __init__(self) -> None:
        self._limits: list[RiskLimit] = []
        self._exposures: dict[str, Exposure] = {}

    def add_limit(self, limit: RiskLimit) -> None:
        self._limits.append(limit)

    def update_exposure(self, exposure: Exposure) -> None:
        self._exposures[exposure.symbol] = exposure

    def check_limits(self) -> list[LimitApproaching | LimitBreached]:
        alerts: list[LimitApproaching | LimitBreached] = []
        for limit in self._limits:
            if limit.is_breached:
                alerts.append(LimitBreached(limit=limit, current_value=limit.current_value, max_value=limit.max_value))
            elif limit.is_approaching():
                alerts.append(LimitApproaching(limit=limit, current_value=limit.current_value, max_value=limit.max_value))
        return alerts
