"""Rust-backed risk policy kernel (agent-migrated behavioral slice).

Pure scalar checks live in Rust (`rust/vayren-core`, `risk` module);
string-keyed audit reasons, kill-switch/session/clock/instrument/
duplicate orchestration stays Python. Bit i of the mask = checks[i].
"""

from __future__ import annotations

import ctypes
from array import array
from collections.abc import Mapping
from typing import Any

from core.native.loader import load_vayren_core

from risk.models import RiskPolicy, RiskRequest

_lib = load_vayren_core()

F64_ORDER: tuple[str, ...] = (
    "request_data_age_seconds",
    "policy_require_fresh_data_seconds",
    "request_spread_pct",
    "policy_spread_limit_pct",
    "policy_cooldown_seconds",
    "request_now_epoch",
    "request_last_order_epoch",
    "request_quantity",
    "request_price",
    "policy_max_order_qty",
    "policy_max_notional",
    "request_position_qty",
    "policy_max_position_qty",
    "request_equity",
    "policy_max_exposure_pct",
    "request_day_pnl",
    "policy_daily_loss_limit",
    "request_strategy_day_pnl",
    "policy_strategy_loss_limit",
    "request_available_capital",
)
I64_ORDER: tuple[str, ...] = (
    "request_orders_today",
    "policy_max_orders_per_day",
)
BOOL_ORDER: tuple[str, ...] = (
    "request_broker_healthy",
    "policy_require_fresh_data_seconds_present",
    "request_data_age_seconds_present",
    "policy_spread_limit_pct_present",
    "request_spread_pct_present",
    "request_last_order_epoch_present",
    "policy_max_orders_per_day_present",
    "policy_max_notional_present",
    "side_is_buy",
    "policy_max_exposure_pct_present",
    "policy_daily_loss_limit_present",
    "policy_strategy_loss_limit_present",
)

CHECK_NAMES: tuple[str, ...] = (
    "broker_health",
    "fresh_data",
    "spread",
    "cooldown",
    "order_rate",
    "sanity",
    "order_qty",
    "notional",
    "position",
    "exposure",
    "daily_loss",
    "strategy_loss",
    "capital",
)

BIT_BROKER_HEALTH = 1 << 0
BIT_FRESH_DATA = 1 << 1
BIT_SPREAD = 1 << 2
BIT_COOLDOWN = 1 << 3
BIT_ORDER_RATE = 1 << 4
BIT_SANITY = 1 << 5
BIT_ORDER_QTY = 1 << 6
BIT_NOTIONAL = 1 << 7
BIT_POSITION = 1 << 8
BIT_EXPOSURE = 1 << 9
BIT_DAILY_LOSS = 1 << 10
BIT_STRATEGY_LOSS = 1 << 11
BIT_CAPITAL = 1 << 12


