"""StrategyParameters — immutable, validated parameter set for a strategy."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass


class ParameterError(ValueError):
    """Raised when parameters are missing, unknown or out of range."""


@dataclass(frozen=True)
class ParameterSpec:
    """Declaration of one strategy parameter (used by configuration UIs).

    Attributes:
        key: Parameter key handed to the logic.
        label: Human-readable label.
        default: Default numeric value.
        minimum: Inclusive lower bound.
        maximum: Inclusive upper bound.
        decimals: Allowed decimal places (0 = integer parameter).
    """

    key: str
    label: str
    default: float
    minimum: float
    maximum: float
    decimals: int = 0

    def validate(self, value: float) -> float:
        """Return the value rounded to `decimals`, clamped to the bounds."""
        step = 10**self.decimals
        rounded = round(value * step) / step
        if rounded < self.minimum or rounded > self.maximum:
            raise ParameterError(f"{self.label} must be between {self.minimum} and {self.maximum}")
        return rounded


class StrategyParameters(Mapping[str, float]):
    """Immutable mapping of parameter values with spec-based validation.

    Wraps a plain dict behind the Mapping protocol so definitions stay
    hashable-by-content and logics read typed numbers only.
    """

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, float] | None = None) -> None:
        self._values: dict[str, float] = dict(values or {})

    def __getitem__(self, key: str) -> float:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, StrategyParameters):
            return self._values == other._values
        if isinstance(other, Mapping):
            return self._values == dict(other)
        return NotImplemented

    def __hash__(self) -> int:
        return hash(tuple(sorted(self._values.items())))

    def __repr__(self) -> str:
        return f"StrategyParameters({self._values!r})"

    @staticmethod
    def from_specs(specs: tuple[ParameterSpec, ...]) -> "StrategyParameters":
        """Build the default parameter set declared by `specs`."""
        return StrategyParameters({spec.key: spec.default for spec in specs})

    def validated(self, specs: tuple[ParameterSpec, ...]) -> "StrategyParameters":
        """Return a copy validated against `specs`.

        Every spec key must be present; unknown keys are rejected; every
        value is clamped/rounded through its spec.
        """
        known = {spec.key for spec in specs}
        unknown = set(self._values) - known
        if unknown:
            raise ParameterError(f"unknown parameters: {sorted(unknown)}")
        validated: dict[str, float] = {}
        for spec in specs:
            if spec.key not in self._values:
                raise ParameterError(f"{spec.label} is required")
            validated[spec.key] = spec.validate(self._values[spec.key])
        return StrategyParameters(validated)
