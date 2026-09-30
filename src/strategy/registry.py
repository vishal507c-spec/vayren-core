"""StrategyRegistry — the dynamic registry of strategy kinds and definitions.

Strategies are never hard-coded into the UI or the runner: the UI lists
whatever this registry holds, and the backtest runner resolves logic
factories from it. Kinds (implementations) and definitions (configured
instances) are registered explicitly — no scanning, no magic.
"""

import math
from collections.abc import Callable
from logging import getLogger
from threading import RLock

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

    All public methods are guarded by one lock, so concurrent UI/worker
    threads can never interleave a register/list/remove sequence.
    """

    def __init__(self) -> None:
        self._lock = RLock()
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
        with self._lock:
            if kind in self._kinds:
                raise StrategyRegistryError(f"strategy kind already registered: {kind}")
            self._kinds[kind] = factory
            self._specs[kind] = tuple(specs)

    def unregister_kind(self, kind: str) -> None:
        """Remove a registered kind (and its specs); unknown kind raises."""
        with self._lock:
            if kind not in self._kinds:
                raise StrategyRegistryError(f"unknown strategy kind: {kind}")
            del self._kinds[kind]
            del self._specs[kind]

    def update_kind(
        self,
        kind: str,
        factory: StrategyFactory,
        specs: tuple[ParameterSpec, ...],
    ) -> None:
        """Replace the factory and specs of a registered kind."""
        with self._lock:
            if kind not in self._kinds:
                raise StrategyRegistryError(f"unknown strategy kind: {kind}")
            self._kinds[kind] = factory
            self._specs[kind] = tuple(specs)

    def has_kind(self, kind: str) -> bool:
        """True when `kind` is registered."""
        with self._lock:
            return kind in self._kinds

    def factory(self, kind: str) -> StrategyFactory:
        """The logic factory for `kind`."""
        with self._lock:
            if kind not in self._kinds:
                raise StrategyRegistryError(f"unknown strategy kind: {kind}")
            return self._kinds[kind]

    def param_specs(self, kind: str) -> tuple[ParameterSpec, ...]:
        """The parameter declarations for `kind`."""
        with self._lock:
            if kind not in self._specs:
                raise StrategyRegistryError(f"unknown strategy kind: {kind}")
            return self._specs[kind]

    @property
    def kinds(self) -> tuple[str, ...]:
        """All registered kind names, sorted."""
        with self._lock:
            return tuple(sorted(self._kinds))

    # ── definitions ───────────────────────────────────────────────────

    def register_definition(self, definition: StrategyDefinition) -> StrategyDefinition:
        """Add a configured strategy instance; rejects duplicate ids."""
        with self._lock:
            if definition.id in self._definitions:
                raise ValueError(f"strategy already registered: {definition.id}")
            if definition.kind not in self._kinds:
                raise StrategyRegistryError(f"unknown strategy kind: {definition.kind}")
            validated = definition.with_params(
                definition.params.validated(self._specs[definition.kind])
            )
            self._definitions[definition.id] = validated
            logger.info("Strategy registered: %s (%s)", validated.label, validated.kind)
            return validated

    def get(self, strategy_id: str) -> StrategyDefinition:
        """The definition for `strategy_id`."""
        with self._lock:
            if strategy_id not in self._definitions:
                raise StrategyRegistryError(f"unknown strategy: {strategy_id}")
            return self._definitions[strategy_id]

    def contains(self, strategy_id: str) -> bool:
        """True when `strategy_id` is registered."""
        with self._lock:
            return strategy_id in self._definitions

    def list(self) -> tuple[StrategyDefinition, ...]:
        """All definitions in registration order."""
        with self._lock:
            return tuple(self._definitions.values())

    def enabled(self) -> tuple[StrategyDefinition, ...]:
        """Definitions that participate in runs, in registration order."""
        with self._lock:
            return tuple(d for d in self._definitions.values() if d.enabled)

    def remove(self, strategy_id: str) -> StrategyDefinition:
        """Remove a definition and return it."""
        with self._lock:
            if strategy_id not in self._definitions:
                raise StrategyRegistryError(f"unknown strategy: {strategy_id}")
            return self._definitions.pop(strategy_id)

    def set_enabled(self, strategy_id: str, enabled: bool) -> StrategyDefinition:
        """Enable or disable a definition; returns the updated definition."""
        return self._replace(strategy_id, lambda d: d.with_enabled(enabled))

    def set_allocation(self, strategy_id: str, allocation_pct: float) -> StrategyDefinition:
        """Set the allocation weight of a definition (0-100, else ValueError)."""
        if isinstance(allocation_pct, bool):
            raise ValueError(f"allocation_pct must be a number, got {allocation_pct!r}")
        try:
            value = float(allocation_pct)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"allocation_pct must be a number, got {allocation_pct!r}") from exc
        if not math.isfinite(value) or not 0.0 <= value <= 100.0:
            raise ValueError(f"allocation_pct must be within [0, 100], got {value!r}")
        return self._replace(strategy_id, lambda d: d.with_allocation(value))

    def set_params(self, strategy_id: str, params: StrategyParameters) -> StrategyDefinition:
        """Replace the parameters of a definition (validated against its kind)."""
        with self._lock:
            if strategy_id not in self._definitions:
                raise StrategyRegistryError(f"unknown strategy: {strategy_id}")
            definition = self._definitions[strategy_id]
            if definition.kind not in self._specs:
                raise StrategyRegistryError(f"unknown strategy kind: {definition.kind}")
            validated = params.validated(self._specs[definition.kind])
            updated = definition.with_params(validated)
            self._definitions[strategy_id] = updated
            return updated

    def duplicate(self, strategy_id: str, new_id: str | None = None) -> StrategyDefinition:
        """Duplicate a definition under a fresh unique id.

        An explicitly requested ``new_id`` that already exists raises
        instead of silently gaining a ``-copy`` suffix; only the
        auto-generated id walks forward until it is unique.
        """
        with self._lock:
            if strategy_id not in self._definitions:
                raise StrategyRegistryError(f"unknown strategy: {strategy_id}")
            source = self._definitions[strategy_id]
            if new_id is not None:
                if new_id in self._definitions:
                    raise StrategyRegistryError(f"strategy already registered: {new_id}")
                candidate = new_id
            else:
                candidate = f"{source.id}-copy"
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
        with self._lock:
            if strategy_id not in self._definitions:
                raise StrategyRegistryError(f"unknown strategy: {strategy_id}")
            updated = mutate(self._definitions[strategy_id])
            self._definitions[strategy_id] = updated
            return updated
