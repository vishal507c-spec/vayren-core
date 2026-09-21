"""Parity: Rust live-safety gate kernel vs the frozen Python rules.

The references below are verbatim copies of the pre-migration `modes.py`
decisions (gate vocabulary, fail-closed flag grammar, LIVE→PAPER
degradation), kept in the TEST as oracles. A safety rule that drifts here
would let a partially-confirmed live session through, so every combination of
gates and every tricky configuration value is compared.
"""

from __future__ import annotations

import itertools
import random

from execution.modes import GATE_ENV_KEYS, ExecutionMode, ModeGates, gates_from_env, resolve_mode


def _ref_flag(raw: str) -> bool:
    """Old `gates_from_env` rule: exact "true", case-insensitive, trimmed."""
    return str(raw).strip().lower() == "true"


def _ref_gates(values: dict[str, str]) -> ModeGates:
    return ModeGates(
        live_trading_enabled=_ref_flag(values.get("LIVE_TRADING_ENABLED", "")),
        broker_live_enabled=_ref_flag(values.get("BROKER_LIVE_ENABLED", "")),
        account_confirmed=_ref_flag(values.get("ACCOUNT_CONFIRMED", "")),
        risk_limits_valid=_ref_flag(values.get("RISK_LIMITS_VALID", "")),
        kill_switch_off=_ref_flag(values.get("KILL_SWITCH_OFF", "")),
    )


def _ref_missing(gates: ModeGates) -> tuple[str, ...]:
    names = {
        "LIVE_TRADING_ENABLED": gates.live_trading_enabled,
        "BROKER_LIVE_ENABLED": gates.broker_live_enabled,
        "ACCOUNT_CONFIRMED": gates.account_confirmed,
        "RISK_LIMITS_VALID": gates.risk_limits_valid,
        "KILL_SWITCH_OFF": gates.kill_switch_off,
    }
    return tuple(name for name, on in names.items() if not on)


def _ref_resolve(
    requested: ExecutionMode, gates: ModeGates
) -> tuple[ExecutionMode, tuple[str, ...]]:
    if requested == ExecutionMode.LIVE and not _ref_missing(gates):
        return ExecutionMode.LIVE, ()
    if requested == ExecutionMode.LIVE:
        return ExecutionMode.PAPER, tuple(f"live gate off: {n}" for n in _ref_missing(gates))
    if requested == ExecutionMode.SANDBOX:
        return ExecutionMode.SANDBOX, ()
    return ExecutionMode.PAPER, ()


def test_only_an_explicit_true_arms_a_gate() -> None:
    tricky = {
        "LIVE_TRADING_ENABLED": "1",
        "BROKER_LIVE_ENABLED": "yes",
        "ACCOUNT_CONFIRMED": "TRUE",
        "RISK_LIMITS_VALID": " true ",
        "KILL_SWITCH_OFF": "True",
    }
    gates = gates_from_env(tricky)
    assert gates == _ref_gates(tricky)
    assert gates.live_trading_enabled is False
    assert gates.broker_live_enabled is False
    assert gates.account_confirmed is True
    assert gates.kill_switch_off is True


def test_missing_gate_names_come_back_in_gate_order() -> None:
    gates = ModeGates(True, False, True, False, False)
    assert gates.missing() == ("BROKER_LIVE_ENABLED", "RISK_LIMITS_VALID", "KILL_SWITCH_OFF")
    assert gates.all_satisfied is False
    assert ModeGates(True, True, True, True, True).missing() == ()
    assert ModeGates(True, True, True, True, True).all_satisfied is True


def test_live_without_every_gate_degrades_to_paper_loudly() -> None:
    mode, notes = resolve_mode(ExecutionMode.LIVE, ModeGates(True, True, True, True, False))
    assert mode == ExecutionMode.PAPER
    assert notes == ("live gate off: KILL_SWITCH_OFF",)
    assert resolve_mode(ExecutionMode.LIVE, ModeGates(True, True, True, True, True)) == (
        ExecutionMode.LIVE,
        (),
    )


def test_paper_and_sandbox_ignore_the_gates() -> None:
    for requested in (ExecutionMode.PAPER, ExecutionMode.SANDBOX):
        mode, notes = resolve_mode(requested, ModeGates())
        assert mode == requested
        assert notes == ()


def test_mask_encoding_matches_the_field_order() -> None:
    for flags in itertools.product((False, True), repeat=5):
        gates = ModeGates(*flags)
        expected = sum(1 << index for index, on in enumerate(flags) if on)
        assert gates.mask == expected
        assert gates == gates_from_env(
            {key: "true" if on else "off" for key, on in zip(GATE_ENV_KEYS, flags, strict=True)}
        )


def test_gate_fuzz() -> None:
    rng = random.Random(20260925)
    spellings = ["true", "TRUE", " True", "true ", "", "1", "yes", "t", "false", "0"]
    for _ in range(400):
        values = {key: rng.choice(spellings) for key in GATE_ENV_KEYS}
        gates = gates_from_env(values)
        assert gates == _ref_gates(values), values
        assert gates.missing() == _ref_missing(gates), values
        for requested in ExecutionMode:
            assert resolve_mode(requested, gates) == _ref_resolve(requested, gates), (
                values,
                requested,
            )
