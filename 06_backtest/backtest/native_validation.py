"""Rust-backed backtest form checks (AI_ENTRY.md §1: Backtesting).

The decision lives in Rust (`rust/vayren-core`, `backtest_validation`
module); this module marshals the form across the boundary. String/set
marshalling (strip-emptiness, enabled-set membership, ISO-date ordering)
stays Python — strings never cross the FFI (ABI rule: fixed-width integers,
`f64` doubles, caller-allocated buffers). Bit i of the mask = MESSAGES[i]
failed. No independent Python checks here.
"""

from __future__ import annotations

import ctypes
from typing import Any

from core.native.loader import load_vayren_core
from strategy import BacktestForm

_lib = load_vayren_core()

BIT_SYMBOL = 1 << 0
BIT_STRATEGY = 1 << 1
BIT_TIMEFRAME = 1 << 2
BIT_DATES = 1 << 3
BIT_CAPITAL = 1 << 4
BIT_CAP_NONPOSITIVE = 1 << 5
BIT_CAP_EXCEEDS = 1 << 6

MESSAGES: tuple[str, ...] = (
    "No symbol loaded — select a symbol first.",
    "No strategy selected or strategy is disabled.",
    "No timeframe selected.",
    "Start date is after end date.",
    "Initial capital must be positive.",
    "Max position size must be positive.",
    "Max position size cannot exceed initial capital.",
)


def pack_env(form: BacktestForm, symbol: str | None, enabled_ids: set[str]) -> dict[str, Any]:
    """Marshal the form to kernel scalars with the exact original semantics."""
    max_cap = form.max_position_size
    return {
        "symbol_ok": 0 if (symbol is None or not symbol.strip()) else 1,
        "strategy_ok": (0 if (not form.strategy_id or form.strategy_id not in enabled_ids) else 1),
        "timeframe_ok": 0 if not form.timeframe else 1,
        "dates_ordered": 0 if form.start_date > form.end_date else 1,
        "initial_capital": float(form.initial_capital),
        "has_cap": 0 if max_cap is None else 1,
        "max_position_size": float(max_cap) if max_cap is not None else 0.0,
    }


def check_mask(env: dict[str, Any]) -> int:
    """Kernel bitmask for a packed env (see spec input order)."""
    _lib.vy_backtest_validate_form.restype = ctypes.c_uint32
    _lib.vy_backtest_validate_form.argtypes = [
        ctypes.c_int32,
        ctypes.c_int32,
        ctypes.c_int32,
        ctypes.c_int32,
        ctypes.c_double,
        ctypes.c_int32,
        ctypes.c_double,
    ]
    return int(
        _lib.vy_backtest_validate_form(
            int(env["symbol_ok"]),
            int(env["strategy_ok"]),
            int(env["timeframe_ok"]),
            int(env["dates_ordered"]),
            float(env["initial_capital"]),
            int(env["has_cap"]),
            float(env["max_position_size"]),
        )
    )


def validate_mask_for(form: BacktestForm, symbol: str | None, enabled_ids: set[str]) -> int:
    """Pack the form and evaluate the Rust kernel (fail-closed)."""
    return check_mask(pack_env(form, symbol, enabled_ids))


__all__ = [
    "BIT_SYMBOL",
    "BIT_STRATEGY",
    "BIT_TIMEFRAME",
    "BIT_DATES",
    "BIT_CAPITAL",
    "BIT_CAP_NONPOSITIVE",
    "BIT_CAP_EXCEEDS",
    "MESSAGES",
    "pack_env",
    "check_mask",
    "validate_mask_for",
]
