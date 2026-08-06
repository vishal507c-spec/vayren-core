from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Indicator:
    """An indicator definition with compute function."""

    name: str
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    _compute_fn: Callable | None = None

    def compute(self, data: Any) -> float:
        if self._compute_fn is None:
            msg = f"No compute function registered for {self.name}"
            raise ValueError(msg)
        return self._compute_fn(data, **self.parameters)
