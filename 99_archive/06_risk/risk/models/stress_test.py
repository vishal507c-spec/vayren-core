from dataclasses import dataclass, field
from typing import Any


@dataclass
class StressTest:
    name: str
    scenario: dict[str, Any] = field(default_factory=dict)
    impact: float = 0.0
    passed: bool = True
    details: dict[str, Any] = field(default_factory=dict)
