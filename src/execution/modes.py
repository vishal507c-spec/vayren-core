"""Execution modes and live safety gates. Default is PAPER, always.

LIVE requires every gate explicitly satisfied; any failure forces PAPER.
There is no path from default configuration to real orders.

Which gate names are missing, what counts as an ON flag, and when a LIVE
request degrades to PAPER are decided by Rust (`execution_engine::modes`,
bridged through :mod:`execution.native_execution`). What stays here is the
vocabulary Python callers pass around and the plain five-flag holder.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum

from execution.native_execution import (
    native_arm_transition,
    native_flags_to_mask,
    native_gates_mask,
    native_mask_to_flags,
    native_missing_gates,
    native_resolve_mode,
)


class ExecutionMode(Enum):
    PAPER = "PAPER"
    SANDBOX = "SANDBOX"
    LIVE = "LIVE"


class LiveArm(Enum):
    """Explicit live arming state. Credentials alone never arm anything.

    DISARMED → ARMING → ARMED → RUNNING, with HALTED reachable from any
    armed state. Arming requires an explicit call with a reason; it is
    recorded in the journal. Consent is never inferred.
    """

    DISARMED = "DISARMED"
    ARMING = "ARMING"
    ARMED = "ARMED"
    RUNNING = "RUNNING"
    HALTED = "HALTED"


class ArmingError(ValueError):
    """Illegal arming transition attempted."""


def arm_transition(current: LiveArm, target: LiveArm) -> LiveArm:
    """Validate one arming step; returns target or raises ArmingError."""
    try:
        val = native_arm_transition(current.value, target.value)
        return LiveArm(val)
    except ValueError as exc:
        raise ArmingError(str(exc)) from exc


# Configuration keys, in gate order: bit i of the kernel mask is gate i.
GATE_ENV_KEYS = (
    "LIVE_TRADING_ENABLED",
    "BROKER_LIVE_ENABLED",
    "ACCOUNT_CONFIRMED",
    "RISK_LIMITS_VALID",
    "KILL_SWITCH_OFF",
)


@dataclass(frozen=True)
class ModeGates:
    """The five mandatory live gates (§14 mission spec)."""

    live_trading_enabled: bool = False
    broker_live_enabled: bool = False
    account_confirmed: bool = False
    risk_limits_valid: bool = False
    kill_switch_off: bool = False

    @property
    def mask(self) -> int:
        """The flags as the kernel encodes them (bit i = field i)."""
        return native_flags_to_mask(
            (
                self.live_trading_enabled,
                self.broker_live_enabled,
                self.account_confirmed,
                self.risk_limits_valid,
                self.kill_switch_off,
            )
        )

    @property
    def all_satisfied(self) -> bool:
        return not self.missing()

    def missing(self) -> tuple[str, ...]:
        """Unsatisfied gate names, in the kernel's gate order."""
        return native_missing_gates(self.mask)


def gates_from_env(env: dict[str, str] | None = None) -> ModeGates:
    """Read gates from explicit configuration (env mapping by default).

    Only the raw values are read here — the kernel decides which of them are
    ON. Fail-closed parsing: nothing but an explicit ``"true"`` (any case,
    padded) arms a gate, so ``"1"``/``"yes"`` stay OFF with no truthy
    surprises.
    """
    source = env if env is not None else os.environ
    values = [str(source.get(name, "")) for name in GATE_ENV_KEYS]
    return ModeGates(*native_mask_to_flags(native_gates_mask(values)))


def resolve_mode(
    requested: ExecutionMode, gates: ModeGates
) -> tuple[ExecutionMode, tuple[str, ...]]:
    """Resolve the effective mode. LIVE without all gates degrades to PAPER.

    Returns (effective_mode, downgrade_reasons). Downgrade is never silent:
    every missing gate is reported. The kernel owns the decision and the
    reason text.
    """
    mode, reasons = native_resolve_mode(requested.value, gates.mask)
    return ExecutionMode(mode), reasons
