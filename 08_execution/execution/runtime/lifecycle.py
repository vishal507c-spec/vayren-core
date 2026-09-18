"""Strategy lifecycle — explicit states with guarded transitions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from execution.native_execution import native_lifecycle_transition_allowed


class LifecycleState(Enum):
    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    WARMING_UP = "WARMING_UP"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"
    RECOVERING = "RECOVERING"
    RECONCILING = "RECONCILING"


TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.CREATED: frozenset(
        {LifecycleState.VALIDATING, LifecycleState.RECOVERING, LifecycleState.STOPPED}
    ),
    LifecycleState.VALIDATING: frozenset(
        {LifecycleState.WARMING_UP, LifecycleState.ERROR, LifecycleState.STOPPED}
    ),
    LifecycleState.WARMING_UP: frozenset(
        {LifecycleState.READY, LifecycleState.ERROR, LifecycleState.STOPPED}
    ),
    LifecycleState.READY: frozenset({LifecycleState.RUNNING, LifecycleState.STOPPED}),
    LifecycleState.RUNNING: frozenset(
        {LifecycleState.PAUSED, LifecycleState.STOPPING, LifecycleState.ERROR}
    ),
    LifecycleState.PAUSED: frozenset({LifecycleState.RUNNING, LifecycleState.STOPPING}),
    LifecycleState.STOPPING: frozenset({LifecycleState.STOPPED, LifecycleState.ERROR}),
    LifecycleState.STOPPED: frozenset({LifecycleState.RECOVERING}),
    LifecycleState.ERROR: frozenset({LifecycleState.RECOVERING, LifecycleState.STOPPED}),
    LifecycleState.RECOVERING: frozenset(
        {
            LifecycleState.VALIDATING,
            LifecycleState.RECONCILING,
            LifecycleState.ERROR,
            LifecycleState.STOPPED,
        }
    ),
    # FINAL §L: recovered sessions reconcile broker truth before validation.
    # RECOVERING → VALIDATING stays legal (backward compatible fast path).
    LifecycleState.RECONCILING: frozenset(
        {LifecycleState.VALIDATING, LifecycleState.ERROR, LifecycleState.STOPPED}
    ),
}


class LifecycleError(ValueError):
    """Illegal lifecycle transition attempted."""


@dataclass
class StrategyLifecycle:
    """One strategy instance's lifecycle. Transitions are explicit and checked."""

    state: LifecycleState = LifecycleState.CREATED
    reason: str = ""

    def transition(self, target: LifecycleState, reason: str = "") -> None:
        if not native_lifecycle_transition_allowed(self.state.value, target.value):
            raise LifecycleError(f"illegal transition {self.state.value} -> {target.value}")
        self.state = target
        self.reason = reason

    @property
    def live(self) -> bool:
        """Whether the instance may currently generate orders."""
        return self.state == LifecycleState.RUNNING
