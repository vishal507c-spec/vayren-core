from dataclasses import dataclass
from risk.models.stress_test import StressTest


@dataclass(frozen=True)
class StressResult:
    test: StressTest
