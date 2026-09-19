"""Rust-backed execution core authority (constitution §1: Execution/Core).

Pure calculations and state-machine checks live in Rust (`rust/vayren-core`,
`execution_engine` and `order_state` modules) and are bridged here via ctypes.
"""

from __future__ import annotations

import ctypes

from core.native.loader import load_vayren_core

_lib = load_vayren_core()

_lib.vy_exec_arm_transition.argtypes = [ctypes.c_int32, ctypes.c_int32]
_lib.vy_exec_arm_transition.restype = ctypes.c_int32

_lib.vy_exec_lifecycle_transition_allowed.argtypes = [ctypes.c_int32, ctypes.c_int32]
_lib.vy_exec_lifecycle_transition_allowed.restype = ctypes.c_int32

_lib.vy_exec_planner_plan.argtypes = [
    ctypes.c_double,
    ctypes.c_int32,
    ctypes.c_double,
    ctypes.c_int32,
    ctypes.c_double,
    ctypes.POINTER(ctypes.c_double),
    ctypes.POINTER(ctypes.c_int32),
    ctypes.POINTER(ctypes.c_int32),
    ctypes.POINTER(ctypes.c_double),
]
_lib.vy_exec_planner_plan.restype = ctypes.c_int32

_lib.vy_exec_ledger_apply_fill.argtypes = [
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_int32,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.POINTER(ctypes.c_double),
    ctypes.POINTER(ctypes.c_double),
    ctypes.POINTER(ctypes.c_double),
    ctypes.POINTER(ctypes.c_double),
]
_lib.vy_exec_ledger_apply_fill.restype = ctypes.c_int32

_lib.vy_exec_reconcile_funds.argtypes = [
    ctypes.c_double,
    ctypes.c_int32,
    ctypes.c_double,
    ctypes.c_double,
]
_lib.vy_exec_reconcile_funds.restype = ctypes.c_int32

_lib.vy_exec_verdict_blocks_live.argtypes = [ctypes.c_int32]
_lib.vy_exec_verdict_blocks_live.restype = ctypes.c_int32

_lib.vy_exec_ledger_snapshot.argtypes = [
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_int32,
    ctypes.POINTER(ctypes.c_double),
    ctypes.POINTER(ctypes.c_double),
    ctypes.POINTER(ctypes.c_double),
    ctypes.POINTER(ctypes.c_double),
    ctypes.POINTER(ctypes.c_double),
    ctypes.POINTER(ctypes.c_double),
]
_lib.vy_exec_ledger_snapshot.restype = ctypes.c_int32

_lib.vy_exec_paper_calculate_fill.argtypes = [
    ctypes.c_int32,
    ctypes.c_int32,
    ctypes.c_double,
    ctypes.c_int32,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.POINTER(ctypes.c_double),
    ctypes.POINTER(ctypes.c_double),
    ctypes.POINTER(ctypes.c_double),
    ctypes.POINTER(ctypes.c_double),
    ctypes.POINTER(ctypes.c_double),
]
_lib.vy_exec_paper_calculate_fill.restype = ctypes.c_int32

_lib.vy_exec_order_apply_fill.argtypes = [
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.POINTER(ctypes.c_double),
    ctypes.POINTER(ctypes.c_double),
]
_lib.vy_exec_order_apply_fill.restype = ctypes.c_int32

_lib.vy_exec_check_live_readiness_basic.argtypes = [
    ctypes.c_int32,
    ctypes.c_int32,
    ctypes.c_int32,
    ctypes.c_int32,
    ctypes.c_int32,
    ctypes.c_int32,
    ctypes.c_int32,
    ctypes.c_int32,
    ctypes.c_int32,
]
_lib.vy_exec_check_live_readiness_basic.restype = ctypes.c_int32

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


def native_reconcile_funds(
    local_equity: float,
    has_broker: bool,
    broker_equity: float,
    tolerance: float,
) -> bool:
    """Check fund reconciliation in the Rust kernel."""
    res = int(
        _lib.vy_exec_reconcile_funds(
            float(local_equity),
            1 if has_broker else 0,
            float(broker_equity),
            float(tolerance),
        )
    )
    return res == 1


