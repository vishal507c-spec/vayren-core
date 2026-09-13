"""State machine: explicit lifecycle, no arbitrary status changes."""

from __future__ import annotations

from scripts.migration import state_machine


def test_allowed_chain_covers_full_lifecycle() -> None:
    chain = [
        "DISCOVERED",
        "ANALYZED",
        "PLANNED",
        "READY",
        "IMPLEMENTING",
        "IMPLEMENTED",
        "PARITY_TESTING",
        "PARITY_VERIFIED",
        "SHADOW_VALIDATED",
        "INTEGRATED",
        "RUST_CANONICAL",
        "PYTHON_DEPRECATED",
        "PYTHON_QUARANTINED",
        "PYTHON_REMOVED",
        "FINAL_VERIFIED",
        "MIGRATED",
    ]
    pairs = list(zip(chain, chain[1:], strict=False))
    assert len(pairs) == len(chain) - 1
    for frm, to in pairs:
        assert state_machine.is_allowed(frm, to), f"{frm} -> {to}"
    assert len(chain) == 16


def test_arbitrary_jumps_refused() -> None:
    assert not state_machine.is_allowed("DISCOVERED", "MIGRATED")
    assert not state_machine.is_allowed("IMPLEMENTED", "RUST_CANONICAL")
    assert not state_machine.is_allowed("MIGRATED", "DISCOVERED")
    ok, _ = state_machine.check_transition("DISCOVERED", "MIGRATED")
    assert not ok


def test_regression_path_exists_but_never_counts_as_progress() -> None:
    assert state_machine.is_allowed("RUST_CANONICAL", "REGRESSION")
    assert state_machine.is_allowed("REGRESSION", "BLOCKED")
    assert not state_machine.is_allowed("REGRESSION", "MIGRATED")


def test_unknown_states_fail_closed() -> None:
    ok, _ = state_machine.check_transition("NOPE", "MIGRATED")
    assert not ok
    ok, _ = state_machine.check_transition("DISCOVERED", "NOPE")
    assert not ok


def test_every_advanced_state_has_requirements() -> None:
    for state in ("PARITY_VERIFIED", "RUST_CANONICAL", "PYTHON_REMOVED", "MIGRATED"):
        assert state_machine.requirements_for(state), state
