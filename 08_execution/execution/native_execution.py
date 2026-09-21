"""Rust-backed execution core authority (constitution §1: Execution/Core).

Pure calculations and state-machine checks live in Rust (`rust/vayren-core`,
`execution_engine` and `order_state` modules) and are bridged here via ctypes.
"""

from __future__ import annotations

import ctypes
from array import array
from collections.abc import Sequence

from core.native.loader import NativeBridgeError, load_vayren_core

_lib = load_vayren_core()

_char_out = ctypes.POINTER(ctypes.c_char)

_lib.vy_exec_gates_mask.argtypes = [ctypes.c_char_p, ctypes.c_int64]
_lib.vy_exec_gates_mask.restype = ctypes.c_int64

_lib.vy_exec_missing_gates.argtypes = [ctypes.c_uint32, _char_out, ctypes.c_size_t]
_lib.vy_exec_missing_gates.restype = ctypes.c_int32

_lib.vy_exec_resolve_mode.argtypes = [
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_uint32,
    _char_out,
    ctypes.c_size_t,
]
_lib.vy_exec_resolve_mode.restype = ctypes.c_int32

_lib.vy_exec_intent_id.argtypes = [
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_int64,
    ctypes.c_int64,
    _char_out,
    ctypes.c_size_t,
]
_lib.vy_exec_intent_id.restype = ctypes.c_int32


_lib.vy_exec_default_quantity.argtypes = [
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
]
_lib.vy_exec_default_quantity.restype = ctypes.c_double

_lib.vy_exec_narrow_multiplier.argtypes = [ctypes.c_int32, ctypes.c_double]
_lib.vy_exec_narrow_multiplier.restype = ctypes.c_double

_lib.vy_exec_multiplier_message.argtypes = [ctypes.c_double, _char_out, ctypes.c_size_t]
_lib.vy_exec_multiplier_message.restype = ctypes.c_int32

_lib.vy_exec_flags_to_mask.argtypes = [ctypes.POINTER(ctypes.c_int32), ctypes.c_size_t]
_lib.vy_exec_flags_to_mask.restype = ctypes.c_int64

_lib.vy_exec_mask_to_flags.argtypes = [
    ctypes.c_int64,
    ctypes.POINTER(ctypes.c_int32),
    ctypes.c_size_t,
]
_lib.vy_exec_mask_to_flags.restype = ctypes.c_int32


def _read(needed: int, call) -> str:
    """Drain a two-call buffer protocol: probe, allocate, refill."""
    if needed < 0:
        raise NativeBridgeError(f"execution kernel rejected the call: {needed}")
    if needed == 0:
        return ""
    buf = ctypes.create_string_buffer(needed + 1)
    if call(buf, needed + 1) != needed:
        raise NativeBridgeError("execution kernel length drift")
    return buf.raw[:needed].decode("utf-8")


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

_lib.vy_exec_reconcile_positions.argtypes = [
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_double,
    _char_out,
    ctypes.c_size_t,
]
_lib.vy_exec_reconcile_positions.restype = ctypes.c_int32

_lib.vy_exec_reconcile_orders.argtypes = [
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_char_p,
    ctypes.c_int64,
    _char_out,
    ctypes.c_size_t,
]
_lib.vy_exec_reconcile_orders.restype = ctypes.c_int32

_lib.vy_exec_reconcile_funds.argtypes = [
    ctypes.c_double,
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_double,
    _char_out,
    ctypes.c_size_t,
]
_lib.vy_exec_reconcile_funds.restype = ctypes.c_int32

_lib.vy_exec_verdict_status.argtypes = [ctypes.c_int32, ctypes.c_int64]
_lib.vy_exec_verdict_status.restype = ctypes.c_int32

_lib.vy_exec_verdict_blocks_live.argtypes = [ctypes.c_int32]
_lib.vy_exec_verdict_blocks_live.restype = ctypes.c_int32

_lib.vy_exec_report_blocks_live.argtypes = [ctypes.c_int32]
_lib.vy_exec_report_blocks_live.restype = ctypes.c_int32

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

