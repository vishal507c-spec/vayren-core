"""StrategyRegistry — the dynamic registry of strategy kinds and definitions.

Strategies are never hard-coded into the UI or the runner: the UI lists
whatever this registry holds, and the backtest runner resolves logic
factories from it. Kinds (implementations) and definitions (configured
instances) are registered explicitly — no scanning, no magic.
"""

from collections.abc import Callable
from logging import getLogger

from strategy.models.definition import StrategyDefinition
from strategy.models.parameters import ParameterSpec, StrategyParameters
from strategy.runtime import StrategyLogic

logger = getLogger(__name__)

StrategyFactory = Callable[[StrategyParameters], StrategyLogic]


class StrategyRegistryError(KeyError):
    """Raised when a strategy id or kind is not registered."""


class StrategyRegistry:
    """Holds strategy kinds (logic factories) and definitions (instances).

    Pure in-memory state with a tiny API: register/list/get plus the
    mutations the lab UI needs (add, duplicate, remove, enable, allocation,
    parameters). Every mutation returns the new definition so callers can
    publish :class:`StrategiesListed` facts.
    """

    def __init__(self) -> None:
        self._kinds: dict[str, StrategyFactory] = {}
        self._specs: dict[str, tuple[ParameterSpec, ...]] = {}
        self._definitions: dict[str, StrategyDefinition] = {}

    # ── kinds ─────────────────────────────────────────────────────────

    def register_kind(
        self,
        kind: str,
        factory: StrategyFactory,
        specs: tuple[ParameterSpec, ...],
    ) -> None:
        """Register a strategy implementation under `kind`."""
        if kind in self._kinds:
            raise ValueError(f"strategy kind already registered: {kind}")
        self._kinds[kind] = factory
        self._specs[kind] = tuple(specs)

    def has_kind(self, kind: str) -> bool:
        """True when `kind` is registered."""
        return kind in self._kinds

    def factory(self, kind: str) -> StrategyFactory:
        """The logic factory for `kind`."""
        if kind not in self._kinds:
            raise StrategyRegistryError(f"unknown strategy kind: {kind}")
        return self._kinds[kind]

    def param_specs(self, kind: str) -> tuple[ParameterSpec, ...]:
        """The parameter declarations for `kind`."""
        if kind not in self._specs:
            raise StrategyRegistryError(f"unknown strategy kind: {kind}")
        return self._specs[kind]

    @property
    def kinds(self) -> tuple[str, ...]:
        """All registered kind names, sorted."""
        return tuple(sorted(self._kinds))

    # ── definitions ───────────────────────────────────────────────────

    def register_definition(self, definition: StrategyDefinition) -> StrategyDefinition:
        """Add a configured strategy instance; rejects duplicate ids."""
        if definition.id in self._definitions:
            raise ValueError(f"strategy already registered: {definition.id}")
        if definition.kind not in self._kinds:
            raise StrategyRegistryError(f"unknown strategy kind: {definition.kind}")
        validated = definition.with_params(
            definition.params.validated(self.param_specs(definition.kind))
        )
        self._definitions[definition.id] = validated
        logger.info("Strategy registered: %s (%s)", validated.label, validated.kind)
        return validated

    def get(self, strategy_id: str) -> StrategyDefinition:
        """The definition for `strategy_id`."""
        if strategy_id not in self._definitions:
            raise StrategyRegistryError(f"unknown strategy: {strategy_id}")
        return self._definitions[strategy_id]

    def contains(self, strategy_id: str) -> bool:
        """True when `strategy_id` is registered."""
        return strategy_id in self._definitions

    def list(self) -> tuple[StrategyDefinition, ...]:
        """All definitions in registration order."""
        return tuple(self._definitions.values())

    def enabled(self) -> tuple[StrategyDefinition, ...]:
        """Definitions that participate in runs, in registration order."""
        return tuple(definition for definition in self.list() if definition.enabled)

    def remove(self, strategy_id: str) -> StrategyDefinition:
        """Remove a definition and return it."""
        if strategy_id not in self._definitions:
            raise StrategyRegistryError(f"unknown strategy: {strategy_id}")
        return self._definitions.pop(strategy_id)

    def set_enabled(self, strategy_id: str, enabled: bool) -> StrategyDefinition:
        """Enable or disable a definition; returns the updated definition."""
        return self._replace(strategy_id, lambda d: d.with_enabled(enabled))

    def set_allocation(self, strategy_id: str, allocation_pct: float) -> StrategyDefinition:
        """Set the allocation weight of a definition (clamped to 0-100)."""
        clamped = max(0.0, min(100.0, float(allocation_pct)))
        return self._replace(strategy_id, lambda d: d.with_allocation(clamped))

    def set_params(self, strategy_id: str, params: StrategyParameters) -> StrategyDefinition:
        """Replace the parameters of a definition (validated against its kind)."""
        definition = self.get(strategy_id)
        validated = params.validated(self.param_specs(definition.kind))
        return self._replace(strategy_id, lambda d: d.with_params(validated))

    def duplicate(self, strategy_id: str, new_id: str | None = None) -> StrategyDefinition:
        """Duplicate a definition under a fresh unique id."""
        source = self.get(strategy_id)
        candidate = new_id or f"{source.id}-copy"
        while candidate in self._definitions:
            candidate = f"{candidate}-copy"
        copy = StrategyDefinition(
            id=candidate,
            name=source.name,
            version=source.version,
            kind=source.kind,
            params=source.params,
            allocation_pct=source.allocation_pct,
            enabled=False,
        )
        self._definitions[copy.id] = copy
        logger.info("Strategy duplicated: %s → %s", source.id, copy.id)
        return copy

    def _replace(
        self, strategy_id: str, mutate: Callable[[StrategyDefinition], StrategyDefinition]
    ) -> StrategyDefinition:
        updated = mutate(self.get(strategy_id))
        self._definitions[strategy_id] = updated
        return updated
