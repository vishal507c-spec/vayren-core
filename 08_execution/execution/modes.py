"""Execution modes and live safety gates. Default is PAPER, always.

LIVE requires every gate explicitly satisfied; any failure forces PAPER.
There is no path from default configuration to real orders.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum

from execution.native_execution import native_arm_transition


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


@dataclass(frozen=True)
class ModeGates:
    """The five mandatory live gates (§14 mission spec)."""

    live_trading_enabled: bool = False
    broker_live_enabled: bool = False
    account_confirmed: bool = False
    risk_limits_valid: bool = False
    kill_switch_off: bool = False

    @property
    def all_satisfied(self) -> bool:
        return (
            self.live_trading_enabled
            and self.broker_live_enabled
            and self.account_confirmed
            and self.risk_limits_valid
            and self.kill_switch_off
        )

    def missing(self) -> tuple[str, ...]:
        missing = []
        if not self.live_trading_enabled:
            missing.append("LIVE_TRADING_ENABLED")
        if not self.broker_live_enabled:
            missing.append("BROKER_LIVE_ENABLED")
        if not self.account_confirmed:
            missing.append("ACCOUNT_CONFIRMED")
        if not self.risk_limits_valid:
            missing.append("RISK_LIMITS_VALID")
        if not self.kill_switch_off:
            missing.append("KILL_SWITCH_OFF")
        return tuple(missing)


def gates_from_env(env: dict[str, str] | None = None) -> ModeGates:
    """Read gates from explicit configuration (env mapping by default).

    Values must be the exact string ``"true"`` (case-insensitive) — any
    other value, including ``"1"``/``"yes"``, counts as OFF. Fail-closed
    parsing: no truthy surprises.
    """
    source = env if env is not None else os.environ

    def flag(name: str) -> bool:
        return str(source.get(name, "")).strip().lower() == "true"

    return ModeGates(
        live_trading_enabled=flag("LIVE_TRADING_ENABLED"),
        broker_live_enabled=flag("BROKER_LIVE_ENABLED"),
        account_confirmed=flag("ACCOUNT_CONFIRMED"),
        risk_limits_valid=flag("RISK_LIMITS_VALID"),
        kill_switch_off=flag("KILL_SWITCH_OFF"),
    )


def resolve_mode(
    requested: ExecutionMode, gates: ModeGates
) -> tuple[ExecutionMode, tuple[str, ...]]:
    """Resolve the effective mode. LIVE without all gates degrades to PAPER.

    Returns (effective_mode, downgrade_reasons). Downgrade is never silent:
    every missing gate is reported.
    """
    if requested == ExecutionMode.LIVE and gates.all_satisfied:
        return ExecutionMode.LIVE, ()
    if requested == ExecutionMode.LIVE:
        return ExecutionMode.PAPER, tuple(f"live gate off: {name}" for name in gates.missing())
    if requested == ExecutionMode.SANDBOX:
        return ExecutionMode.SANDBOX, ()
    return ExecutionMode.PAPER, ()
