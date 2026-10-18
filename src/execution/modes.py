"""Execution modes and live safety gates. Exactly two modes, no third.

- **BACKTEST** replays stored candles through the backtest engine and never
  touches a venue.
- **LIVE** trades the real broker with real money: explicit operator consent,
  mandatory risk checks, broker reconciliation.

There is no simulated venue any more. A LIVE request whose gates are not all
satisfied is **refused** with the exact missing gate names — it is never
downgraded to another mode, because the only other mode (BACKTEST) executes no
orders at all and would silently pretend the engine was running.

Which gate names count as ON, and the gate order, are decided by Rust
(`execution_engine::modes`, bridged through :mod:`execution.native_execution`).
What stays here is the vocabulary Python callers pass around and the plain
five-flag holder.
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
)


class ExecutionMode(Enum):
    """The only two operating modes. PAPER/SANDBOX no longer exist."""

    BACKTEST = "BACKTEST"
    LIVE = "LIVE"


#: Retired labels kept only to produce an actionable refusal, never to run.
RETIRED_MODE_LABELS: tuple[str, ...] = ("PAPER", "SANDBOX")


class ExecutionModeError(ValueError):
    """An unknown or retired execution mode was requested."""


class LiveModeError(RuntimeError):
    """LIVE requested without the mandatory gates it requires.

    Fail-closed: the caller reports NOT READY / SAFE STOP with these reasons.
    There is no fallback mode to switch into.
    """

    def __init__(self, reasons: tuple[str, ...]) -> None:
        self.reasons = tuple(reasons)
        super().__init__("LIVE unavailable — missing gates: " + ", ".join(self.reasons))


def parse_mode(value: object) -> ExecutionMode:
    """Parse a persisted/remote mode label into one of the two modes.

    Legacy ``PAPER``/``SANDBOX`` values are REFUSED with an explicit reason
    instead of being reinterpreted — a stored simulated mode must never
    silently become LIVE.
    """
    text = str(value or "").strip().upper()
    if text in RETIRED_MODE_LABELS:
        raise ExecutionModeError(
            f"execution mode {text!r} was retired — simulated execution no longer "
            "exists; select LIVE explicitly (or BACKTEST for historical replay)"
        )
    try:
        return ExecutionMode(text)
    except ValueError as exc:
        raise ExecutionModeError(
            f"unknown execution mode {value!r}; valid modes are "
            f"{[mode.value for mode in ExecutionMode]}"
        ) from exc


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
    """Resolve the effective mode, or refuse.

    BACKTEST resolves to itself and needs no venue. LIVE resolves only when
    every gate is satisfied; otherwise :class:`LiveModeError` is raised
    with the kernel's own missing-gate names. There is no downgrade target —
    degrading LIVE to a simulated venue is exactly what this architecture
    removed.
    """
    if requested is ExecutionMode.BACKTEST:
        return ExecutionMode.BACKTEST, ()
    missing = gates.missing()
    if missing:
        raise LiveModeError(missing)
    return ExecutionMode.LIVE, ()
