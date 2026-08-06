from dataclasses import dataclass


@dataclass(frozen=True)
class RebalanceTriggered:
    portfolio: str = ""
    reason: str = ""
