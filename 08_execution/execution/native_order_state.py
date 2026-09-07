"""Rust-backed order lifecycle authority (constitution §1: Execution/Core).

The transition TABLE and terminal set live in Rust (`rust/vayren-core`,
`order_state` module) and are exposed here as read-only views materialized
once at import. This module holds NO independent transition logic — it is a
projection of the Rust authority, verified by handshake (ABI version, state
count, name mapping) and pinned by parity tests.

Fail-closed: a missing/incompatible native library raises at import so the
gate forces a build instead of silently running a stale table.
"""

from __future__ import annotations

import ctypes

from core.native.loader import load_vayren_core

from execution.models.order_state import OrderState

# Declaration order MUST match the Rust `OrderState` discriminants 0..=12.
_STATE_CODES: tuple[str, ...] = (
    "CREATED",
    "VALIDATED",
    "SUBMITTED",
    "ACKNOWLEDGED",
    "PARTIALLY_FILLED",
    "FILLED",
    "REJECTED",
    "CANCEL_PENDING",
    "CANCELLED",
    "MODIFY_PENDING",
    "MODIFIED",
    "EXPIRED",
    "UNKNOWN",
)

_lib = load_vayren_core()

_code_by_state: dict[OrderState, int] = {}
for _index, _name in enumerate(_STATE_CODES):
    try:
        _code_by_state[OrderState(_name)] = _index
    except ValueError as exc:
        raise RuntimeError(f"native order-state vocabulary drift: {exc}") from exc
if len(_code_by_state) != len(_STATE_CODES) or len(OrderState) != len(_STATE_CODES):
    raise RuntimeError("native order-state vocabulary drift: enum/code mismatch")
_state_by_code: dict[int, OrderState] = {code: state for state, code in _code_by_state.items()}


def _materialize_transitions() -> dict[OrderState, frozenset[OrderState]]:
    capacity = 512
    buffer = (ctypes.c_uint32 * capacity)()
    needed = int(_lib.vy_order_transitions(buffer, capacity))
    if needed > capacity:
        raise RuntimeError(f"native transition table overflow: needs {needed}")
    table: dict[OrderState, set[OrderState]] = {state: set() for state in OrderState}
    for slot in range(needed):
        packed = int(buffer[slot])
        from_code, to_code = (packed >> 16) & 0xFFFF, packed & 0xFFFF
        if from_code not in _state_by_code or to_code not in _state_by_code:
            raise RuntimeError(f"native transition references unknown state code: {packed:#x}")
        table[_state_by_code[from_code]].add(_state_by_code[to_code])
    return {state: frozenset(targets) for state, targets in table.items()}


def _materialize_terminal() -> frozenset[OrderState]:
    buffer = (ctypes.c_int32 * 16)()
    count = int(_lib.vy_order_terminal_states(buffer, 16))
    states = set()
    for slot in range(count):
        code = int(buffer[slot])
        if code not in _state_by_code:
            raise RuntimeError(f"native terminal set references unknown code: {code}")
        states.add(_state_by_code[code])
    return frozenset(states)


TRANSITIONS: dict[OrderState, frozenset[OrderState]] = _materialize_transitions()
TERMINAL_STATES: frozenset[OrderState] = _materialize_terminal()


def transition_allowed(from_state: OrderState, to_state: OrderState) -> bool:
    """Single authoritative legality check (Rust table, zero-copy read)."""
    return to_state in TRANSITIONS[from_state]


__all__ = ["TRANSITIONS", "TERMINAL_STATES", "transition_allowed"]
