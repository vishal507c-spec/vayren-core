"""Rust-backed order lifecycle authority (AI_ENTRY.md §1: Execution/Core).

The transition TABLE and terminal set live ONLY in Rust
(`rust/vayren-core`, `order_state` module). This bridge holds no table and
no state-machine logic: every legality check is a direct kernel call, and
the enum↔code mapping below is pure boundary vocabulary (drift-checked at
import against the kernel's state count).

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

_lib.vy_order_transition_allowed.argtypes = [ctypes.c_int32, ctypes.c_int32]
_lib.vy_order_transition_allowed.restype = ctypes.c_int32

_lib.vy_order_state_count.argtypes = []
_lib.vy_order_state_count.restype = ctypes.c_int32

_lib.vy_order_transitions.argtypes = [
    ctypes.POINTER(ctypes.c_uint32),
    ctypes.c_uint32,
]
_lib.vy_order_transitions.restype = ctypes.c_uint32

_lib.vy_order_terminal_states.argtypes = [
    ctypes.POINTER(ctypes.c_int32),
    ctypes.c_uint32,
]
_lib.vy_order_terminal_states.restype = ctypes.c_uint32

_code_by_state: dict[OrderState, int] = {}
for _index, _name in enumerate(_STATE_CODES):
    try:
        _code_by_state[OrderState(_name)] = _index
    except ValueError as exc:
        raise RuntimeError(f"native order-state vocabulary drift: {exc}") from exc
if len(_code_by_state) != len(_STATE_CODES) or len(OrderState) != len(_STATE_CODES):
    raise RuntimeError("native order-state vocabulary drift: enum/code mismatch")


def _code(state: OrderState) -> int:
    return _code_by_state[state]


def transition_allowed(from_state: OrderState, to_state: OrderState) -> bool:
    """Single authoritative legality check — decided by the Rust table."""
    return bool(_lib.vy_order_transition_allowed(_code(from_state), _code(to_state)))


def _state_by_code(code: int) -> OrderState:
    return OrderState(_STATE_CODES[code])


def _transition_table() -> dict[OrderState, tuple[OrderState, ...]]:
    """Read-only projection of the Rust transition table (Rust decides every edge).

    The buffer is sized at the full `state_count²` cross product and the
    kernel's written count is the authority, so the projection never depends on
    how the kernel treats a zero-capacity probe.
    """
    count = int(_lib.vy_order_state_count())
    if count <= 0:
        raise RuntimeError("native order-state vocabulary drift: kernel reports no states")
    capacity = count * count
    packed = (ctypes.c_uint32 * capacity)()
    edges = int(_lib.vy_order_transitions(packed, capacity))
    if edges < 0 or edges > capacity:
        raise RuntimeError(f"native order-state table reported {edges} edges")
    table: dict[OrderState, list[OrderState]] = {_state_by_code(code): [] for code in range(count)}
    for index in range(edges):
        edge = int(packed[index])
        table[_state_by_code(edge >> 16)].append(_state_by_code(edge & 0xFFFF))
    return {state: tuple(targets) for state, targets in table.items()}


#: Every legal edge, projected from the kernel at import. Pure read-only
#: mirror — the authority stays in Rust (`transition_allowed` still asks the
#: kernel for any single decision).
TRANSITIONS: dict[OrderState, tuple[OrderState, ...]] = _transition_table()


def _terminal_states() -> tuple[OrderState, ...]:
    """The kernel's terminal set.

    `vy_order_terminal_states` returns `min(len, cap)` — a zero-capacity probe
    therefore answers 0, NOT the size, so the buffer is allocated at the state
    count and the kernel's written count is the authority. (Asking with cap=0
    here silently yields an EMPTY terminal set, which makes every filled order
    look open forever.)
    """
    capacity = int(_lib.vy_order_state_count())
    if capacity <= 0:
        raise RuntimeError("native order-state vocabulary drift: kernel reports no states")
    codes = (ctypes.c_int32 * capacity)()
    written = int(_lib.vy_order_terminal_states(codes, capacity))
    if written <= 0:
        raise RuntimeError("native order-state terminal set is empty")
    return tuple(_state_by_code(int(codes[index])) for index in range(written))


#: The terminal set, projected from the kernel at import (read-only mirror).
TERMINAL_STATES: frozenset[OrderState] = frozenset(_terminal_states())


__all__ = [
    "TERMINAL_STATES",
    "TRANSITIONS",
    "transition_allowed",
]
