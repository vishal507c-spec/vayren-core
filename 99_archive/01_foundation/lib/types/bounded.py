from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Bounded(Generic[T]):
    """A value constrained between lower and upper bounds."""

    value: T
    lower: T
    upper: T

    def __post_init__(self) -> None:
        if not (self.lower <= self.value <= self.upper):
            msg = f"Value {self.value} outside bounds [{self.lower}, {self.upper}]"
            raise ValueError(msg)

    @property
    def normalized(self) -> float:
        """Returns value normalized to [0, 1] range."""
        if self.lower == self.upper:
            return 1.0
        return (self.value - self.lower) / (self.upper - self.lower)

    def clamp(self, value: T) -> T:
        """Clamp a value within these bounds."""
        return max(self.lower, min(value, self.upper))
