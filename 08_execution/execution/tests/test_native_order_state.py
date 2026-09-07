"""Parity: the Rust-owned lifecycle table vs the frozen behavioral contract.

The 13×13 reference below encodes the EFFECTIVE pre-migration behavior
(the old `TRANSITIONS` literal plus the engine's UNKNOWN-entry bypass for
every non-terminal state). The Rust table must match it exactly — any drift
fails the gate. This reference lives in the TEST only; production holds no
Python table (migration §12: single authority).
"""

from __future__ import annotations

from execution.models.order import TERMINAL_STATES, TRANSITIONS, OrderState
from execution.native_order_state import _STATE_CODES, transition_allowed

_REFERENCE: dict[str, frozenset[str]] = {
    "CREATED": frozenset({"VALIDATED", "REJECTED", "UNKNOWN"}),
    "VALIDATED": frozenset({"SUBMITTED", "REJECTED", "EXPIRED", "UNKNOWN"}),
    "SUBMITTED": frozenset({"ACKNOWLEDGED", "REJECTED", "EXPIRED", "UNKNOWN"}),
    "ACKNOWLEDGED": frozenset(
        {
            "PARTIALLY_FILLED",
            "FILLED",
            "REJECTED",
            "CANCEL_PENDING",
            "MODIFY_PENDING",
            "EXPIRED",
            "UNKNOWN",
        }
    ),
    "PARTIALLY_FILLED": frozenset(
        {
            "PARTIALLY_FILLED",
            "FILLED",
            "CANCEL_PENDING",
            "MODIFY_PENDING",
            "EXPIRED",
            "UNKNOWN",
        }
    ),
    "CANCEL_PENDING": frozenset({"CANCELLED", "FILLED", "UNKNOWN"}),
    "MODIFY_PENDING": frozenset({"MODIFIED", "FILLED", "UNKNOWN"}),
    "MODIFIED": frozenset(
        {
            "PARTIALLY_FILLED",
            "FILLED",
            "REJECTED",
            "CANCEL_PENDING",
            "MODIFY_PENDING",
            "EXPIRED",
            "UNKNOWN",
        }
    ),
    "UNKNOWN": frozenset(),
    "FILLED": frozenset(),
    "REJECTED": frozenset(),
    "CANCELLED": frozenset(),
    "EXPIRED": frozenset(),
}


def test_state_codes_match_declaration_order() -> None:
    assert tuple(state.value for state in OrderState) == _STATE_CODES
    assert len(_STATE_CODES) == len(OrderState) == 13


def test_materialized_table_matches_reference() -> None:
    assert set(TRANSITIONS) == set(OrderState)
    for state in OrderState:
        actual = {target.value for target in TRANSITIONS[state]}
        assert actual == _REFERENCE[state.value], f"drift at {state.value}"


def test_full_matrix_matches_reference() -> None:
    for source in OrderState:
        for target in OrderState:
            expected = target.value in _REFERENCE[source.value]
            assert transition_allowed(source, target) is expected, (
                f"{source.value} -> {target.value}"
            )


def test_terminal_set_matches_reference() -> None:
    assert {state.value for state in TERMINAL_STATES} == {
        "FILLED",
        "REJECTED",
        "CANCELLED",
        "EXPIRED",
    }


def test_unknown_exits_only_via_reconcile() -> None:
    assert TRANSITIONS[OrderState.UNKNOWN] == frozenset()
    for target in OrderState:
        assert transition_allowed(OrderState.UNKNOWN, target) is False
