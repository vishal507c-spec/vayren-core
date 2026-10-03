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
    status: str = "ACTIVE"
    timeframe: str = ""
    direction: str = ""
    symbols: tuple[str, ...] = ()
    runtime_state: str = "IDLE"
    description: str = ""
    metadata: tuple[tuple[str, str], ...] = ()
    source_code: str = ""

    @property
    def symbol_count(self) -> int:
        """Total symbols in the strategy universe."""
        return len(self.symbols)

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
            status=self.status,
            timeframe=self.timeframe,
            direction=self.direction,
            symbols=self.symbols,
            runtime_state=self.runtime_state,
            description=self.description,
            metadata=self.metadata,
            source_code=self.source_code,
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
            status=self.status,
            timeframe=self.timeframe,
            direction=self.direction,
            symbols=self.symbols,
            runtime_state=self.runtime_state,
            description=self.description,
            metadata=self.metadata,
            source_code=self.source_code,
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
            status=self.status,
            timeframe=self.timeframe,
            direction=self.direction,
            symbols=self.symbols,
            runtime_state=self.runtime_state,
            description=self.description,
            metadata=self.metadata,
            source_code=self.source_code,
        )

    def with_symbols(self, symbols: tuple[str, ...]) -> "StrategyDefinition":
        """Return a copy with replaced symbols."""
        return StrategyDefinition(
            id=self.id,
            name=self.name,
            version=self.version,
            kind=self.kind,
            params=self.params,
            allocation_pct=self.allocation_pct,
            enabled=self.enabled,
            status=self.status,
            timeframe=self.timeframe,
            direction=self.direction,
            symbols=tuple(symbols),
            runtime_state=self.runtime_state,
            description=self.description,
            metadata=self.metadata,
            source_code=self.source_code,
        )

    def with_status(self, status: str) -> "StrategyDefinition":
        """Return a copy with a new status."""
        return StrategyDefinition(
            id=self.id,
            name=self.name,
            version=self.version,
            kind=self.kind,
            params=self.params,
            allocation_pct=self.allocation_pct,
            enabled=self.enabled,
            status=status,
            timeframe=self.timeframe,
            direction=self.direction,
            symbols=self.symbols,
            runtime_state=self.runtime_state,
            description=self.description,
            metadata=self.metadata,
            source_code=self.source_code,
        )

    def with_runtime_state(self, runtime_state: str) -> "StrategyDefinition":
        """Return a copy with a new runtime state."""
        return StrategyDefinition(
            id=self.id,
            name=self.name,
            version=self.version,
            kind=self.kind,
            params=self.params,
            allocation_pct=self.allocation_pct,
            enabled=self.enabled,
            status=self.status,
            timeframe=self.timeframe,
            direction=self.direction,
            symbols=self.symbols,
            runtime_state=runtime_state,
            description=self.description,
            metadata=self.metadata,
            source_code=self.source_code,
        )

    @property
    def metadata_dict(self) -> dict[str, str]:
        """Convert tuple metadata to dict."""
        return dict(self.metadata)

    @property
    def label(self) -> str:
        """Display label: ``NAME vX.Y``."""
        return f"{self.name} v{self.version}"
