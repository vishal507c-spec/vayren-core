from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class FeatureDefinition:
    name: str
    description: str = ""
    category: str = "price"
    dependencies: list[str] = field(default_factory=list)
    compute_fn: Callable | None = None

    def compute(self, data: Any) -> float:
        if self.compute_fn is None:
            return 0.0
        return self.compute_fn(data)
