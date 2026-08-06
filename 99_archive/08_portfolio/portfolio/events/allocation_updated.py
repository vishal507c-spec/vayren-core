from dataclasses import dataclass
from portfolio.models.allocation import Allocation


@dataclass(frozen=True)
class AllocationUpdated:
    allocations: list[Allocation]
    portfolio: str = ""
