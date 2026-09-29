"""StrategyDefinition — immutable description of a registered strategy."""

import math
from dataclasses import dataclass

from strategy.models.parameters import StrategyParameters


@dataclass(frozen=True)
class StrategyDefinition:
    """A strategy entry in the registry: identity, kind, parameters, allocation.

    A definition never contains trading logic itself; ``kind`` names the
    registered logic implementation that turns bars into signals.
    ``allocation_pct`` is the configurable portfolio weight (0-100) used by
    a future orchestrator; it does not scale backtest results by itself.

    Attributes:
        id: Stable unique identifier (kebab-case, e.g. ``"sma-crossover"``).
        name: Display name (e.g. ``"SMA Crossover"``).
        version: Strategy version string (e.g. ``"1.0"``).
        kind: Registered logic kind (e.g. ``"sma_crossover"``).
        params: Strategy parameters handed to the logic at run time.
        allocation_pct: Configurable allocation weight in percent (0-100).
        enabled: Whether the strategy participates in runs.
    """

    id: str
    name: str
    version: str
    kind: str
    params: StrategyParameters
    allocation_pct: float = 100.0
    enabled: bool = True

    def __post_init__(self) -> None:
        """Reject empty identity fields and out-of-range allocations."""
        for field_name in ("id", "name", "kind"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"StrategyDefinition {field_name} must be a non-empty string")
        try:
            allocation = float(self.allocation_pct)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"allocation_pct must be a number, got {self.allocation_pct!r}"
            ) from exc
        if not math.isfinite(allocation) or not 0.0 <= allocation <= 100.0:
            raise ValueError(f"allocation_pct must be within [0, 100], got {allocation!r}")

    def with_params(self, params: StrategyParameters) -> "StrategyDefinition":
        """Return a copy with replaced parameters."""
        return StrategyDefinition(
            id=self.id,
            name=self.name,
            version=self.version,
            kind=self.kind,
            params=params,
            allocation_pct=self.allocation_pct,
            enabled=self.enabled,
        )

    def with_enabled(self, enabled: bool) -> "StrategyDefinition":
        """Return a copy with a new enabled flag."""
        return StrategyDefinition(
            id=self.id,
            name=self.name,
            version=self.version,
            kind=self.kind,
            params=self.params,
            allocation_pct=self.allocation_pct,
            enabled=enabled,
        )

    def with_allocation(self, allocation_pct: float) -> "StrategyDefinition":
        """Return a copy with a new allocation weight."""
        return StrategyDefinition(
            id=self.id,
            name=self.name,
            version=self.version,
            kind=self.kind,
            params=self.params,
            allocation_pct=allocation_pct,
            enabled=self.enabled,
        )

    @property
    def label(self) -> str:
        """Display label: ``NAME vX.Y``."""
        return f"{self.name} v{self.version}"
