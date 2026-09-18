"""Rust-backed execution core authority (constitution §1: Execution/Core).

Pure calculations and state-machine checks live in Rust (`rust/vayren-core`,
`execution_engine` and `order_state` modules) and are bridged here via ctypes.
"""

from __future__ import annotations

import ctypes

from core.native.loader import load_vayren_core

_lib = load_vayren_core()

_ARM_CODE: dict[str, int] = {
    "DISARMED": 0,
    "ARMING": 1,
    "ARMED": 2,
    "RUNNING": 3,
    "HALTED": 4,
}
_ARM_BY_CODE: dict[int, str] = {v: k for k, v in _ARM_CODE.items()}

_LIFECYCLE_CODE: dict[str, int] = {
    "CREATED": 0,
    "VALIDATING": 1,
    "WARMING_UP": 2,
    "READY": 3,
    "RUNNING": 4,
    "PAUSED": 5,
    "STOPPING": 6,
    "STOPPED": 7,
    "ERROR": 8,
    "RECOVERING": 9,
    "RECONCILING": 10,
}


def native_arm_transition(current_val: str, target_val: str) -> str:
    """Validate one arm transition using the Rust authority."""
    cur_code = _ARM_CODE.get(current_val, -1)
    tgt_code = _ARM_CODE.get(target_val, -1)
    if cur_code < 0 or tgt_code < 0:
        raise ValueError(f"illegal arming transition {current_val} -> {target_val}")
    res = int(_lib.vy_exec_arm_transition(cur_code, tgt_code))
    if res < 0:
        raise ValueError(f"illegal arming transition {current_val} -> {target_val}")
    return _ARM_BY_CODE[res]


def native_lifecycle_transition_allowed(from_val: str, to_val: str) -> bool:
    """Check lifecycle transition legality against the Rust authority."""
    f_code = _LIFECYCLE_CODE.get(from_val, -1)
    t_code = _LIFECYCLE_CODE.get(to_val, -1)
    if f_code < 0 or t_code < 0:
        return False
    return bool(_lib.vy_exec_lifecycle_transition_allowed(f_code, t_code))


def native_plan_order(
    quantity: float,
    order_type: str,
    reference_price: float,
    prefer_limit: bool,
    size_multiplier: float,
) -> tuple[float, str, float | None]:
    """Execute order planning in the Rust kernel."""
    ot_code = 1 if order_type == "LIMIT" else 0
    out_qty = ctypes.c_double()
    out_order_type = ctypes.c_int32()
    out_has_limit = ctypes.c_int32()
    out_limit_price = ctypes.c_double()

    rc = int(
        _lib.vy_exec_planner_plan(
            float(quantity),
            ot_code,
            float(reference_price),
            1 if prefer_limit else 0,
            float(size_multiplier),
            ctypes.byref(out_qty),
            ctypes.byref(out_order_type),
            ctypes.byref(out_has_limit),
            ctypes.byref(out_limit_price),
        )
    )
    if rc != 0:
        raise ValueError("size_multiplier must be in (0, 1]")

    planned_type = "LIMIT" if out_order_type.value == 1 else "MARKET"
    limit_price = float(out_limit_price.value) if out_has_limit.value != 0 else None
    return float(out_qty.value), planned_type, limit_price


def native_ledger_apply_fill(
    pos_qty: float,
    pos_avg_price: float,
    pos_realized_pnl: float,
    side: str,
    fill_qty: float,
    fill_price: float,
    commission: float,
) -> tuple[float, float, float, float]:
    """Execute position ledger fill folding in the Rust kernel."""
    side_code = 0 if side == "BUY" else 1
    out_qty = ctypes.c_double()
    out_avg_price = ctypes.c_double()
    out_realized_pnl = ctypes.c_double()
    out_day_pnl_delta = ctypes.c_double()

    rc = int(
        _lib.vy_exec_ledger_apply_fill(
            float(pos_qty),
            float(pos_avg_price),
            float(pos_realized_pnl),
            side_code,
            float(fill_qty),
            float(fill_price),
            float(commission),
            ctypes.byref(out_qty),
            ctypes.byref(out_avg_price),
            ctypes.byref(out_realized_pnl),
            ctypes.byref(out_day_pnl_delta),
        )
    )
    if rc != 0:
        raise RuntimeError("native ledger fill calculation failed")

    return (
        float(out_qty.value),
        float(out_avg_price.value),
        float(out_realized_pnl.value),
        float(out_day_pnl_delta.value),
    )