_lib.vy_exec_position_state.argtypes = [ctypes.c_double]
_lib.vy_exec_position_state.restype = ctypes.c_int32

_lib.vy_exec_position_unrealized.argtypes = [
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.POINTER(ctypes.c_double),
]
_lib.vy_exec_position_unrealized.restype = ctypes.c_int32

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

_lib.vy_exec_percentile.argtypes = [
    ctypes.POINTER(ctypes.c_double),
    ctypes.c_size_t,
    ctypes.c_double,
    ctypes.POINTER(ctypes.c_double),
]
_lib.vy_exec_percentile.restype = ctypes.c_int32

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


_VERDICT_CODES: dict[str, int] = {"SAFE": 0, "WARNING": 1, "BLOCKED": 2}
_VERDICT_BY_CODE: dict[int, str] = {code: name for name, code in _VERDICT_CODES.items()}


def _fields(fields: Sequence[str]) -> bytes:
    """Trailing-NUL terminated field list (an empty list is an empty blob)."""
    return "".join(f"{field}\0" for field in fields).encode("utf-8")


def _pairs(pairs: Sequence[tuple[str, str]]) -> bytes:
    """Field blob of `(first, second)` pairs, each field NUL-terminated."""
    return "".join(f"{first}\0{second}\0" for first, second in pairs).encode("utf-8")


def _report(text: str) -> list[tuple[str, str, str, str]]:
    """Decode a `"<count>\\n"` + four-fields-per-mismatch report document."""
    head, _, body = text.partition("\n")
    try:
        count = int(head)
    except ValueError as exc:  # pragma: no cover - kernel contract break
        raise NativeBridgeError("reconciliation report has no count line") from exc
    fields = body.split("\0")
    if count < 0 or len(fields) != count * 4 + 1 or fields[-1] != "":
        raise NativeBridgeError("reconciliation report document is malformed")
    records: list[tuple[str, str, str, str]] = []
    for index in range(count):
        kind, ident, local, broker = fields[index * 4 : index * 4 + 4]
        records.append((kind, ident, local, broker))
    return records


def _document(call) -> str:
    """Two-call document protocol: probe the byte length, then read it back."""
    return _read(int(call(None, 0)), call)


def native_reconcile_positions(
    local: Sequence[tuple[str, float]],
    broker: Sequence[tuple[str, object]],
    tolerance: float,
) -> list[tuple[str, str, str, str]]:
    """Position mismatches, decided by the Rust kernel.

    Broker quantities travel as payload text: the parse-or-zero rule, the
    tolerance compare and the sorted symbol union are all the kernel's.
    Symbols and ids are exchange-feed payload and never contain NUL.
    """
    local_blob = _pairs([(symbol, str(quantity)) for symbol, quantity in local])
    broker_blob = _pairs([(symbol, str(raw)) for symbol, raw in broker])
    return _report(
        _document(
            lambda buf, cap: int(
                _lib.vy_exec_reconcile_positions(
                    local_blob,
                    len(local_blob),
                    broker_blob,
                    len(broker_blob),
                    float(tolerance),
                    buf,
                    cap,
                )
            )
        )
    )


def native_reconcile_orders(
    local_ids: Sequence[str],
    broker_ids: Sequence[str],
) -> list[tuple[str, str, str, str]]:
    """Open-order mismatches (symmetric difference, side-labelled) from Rust."""
    local_blob = _fields(local_ids)
    broker_blob = _fields(broker_ids)
    return _report(
        _document(
            lambda buf, cap: int(
                _lib.vy_exec_reconcile_orders(
                    local_blob,
                    len(local_blob),
                    broker_blob,
                    len(broker_blob),
                    buf,
                    cap,
                )
            )
        )
    )


