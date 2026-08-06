from logging import getLogger
from typing import Any

from market.models.bar import Bar

logger = getLogger(__name__)


class DataQualityService:
    """Validates and monitors data quality."""

    def validate_bar(self, bar: Bar) -> list[str]:
        issues: list[str] = []
        if bar.high < bar.low:
            issues.append(f"High ({bar.high}) < Low ({bar.low})")
        if bar.open < 0 or bar.high < 0 or bar.low < 0 or bar.close < 0:
            issues.append("Negative price detected")
        if bar.volume < 0:
            issues.append("Negative volume")
        if not bar.timestamp:
            issues.append("Missing timestamp")
        if bar.high == 0 and bar.low == 0:
            issues.append("Zero range (all prices zero)")
        return issues

    def is_bar_valid(self, bar: Bar) -> bool:
        return len(self.validate_bar(bar)) == 0
