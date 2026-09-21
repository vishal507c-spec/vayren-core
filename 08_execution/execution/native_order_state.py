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


def _code(state: OrderState) -> int:
    return _code_by_state[state]


def transition_allowed(from_state: OrderState, to_state: OrderState) -> bool:
    """Single authoritative legality check — decided by the Rust table."""
    return bool(_lib.vy_order_transition_allowed(_code(from_state), _code(to_state)))


__all__ = ["transition_allowed"]