def native_reconcile_funds(
    local_equity: float,
    broker_equity: object,
    tolerance: float,
) -> list[tuple[str, str, str, str]]:
    """Funds mismatch list from Rust; ``None``/unparseable equity is unknown.

    UNKNOWN never matches: the kernel decides that and the tolerance compare,
    so the caller only hands over the payload value as text.
    """
    raw = None if broker_equity is None else str(broker_equity).encode("utf-8")
    return _report(
        _document(
            lambda buf, cap: int(
                _lib.vy_exec_reconcile_funds(
                    float(local_equity),
                    raw,
                    -1 if raw is None else len(raw),
                    float(tolerance),
                    buf,
                    cap,
                )
            )
        )
    )


def native_verdict_status(evaluated: bool, mismatch_total: int) -> str:
    """Combined SAFE/WARNING/BLOCKED status, decided by the Rust kernel."""
    code = int(_lib.vy_exec_verdict_status(1 if evaluated else 0, int(mismatch_total)))
    if code not in _VERDICT_BY_CODE:
        raise NativeBridgeError(f"unknown verdict status code: {code}")
    return _VERDICT_BY_CODE[code]


def native_verdict_blocks_live(status_val: str) -> bool:
    """Check whether a reconciliation verdict status blocks live trading via Rust authority."""
    code = _VERDICT_CODES.get(status_val, 2)
    return bool(_lib.vy_exec_verdict_blocks_live(code))


def native_report_blocks_live(matched: bool) -> bool:
    """Report-level blocking rule: an unresolved report blocks live."""
    return bool(_lib.vy_exec_report_blocks_live(1 if matched else 0))


def native_ledger_snapshot(
    starting_capital: float,
    realized_sum: float,
    day_pnl: float,
    positions: list[tuple[float, float, float]],  # (qty, avg_price, mark_price)
) -> tuple[float, float, float]:
    """Calculate account snapshot values in the Rust kernel."""
    count = len(positions)
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


_POSITION_SIDES: tuple[str | None, ...] = (None, "LONG", "SHORT")


def native_position_state(quantity: float) -> tuple[bool, str | None]:
    """``(flat, side)`` for a signed net quantity.

    The kernel reads the sign once (`execution::position_state`), so
    the flat test and the ``LONG``/``SHORT`` label can never disagree.
    """
    code = int(_lib.vy_exec_position_state(float(quantity)))
    if code < 0 or code >= len(_POSITION_SIDES):
        raise NativeBridgeError(f"unknown position state code: {code}")
    return code == 0, _POSITION_SIDES[code]


def native_position_unrealized(quantity: float, avg_price: float, mark_price: float) -> float:
    """Mark-to-market P&L of one leg, calculated by the Rust kernel."""
    out = ctypes.c_double()
    rc = int(
        _lib.vy_exec_position_unrealized(
            float(quantity),
            float(avg_price),
            float(mark_price),
            ctypes.byref(out),
        )
    )
    if rc != 1:
        raise NativeBridgeError("position mark kernel did not return a value")
    return float(out.value)


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


def native_percentile(ordered: Sequence[float], pct: float) -> float:
    """Rank percentile of an ascending sample set; 0.0 when there is no sample.

    The ranking itself (`min(n-1, int(pct/100*n))`) is Rust's —
    `execution_engine::percentile` — this only packs the samples.
    """
    packed = array("d", (float(value) for value in ordered))
    count = len(packed)
    view = (ctypes.c_double * count).from_buffer(packed) if count else None
    out = ctypes.c_double()
    code = int(_lib.vy_exec_percentile(view, count, float(pct), ctypes.byref(out)))
    if code != 1:
        raise ValueError(f"native percentile kernel rejected the call: {code}")
    return float(out.value)


def native_gates_mask(values: Sequence[str]) -> int:
    """Gate mask from five configuration values, in gate order.

    The fail-closed grammar (only an exact ``"true"`` counts as ON) is the
    kernel's; this only joins the raw strings with NUL, a separator no
    configuration value can contain.
    """
    blob = "\0".join(values).encode("utf-8")
    mask = int(_lib.vy_exec_gates_mask(blob, len(blob)))
    if mask < 0:
        raise NativeBridgeError("live gate kernel rejected the flag set")
    return mask


