"""StrategyParameters — immutable, validated parameter set for a strategy."""

import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType


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

    def __post_init__(self) -> None:
        """Validate the declaration itself (fail fast at registration)."""
        if not isinstance(self.key, str) or not self.key.strip():
            raise ParameterError("ParameterSpec key must be a non-empty string")
        if not isinstance(self.decimals, int) or isinstance(self.decimals, bool):
            raise ParameterError(f"{self.key}: decimals must be an int >= 0")
        if self.decimals < 0:
            raise ParameterError(f"{self.key}: decimals must be >= 0")
        for bound_name in ("minimum", "maximum", "default"):
            bound = getattr(self, bound_name)
            if not isinstance(bound, (int, float)) or isinstance(bound, bool):
                raise ParameterError(f"{self.key}: {bound_name} must be a number")
            if not math.isfinite(float(bound)):
                raise ParameterError(f"{self.key}: {bound_name} must be finite")
        if self.minimum > self.maximum:
            raise ParameterError(
                f"{self.key}: minimum ({self.minimum}) must be <= maximum ({self.maximum})"
            )
        if not (self.minimum <= float(self.default) <= self.maximum):
            raise ParameterError(
                f"{self.key}: default ({self.default}) must be within "
                f"[{self.minimum}, {self.maximum}]"
            )

    def validate(self, value: float) -> float:
        """Return the value rounded to `decimals`, clamped to the bounds."""
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ParameterError(f"{self.label} must be a finite number")
        if not isinstance(self.decimals, int) or self.decimals < 0:
            raise ParameterError(f"{self.label} has invalid decimals")
        step = 10**self.decimals
        rounded = round(value * step) / step
        if rounded < self.minimum or rounded > self.maximum:
            raise ParameterError(f"{self.label} must be between {self.minimum} and {self.maximum}")
        return rounded


class StrategyParameters(Mapping[str, float]):
    """Immutable mapping of parameter values with spec-based validation.

    Wraps a plain dict behind the Mapping protocol so definitions stay
    hashable-by-content and logics read typed numbers only. The backing
    store is a :class:`~types.MappingProxyType` and attribute assignment
    is blocked, so instances are frozen-like: neither ``params[key] = v``
    (Mapping has no ``__setitem__``) nor ``params._values = ...`` can
    mutate them after construction.
    """

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, float] | None = None) -> None:
        object.__setattr__(self, "_values", MappingProxyType(dict(values or {})))

    def __setattr__(self, name: str, value: object) -> None:
        raise ParameterError(f"StrategyParameters is immutable — cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        raise ParameterError(f"StrategyParameters is immutable — cannot delete {name!r}")

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