def native_verdict_blocks_live(status_val: str) -> bool:
    """Check whether a reconciliation verdict status blocks live trading via Rust authority."""
    code = 0 if status_val == "SAFE" else (1 if status_val == "WARNING" else 2)
    return bool(_lib.vy_exec_verdict_blocks_live(code))


def native_ledger_snapshot(
    starting_capital: float,
    realized_sum: float,
    day_pnl: float,
    positions: list[tuple[float, float, float]],  # (qty, avg_price, mark_price)
) -> tuple[float, float, float]:
    """Calculate account snapshot values in the Rust kernel."""
    count = len(positions)
    if count == 0:
        equity = starting_capital + realized_sum
        return equity, equity, day_pnl

    c_double_array = ctypes.c_double * count
    qtys = c_double_array(*(p[0] for p in positions))
    avg_prices = c_double_array(*(p[1] for p in positions))
    mark_prices = c_double_array(*(p[2] for p in positions))

    out_equity = ctypes.c_double()
    out_available = ctypes.c_double()
    out_day_pnl = ctypes.c_double()

    _lib.vy_exec_ledger_snapshot(
        float(starting_capital),
        float(realized_sum),
        float(day_pnl),
        count,
        qtys,
        avg_prices,
        mark_prices,
        ctypes.byref(out_equity),
        ctypes.byref(out_available),
        ctypes.byref(out_day_pnl),
    )
    return (
        float(out_equity.value),
        float(out_available.value),
        float(out_day_pnl.value),
    )


def native_paper_calculate_fill(
    is_limit: bool,
    has_limit: bool,
    limit_price: float,
    is_buy: bool,
    reference_price: float,
    slippage_pct: float,
    commission_pct: float,
    capital: float,
    remaining_qty: float,
) -> tuple[float, float, float, float, float] | None:
    """Calculate fill economics for paper broker in Rust.

    Returns (fill_price, fill_qty, notional, commission, new_capital) or None if unfillable.
    """
    out_fill_price = ctypes.c_double()
    out_fill_qty = ctypes.c_double()
    out_notional = ctypes.c_double()
    out_commission = ctypes.c_double()
    out_new_capital = ctypes.c_double()

    rc = int(
        _lib.vy_exec_paper_calculate_fill(
            1 if is_limit else 0,
            1 if has_limit else 0,
            float(limit_price),
            1 if is_buy else 0,
            float(reference_price),
            float(slippage_pct),
            float(commission_pct),
            float(capital),
            float(remaining_qty),
            ctypes.byref(out_fill_price),
            ctypes.byref(out_fill_qty),
            ctypes.byref(out_notional),
            ctypes.byref(out_commission),
            ctypes.byref(out_new_capital),
        )
    )
    if rc != 0:
        return None
    return (
        float(out_fill_price.value),
        float(out_fill_qty.value),
        float(out_notional.value),
        float(out_commission.value),
        float(out_new_capital.value),
    )


def native_order_apply_fill(
    prev_qty: float,
    prev_avg: float,
    fill_qty: float,
    fill_price: float,
) -> tuple[float, float]:
    """Fold a fill report into order filled_qty and avg_fill_price via Rust."""
    out_qty = ctypes.c_double()
    out_avg = ctypes.c_double()
    _lib.vy_exec_order_apply_fill(
        float(prev_qty),
        float(prev_avg),
        float(fill_qty),
        float(fill_price),
        ctypes.byref(out_qty),
        ctypes.byref(out_avg),
    )
    return float(out_qty.value), float(out_avg.value)


def native_check_live_readiness_basic(
    warmup_have: int,
    warmup_need: int,
    risk_ok: bool,
    account_ok: bool,
    clock_ok: bool,
    reconcile_ok: bool,
    persistence_ok: bool,
    kill_ok: bool,
    observability_ok: bool,
) -> bool:
    """Evaluate basic live readiness gates in Rust."""
    res = int(
        _lib.vy_exec_check_live_readiness_basic(
            int(warmup_have),
            int(warmup_need),
            1 if risk_ok else 0,
            1 if account_ok else 0,
            1 if clock_ok else 0,
            1 if reconcile_ok else 0,
            1 if persistence_ok else 0,
            1 if kill_ok else 0,
            1 if observability_ok else 0,
        )
    )
    return res == 1