def native_missing_gates(mask: int) -> tuple[str, ...]:
    """Names of the unsatisfied gates, in the kernel's gate order."""
    needed = int(_lib.vy_exec_missing_gates(mask, None, 0))
    text = _read(
        needed,
        lambda buf, cap: int(_lib.vy_exec_missing_gates(mask, buf, cap)),
    )
    return tuple(text.split(",")) if text else ()


def native_resolve_mode(requested: str, mask: int) -> tuple[str, tuple[str, ...]]:
    """Effective ``(mode, downgrade_reasons)`` for a request plus its gates.

    The LIVE→PAPER degradation rule, the gate vocabulary and the reason text
    all come from the kernel; an unknown mode label is a bridge error, not a
    silent downgrade.
    """
    payload = requested.encode("utf-8")
    needed = int(_lib.vy_exec_resolve_mode(payload, len(payload), mask, None, 0))
    if needed < 0:
        raise NativeBridgeError(f"unknown execution mode label: {requested!r}")
    doc = _read(
        needed,
        lambda buf, cap: int(_lib.vy_exec_resolve_mode(payload, len(payload), mask, buf, cap)),
    )
    mode, _, reasons = doc.partition("\n")
    return mode, tuple(reasons.split("\n")) if reasons else ()


def native_intent_id(
    strategy_id: str,
    strategy_version: str,
    event_seq: int,
    intent_seq: int,
) -> str:
    """Idempotency key for one input event.

    The separator grammar is the kernel's (`execution::make_intent_id`);
    this only encodes the two string segments and reads the answer back.
    """
    identity = strategy_id.encode("utf-8")
    version = strategy_version.encode("utf-8")
    needed = int(
        _lib.vy_exec_intent_id(
            identity,
            len(identity),
            version,
            len(version),
            int(event_seq),
            int(intent_seq),
            None,
            0,
        )
    )
    return _read(
        needed,
        lambda buf, cap: int(
            _lib.vy_exec_intent_id(
                identity,
                len(identity),
                version,
                len(version),
                int(event_seq),
                int(intent_seq),
                buf,
                cap,
            )
        ),
    )


def native_default_quantity(available_capital: float, price: float, max_order_qty: float) -> float:
    """Default order size: affordable at `price`, capped by the policy ceiling."""
    return float(
        _lib.vy_exec_default_quantity(float(available_capital), float(price), float(max_order_qty))
    )


def native_narrow_multiplier(raw: object) -> float:
    """Advisory size multiplier as the planner can use it (`1.0` = no shrink).

    Only the readability of `raw` is decided here; the kernel decides which
    values are usable.
    """
    if isinstance(raw, bool):
        readable, value = False, 0.0
    elif isinstance(raw, (int, float)):
        readable, value = True, float(raw)
    else:
        readable, value = False, 0.0
    return float(_lib.vy_exec_narrow_multiplier(1 if readable else 0, value))


def native_multiplier_problem(size_multiplier: float) -> str:
    """Why an advisory multiplier is unusable — empty means it is usable."""
    needed = int(_lib.vy_exec_multiplier_message(float(size_multiplier), None, 0))
    return _read(
        needed,
        lambda buf, cap: int(_lib.vy_exec_multiplier_message(float(size_multiplier), buf, cap)),
    )


def native_flags_to_mask(flags: Sequence[bool]) -> int:
    """Five gate flags packed exactly as the kernel encodes them."""
    view = (ctypes.c_int32 * len(flags))(*[1 if on else 0 for on in flags])
    mask = int(_lib.vy_exec_flags_to_mask(view, len(flags)))
    if mask < 0:
        raise NativeBridgeError(f"execution kernel rejected the gate flags: {mask}")
    return mask


def native_mask_to_flags(mask: int) -> tuple[bool, ...]:
    """The gate flags in the kernel's own order."""
    view = (ctypes.c_int32 * 5)()
    if int(_lib.vy_exec_mask_to_flags(int(mask), view, 5)) != 5:
        raise NativeBridgeError("execution kernel could not decode the gate mask")
    return tuple(bool(bit) for bit in view)