def pack_env(policy: RiskPolicy, request: RiskRequest) -> dict[str, Any]:
    """Kernel env in spec order (None-safe: guarded by present flags)."""
    env: dict[str, Any] = {}
    env["request_broker_healthy"] = bool(request.broker_healthy)
    env["policy_require_fresh_data_seconds_present"] = bool(
        policy.require_fresh_data_seconds is not None
    )
    env["request_data_age_seconds_present"] = bool(request.data_age_seconds is not None)
    env["request_data_age_seconds"] = (
        float(request.data_age_seconds) if (request.data_age_seconds) is not None else 0.0
    )
    env["policy_require_fresh_data_seconds"] = (
        float(policy.require_fresh_data_seconds)
        if (policy.require_fresh_data_seconds) is not None
        else 0.0
    )
    env["policy_spread_limit_pct_present"] = bool(policy.spread_limit_pct is not None)
    env["request_spread_pct_present"] = bool(request.spread_pct is not None)
    env["request_spread_pct"] = (
        float(request.spread_pct) if (request.spread_pct) is not None else 0.0
    )
    env["policy_spread_limit_pct"] = (
        float(policy.spread_limit_pct) if (policy.spread_limit_pct) is not None else 0.0
    )
    env["policy_cooldown_seconds"] = (
        float(policy.cooldown_seconds) if (policy.cooldown_seconds) is not None else 0.0
    )
    env["request_last_order_epoch_present"] = bool(request.last_order_epoch is not None)
    env["request_now_epoch"] = float(request.now_epoch) if (request.now_epoch) is not None else 0.0
    env["request_last_order_epoch"] = (
        float(request.last_order_epoch) if (request.last_order_epoch) is not None else 0.0
    )
    env["policy_max_orders_per_day_present"] = bool(policy.max_orders_per_day is not None)
    env["request_orders_today"] = (
        int(request.orders_today) if (request.orders_today) is not None else 0
    )
    env["policy_max_orders_per_day"] = (
        int(policy.max_orders_per_day) if (policy.max_orders_per_day) is not None else 0
    )
    env["request_quantity"] = float(request.quantity) if (request.quantity) is not None else 0.0
    env["request_price"] = float(request.price) if (request.price) is not None else 0.0
    env["policy_max_order_qty"] = (
        float(policy.max_order_qty) if (policy.max_order_qty) is not None else 0.0
    )
    env["policy_max_notional_present"] = bool(policy.max_notional is not None)
    env["policy_max_notional"] = (
        float(policy.max_notional) if (policy.max_notional) is not None else 0.0
    )
    env["request_position_qty"] = (
        float(request.position_qty) if (request.position_qty) is not None else 0.0
    )
    env["side_is_buy"] = bool(request.side == "BUY")
    env["policy_max_position_qty"] = (
        float(policy.max_position_qty) if (policy.max_position_qty) is not None else 0.0
    )
    env["policy_max_exposure_pct_present"] = bool(policy.max_exposure_pct is not None)
    env["request_equity"] = float(request.equity) if (request.equity) is not None else 0.0
    env["policy_max_exposure_pct"] = (
        float(policy.max_exposure_pct) if (policy.max_exposure_pct) is not None else 0.0
    )
    env["policy_daily_loss_limit_present"] = bool(policy.daily_loss_limit is not None)
    env["request_day_pnl"] = float(request.day_pnl) if (request.day_pnl) is not None else 0.0
    env["policy_daily_loss_limit"] = (
        float(policy.daily_loss_limit) if (policy.daily_loss_limit) is not None else 0.0
    )
    env["policy_strategy_loss_limit_present"] = bool(policy.strategy_loss_limit is not None)
    env["request_strategy_day_pnl"] = (
        float(request.strategy_day_pnl) if (request.strategy_day_pnl) is not None else 0.0
    )
    env["policy_strategy_loss_limit"] = (
        float(policy.strategy_loss_limit) if (policy.strategy_loss_limit) is not None else 0.0
    )
    env["request_available_capital"] = (
        float(request.available_capital) if (request.available_capital) is not None else 0.0
    )
    return env


def _f64_view(values: object) -> tuple[ctypes.Array, array]:
    packed = array("d", values)  # type: ignore[arg-type]
    return (ctypes.c_double * len(packed)).from_buffer(packed), packed


def _i64_view(values: object) -> tuple[ctypes.Array, array]:
    packed = array("q", values)  # type: ignore[arg-type]
    return (ctypes.c_int64 * len(packed)).from_buffer(packed), packed


def check_mask(env: Mapping[str, Any]) -> int:
    """Kernel bitmask for a packed env (see spec input order)."""
    f64_values = [float(env[name]) for name in F64_ORDER]
    i64_values = [int(env[name]) for name in I64_ORDER]
    i64_values += [1 if env[name] else 0 for name in BOOL_ORDER]
    f64_view, _keep_f = _f64_view(f64_values)
    i64_view, _keep_i = _i64_view(i64_values)
    _lib.vy_risk_kernel.restype = ctypes.c_uint32
    _lib.vy_risk_kernel.argtypes = [
        ctypes.POINTER(ctypes.c_double),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_int64),
        ctypes.c_size_t,
    ]
    return int(_lib.vy_risk_kernel(f64_view, len(f64_values), i64_view, len(i64_values)))


def check_mask_for(policy: RiskPolicy, request: RiskRequest) -> int:
    """Pack the kernel env and evaluate the Rust kernel (fail-closed)."""
    return check_mask(pack_env(policy, request))


__all__ = [
    "F64_ORDER",
    "I64_ORDER",
    "BOOL_ORDER",
    "CHECK_NAMES",
    "BIT_BROKER_HEALTH",
    "BIT_FRESH_DATA",
    "BIT_SPREAD",
    "BIT_COOLDOWN",
    "BIT_ORDER_RATE",
    "BIT_SANITY",
    "BIT_ORDER_QTY",
    "BIT_NOTIONAL",
    "BIT_POSITION",
    "BIT_EXPOSURE",
    "BIT_DAILY_LOSS",
    "BIT_STRATEGY_LOSS",
    "BIT_CAPITAL",
    "pack_env",
    "check_mask",
    "check_mask_for",
]
