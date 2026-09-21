"""ctypes wrapper for the Rust risk decision engine (handle-based bridge).

The checklist itself lives in `rust/vayren-core/src/risk_engine.rs`: the 18
named gates, their order, their verdicts, every reason string and the
duplicate-intent memory. This module only marshals a policy and a request
across the boundary and re-materialises the JSON decision document as the
frozen value objects callers already expect — no risk rule is evaluated here
(constitution §8, migration §14).
"""

from __future__ import annotations

import contextlib
import ctypes
import json
from typing import Any

from core.native.loader import NativeBridgeError, load_vayren_core

from risk.models import RiskCheck, RiskDecision, RiskPolicy, RiskRequest

_lib = load_vayren_core()

_REQUIRED_EXPORTS = (
    "vy_risk_engine_new",
    "vy_risk_engine_allow",
    "vy_risk_engine_evaluate",
    "vy_risk_engine_decision",
    "vy_risk_engine_free",
)

_missing_exports = [name for name in _REQUIRED_EXPORTS if not hasattr(_lib, name)]
if _missing_exports:
    raise NativeBridgeError(
        f"native library has no risk engine ({', '.join(_missing_exports)}). "
        "Rebuild: `python scripts/build_rust.py`"
    )

# A missing optional limit/observation crosses as a `defined = 0` flag plus a
# filler; `0.0` is itself a meaningful limit, so presence needs its own signal.
_DEFINED = ctypes.c_int32
_TEXT = [ctypes.c_char_p, ctypes.c_int64]

_lib.vy_risk_engine_new.restype = ctypes.c_int64
_lib.vy_risk_engine_new.argtypes = [
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    _DEFINED,
    ctypes.c_double,
    _DEFINED,
    ctypes.c_double,
    _DEFINED,
    ctypes.c_double,
    _DEFINED,
    ctypes.c_double,
    _DEFINED,
    ctypes.c_double,
    _DEFINED,
    ctypes.c_double,
    _DEFINED,
    ctypes.c_int64,
    *_TEXT,
    *_TEXT,
]
_lib.vy_risk_engine_allow.restype = ctypes.c_int32
_lib.vy_risk_engine_allow.argtypes = [ctypes.c_int64, *_TEXT]
_lib.vy_risk_engine_evaluate.restype = ctypes.c_int32
_lib.vy_risk_engine_evaluate.argtypes = [
    ctypes.c_int64,
    ctypes.c_int32,
    *_TEXT,
    *_TEXT,
    *_TEXT,
    *_TEXT,
    *_TEXT,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_int64,
    _DEFINED,
    ctypes.c_double,
    _DEFINED,
    ctypes.c_double,
    _DEFINED,
    ctypes.c_double,
    ctypes.c_int32,
]
_lib.vy_risk_engine_decision.restype = ctypes.c_int32
_lib.vy_risk_engine_decision.argtypes = [
    ctypes.c_int64,
    ctypes.POINTER(ctypes.c_char),
    ctypes.c_size_t,
]
_lib.vy_risk_engine_free.restype = ctypes.c_int32
_lib.vy_risk_engine_free.argtypes = [ctypes.c_int64]


def _text(value: str | None) -> tuple[bytes | None, int]:
    """UTF-8 payload + length; `None` travels as the negative-length sentinel."""
    if value is None:
        return (None, -1)
    if not isinstance(value, str):
        raise TypeError(f"risk text must be str or None, not {type(value).__name__}")
    payload = value.encode("utf-8")
    return (payload, len(payload))


def _optional(value: float | int | None) -> tuple[int, float]:
    """`(defined, slot)` pair for an optional numeric limit or observation."""
    if value is None:
        return (0, 0.0)
    return (1, float(value))


def _read_decision(handle: int) -> dict[str, Any]:
    """Drain the decision document: probe its length, then fill exactly."""

    def call(buf: ctypes.Array[ctypes.c_char] | None, cap: int) -> int:
        return int(_lib.vy_risk_engine_decision(handle, buf, cap))

    needed = call(None, 0)
    if needed < 0:
        raise NativeBridgeError(f"native risk decision unavailable: {needed}")
    buf = ctypes.create_string_buffer(needed + 1)
    if call(buf, needed + 1) != needed:
        raise NativeBridgeError("native risk decision length drift")
    document: dict[str, Any] = json.loads(buf.raw[:needed].decode("utf-8"))
    return document


class NativeRiskEngine:
    """One Rust-side policy engine + duplicate-intent memory, by handle."""

    def __init__(self, policy: RiskPolicy) -> None:
        handle = int(
            _lib.vy_risk_engine_new(
                float(policy.max_position_qty),
                float(policy.max_order_qty),
                float(policy.cooldown_seconds),
                *_optional(policy.max_notional),
                *_optional(policy.max_exposure_pct),
                *_optional(policy.daily_loss_limit),
                *_optional(policy.strategy_loss_limit),
                *_optional(policy.spread_limit_pct),
                *_optional(policy.require_fresh_data_seconds),
                *(
                    (1, int(policy.max_orders_per_day))
                    if policy.max_orders_per_day is not None
                    else (0, 0)
                ),
                *_text(policy.session_start),
                *_text(policy.session_end),
            )
        )
        if handle <= 0:
            raise NativeBridgeError(f"native risk engine handle allocation failed: {handle}")
        self._handle = handle
        for symbol in policy.allowed_symbols:
            if int(_lib.vy_risk_engine_allow(handle, *_text(symbol))) != 0:
                raise NativeBridgeError("native risk engine rejected an allowed symbol")

    def __del__(self) -> None:
        with contextlib.suppress(Exception):  # interpreter teardown: nothing to report
            self.close()

    def close(self) -> None:
        """Release the Rust slot. Idempotent."""
        handle, self._handle = self._handle, 0
        if handle and int(_lib.vy_risk_engine_free(handle)) != 0:
            raise NativeBridgeError(f"native risk engine handle {handle} already released")

    def evaluate(self, request: RiskRequest, kill_halted: bool) -> RiskDecision:
        """Ask the kernel for a verdict. Bridge faults propagate; the caller
        that must never raise wraps them into a denial."""
        code = int(
            _lib.vy_risk_engine_evaluate(
                self._handle,
                1 if kill_halted else 0,
                *_text(request.intent_id),
                *_text(request.strategy_id),
                *_text(request.symbol),
                *_text(request.side),
                *_text(request.timestamp),
                float(request.quantity),
                float(request.price),
                float(request.position_qty),
                float(request.day_pnl),
                float(request.strategy_day_pnl),
                float(request.equity),
                float(request.available_capital),
                float(request.now_epoch),
                int(request.orders_today),
                *_optional(request.spread_pct),
                *_optional(request.data_age_seconds),
                *_optional(request.last_order_epoch),
                1 if request.broker_healthy else 0,
            )
        )
        if code not in (0, 1):
            raise NativeBridgeError(f"native risk engine rejected the request: {code}")
        document = _read_decision(self._handle)
        checks = tuple(
            RiskCheck(
                name=str(entry["name"]),
                passed=bool(entry["passed"]),
                detail=str(entry["detail"]),
            )
            for entry in document["checks"]
        )
        return RiskDecision(
            approved=bool(document["approved"]),
            intent_id=str(document["intent_id"]),
            reasons=tuple(str(reason) for reason in document["reasons"]),
            checks=checks,
        )


__all__ = ["NativeBridgeError", "NativeRiskEngine"]
