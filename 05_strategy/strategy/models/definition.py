"""StrategyDefinition — immutable description of a registered strategy."""

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
