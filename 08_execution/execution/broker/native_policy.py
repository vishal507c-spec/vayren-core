"""Rust-backed broker-boundary policy authority (constitution §1: Execution).

The rules that decide *whether* the system may act against a venue — retry
classification, quota, clock drift, budget validity, live-gate verdicts, the
activation ceremony and the sandbox fill script — live in Rust
(`rust/vayren-core`, `resilience` and `execution_engine`). This module is the
typed ctypes boundary only: argument marshalling, buffer reads and handle
lifetimes. No policy is evaluated here (migration §7).
"""

from __future__ import annotations

import contextlib
import ctypes
from collections.abc import Callable

from core.native.loader import NativeBridgeError, load_vayren_core

_lib = load_vayren_core()

_char_out = ctypes.POINTER(ctypes.c_char)

_lib.vy_res_retry_kind.argtypes = [ctypes.c_char_p, ctypes.c_int64]
_lib.vy_res_retry_kind.restype = ctypes.c_int64

_lib.vy_res_limiter_problem.argtypes = [ctypes.c_int64, ctypes.c_double, _char_out, ctypes.c_size_t]
_lib.vy_res_limiter_problem.restype = ctypes.c_int32

_lib.vy_res_limiter_new.argtypes = [ctypes.c_int64, ctypes.c_double]
_lib.vy_res_limiter_new.restype = ctypes.c_int64

_lib.vy_res_limiter_allow.argtypes = [ctypes.c_int64, ctypes.c_double]
_lib.vy_res_limiter_allow.restype = ctypes.c_int32

_lib.vy_res_limiter_used.argtypes = [ctypes.c_int64]
_lib.vy_res_limiter_used.restype = ctypes.c_int64

_lib.vy_res_limiter_record_429.argtypes = [ctypes.c_int64]
_lib.vy_res_limiter_record_429.restype = ctypes.c_int32

_lib.vy_res_limiter_rejections.argtypes = [ctypes.c_int64]
_lib.vy_res_limiter_rejections.restype = ctypes.c_int64

_lib.vy_res_limiter_free.argtypes = [ctypes.c_int64]
_lib.vy_res_limiter_free.restype = ctypes.c_int32

_lib.vy_res_clock_ok.argtypes = [ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_int32]
_lib.vy_res_clock_ok.restype = ctypes.c_int32

_lib.vy_res_timeout_problem.argtypes = [
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    _char_out,
    ctypes.c_size_t,
]
_lib.vy_res_timeout_problem.restype = ctypes.c_int32

_lib.vy_res_backoff_problem.argtypes = [
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_int64,
    _char_out,
    ctypes.c_size_t,
]
_lib.vy_res_backoff_problem.restype = ctypes.c_int32

_lib.vy_res_backoff_delay.argtypes = [
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_int64,
]
_lib.vy_res_backoff_delay.restype = ctypes.c_double

_lib.vy_res_exhausted.argtypes = [ctypes.c_int64, ctypes.c_int64]
_lib.vy_res_exhausted.restype = ctypes.c_int32

_lib.vy_res_reconnect_problem.argtypes = [ctypes.c_int64, _char_out, ctypes.c_size_t]
_lib.vy_res_reconnect_problem.restype = ctypes.c_int32

_int8_out = ctypes.POINTER(ctypes.c_int8)

_lib.vy_cer_credentials.argtypes = [
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_int64,
    _int8_out,
    ctypes.c_int64,
    ctypes.c_int32,
    ctypes.c_int32,
    _char_out,
    ctypes.c_size_t,
]
_lib.vy_cer_credentials.restype = ctypes.c_int32

_lib.vy_cer_account.argtypes = [
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_char_p,
    ctypes.c_int64,
    _char_out,
    ctypes.c_size_t,
]
_lib.vy_cer_account.restype = ctypes.c_int32

_lib.vy_cer_risk.argtypes = [
    ctypes.c_int64,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_int64,
    ctypes.c_double,
    _char_out,
    ctypes.c_size_t,
]
_lib.vy_cer_risk.restype = ctypes.c_int32

_lib.vy_cer_funds.argtypes = [
    ctypes.c_double,
    ctypes.c_double,
    ctypes.c_char_p,
    ctypes.c_int64,
    _char_out,
    ctypes.c_size_t,
]
_lib.vy_cer_funds.restype = ctypes.c_int32

_lib.vy_cer_gates.argtypes = [
    ctypes.c_int32,
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_int32,
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_int32,
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_int64,
    ctypes.c_int32,
    ctypes.c_int32,
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_int64,
    ctypes.c_int32,
    ctypes.c_int32,
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_int64,
    ctypes.c_int32,
    _char_out,
    ctypes.c_size_t,
]
_lib.vy_cer_gates.restype = ctypes.c_int32

_lib.vy_cer_activation.argtypes = [
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_int32,
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_int64,
    ctypes.c_int32,
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_int64,
    ctypes.c_int32,
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_int32,
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_int64,
    ctypes.c_int32,
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_int64,
    ctypes.c_int32,
    ctypes.c_int32,
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_int64,
    ctypes.c_int32,
    ctypes.c_int32,
    ctypes.c_int32,
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_int64,
    ctypes.c_char_p,
    ctypes.c_int64,
    ctypes.c_int64,
    ctypes.c_char_p,
    ctypes.c_int64,
    _char_out,
    ctypes.c_size_t,
]
_lib.vy_cer_activation.restype = ctypes.c_int32

_lib.vy_sbx_settle_decision.argtypes = [ctypes.c_char_p, ctypes.c_int64, _char_out, ctypes.c_size_t]
_lib.vy_sbx_settle_decision.restype = ctypes.c_int32


def _reject(code: int, what: str) -> NativeBridgeError:
    return NativeBridgeError(f"resilience kernel rejected the {what} call: {code}")


def _read_problem(call: Callable[[ctypes.Array[ctypes.c_char] | None, int], int], what: str) -> str:
    """Drain a `0 = usable / >0 = reason bytes` answer with the two-call probe."""
    needed = call(None, 0)
    if needed < 0:
        raise _reject(needed, what)
    if needed == 0:
        return ""
    buf = ctypes.create_string_buffer(needed + 1)
    if call(buf, needed + 1) != needed:
        raise NativeBridgeError(f"resilience kernel {what} length drift")
    return buf.raw[:needed].decode("utf-8")


def _arg(value: str) -> bytes:
    return value.encode("utf-8")


def _read_document(
    call: Callable[[ctypes.Array[ctypes.c_char] | None, int], int], what: str
) -> tuple[str, ...]:
    """Drain a readiness document: concatenated `<byte length>:<text>` fields."""
    needed = call(None, 0)
    if needed < 0:
        raise _reject(needed, what)
    if needed == 0:
        return ()
    buf = ctypes.create_string_buffer(needed + 1)
    if call(buf, needed + 1) != needed:
        raise NativeBridgeError(f"readiness kernel {what} length drift")
    return _split_frame(buf.raw[:needed])


def _split_frame(payload: bytes) -> tuple[str, ...]:
    fields: list[str] = []
    rest = payload
    while rest:
        colon = rest.index(b":")
        size = int(rest[:colon])
        start = colon + 1
        fields.append(rest[start : start + size].decode("utf-8"))
        rest = rest[start + size :]
    return tuple(fields)


def _string_array(items: tuple[str, ...]) -> tuple[bytes, int]:
    """Encode an inbound string array: NUL-joined with a trailing NUL."""
    blob = "".join(f"{item}\0" for item in items)
    return _arg(blob), len(items)


def _flag_array(flags: tuple[bool, ...]) -> ctypes.Array[ctypes.c_int8]:
    holder = (ctypes.c_int8 * len(flags))(*[int(flag) for flag in flags])
    return holder


def native_retry_kind(operation: str) -> int:
    """Retry class for one venue operation: 0 safe, 1 not safe, 2 reconcile."""
    payload = operation.encode("utf-8")
    code = int(_lib.vy_res_retry_kind(payload, len(payload)))
    if code < 0:
        raise _reject(code, "retry_kind")
    return code


def native_limiter_problem(max_requests: int, window_seconds: float) -> str:
    """Kernel wording for an unusable throttle configuration ("" = usable)."""
    return _read_problem(
        lambda buf, cap: int(
            _lib.vy_res_limiter_problem(int(max_requests), float(window_seconds), buf, cap)
        ),
        "limiter",
    )


def native_limiter_allow(handle: int, now_epoch: float) -> bool:
    code = int(_lib.vy_res_limiter_allow(int(handle), float(now_epoch)))
    if code < 0:
        raise _reject(code, "limiter.allow")
    return code == 1


def native_limiter_used(handle: int) -> int:
    value = int(_lib.vy_res_limiter_used(int(handle)))
    if value < 0:
        raise _reject(value, "limiter.used")
    return value


def native_limiter_record_429(handle: int) -> None:
    if int(_lib.vy_res_limiter_record_429(int(handle))) != 0:
        raise _reject(-2, "limiter.record_429")


def native_limiter_rejections(handle: int) -> int:
    value = int(_lib.vy_res_limiter_rejections(int(handle)))
    if value < 0:
        raise _reject(value, "limiter.rejections")
    return value


def native_clock_ok(local: float, reference: float, max_drift: float, readable: bool) -> bool:
    """Clock-drift gate. `readable` is the boundary's own float conversion."""
    return (
        int(_lib.vy_res_clock_ok(float(local), float(reference), float(max_drift), int(readable)))
        == 1
    )


def native_timeout_problem(
    connect_seconds: float,
    read_seconds: float,
    submit_seconds: float,
    reconcile_seconds: float,
) -> str:
    """Kernel wording naming the first unusable budget ("" = usable)."""
    return _read_problem(
        lambda buf, cap: int(
            _lib.vy_res_timeout_problem(
                float(connect_seconds),
                float(read_seconds),
                float(submit_seconds),
                float(reconcile_seconds),
                buf,
                cap,
            )
        ),
        "timeout",
    )


def native_backoff_problem(
    base_seconds: float, factor: float, max_seconds: float, max_attempts: int
) -> str:
    return _read_problem(
        lambda buf, cap: int(
            _lib.vy_res_backoff_problem(
                float(base_seconds), float(factor), float(max_seconds), int(max_attempts), buf, cap
            )
        ),
        "backoff",
    )


def native_backoff_delay(
    base_seconds: float, factor: float, max_seconds: float, attempt: int
) -> float:
    return float(
        _lib.vy_res_backoff_delay(
            float(base_seconds), float(factor), float(max_seconds), int(attempt)
        )
    )


def native_exhausted(attempts_made: int, max_attempts: int) -> bool:
    return int(_lib.vy_res_exhausted(int(attempts_made), int(max_attempts))) == 1


def native_reconnect_problem(max_attempts: int) -> str:
    return _read_problem(
        lambda buf, cap: int(_lib.vy_res_reconnect_problem(int(max_attempts), buf, cap)),
        "reconnect",
    )


class NativeLimiter:
    """One Rust-side sliding-window throttle, addressed by an opaque handle."""

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        handle = int(_lib.vy_res_limiter_new(int(max_requests), float(window_seconds)))
        if handle < 1:
            raise NativeBridgeError(
                "resilience kernel rejected the throttle configuration: "
                f"max_requests={max_requests} window_seconds={window_seconds}"
            )
        self._handle = handle

    def __del__(self) -> None:
        with contextlib.suppress(Exception):  # interpreter teardown: nothing to report
            self.close()

    def close(self) -> None:
        """Release the Rust slot. Idempotent."""
        handle, self._handle = self._handle, 0
        if handle and int(_lib.vy_res_limiter_free(handle)) != 0:
            raise NativeBridgeError(f"resilience handle {handle} already released")

    def allow(self, now_epoch: float) -> bool:
        return native_limiter_allow(self._handle, now_epoch)

    def record_429(self) -> None:
        native_limiter_record_429(self._handle)

    @property
    def used(self) -> int:
        return native_limiter_used(self._handle)

    @property
    def rejections(self) -> int:
        return native_limiter_rejections(self._handle)


def native_credential_reasons(
    *,
    account_id: str,
    environment: str,
    expected_environment: str,
    key_refs: tuple[str, ...],
    resolvable: tuple[bool, ...],
    require_secrets: bool,
    store_present: bool,
) -> tuple[str, ...]:
    """Why the broker identity is (not) usable — wording owned by Rust."""
    account = _arg(account_id)
    env = _arg(environment)
    expected = _arg(expected_environment)
    refs, ref_count = _string_array(key_refs)
    flags = _flag_array(resolvable)
    return _read_document(
        lambda buf, cap: int(
            _lib.vy_cer_credentials(
                account,
                len(account),
                env,
                len(env),
                expected,
                len(expected),
                refs,
                len(refs),
                ref_count,
                flags,
                ref_count,
                int(require_secrets),
                int(store_present),
                buf,
                cap,
            )
        ),
        "credentials",
    )


def native_account_reasons(
    *, account_id: str, environment: str, expected_account_id: str, expected_environment: str
) -> tuple[str, ...]:
    """Why the adapter-reported identity is (not) the expected account."""
    reported = _arg(account_id)
    env = _arg(environment)
    want_id = _arg(expected_account_id)
    want_env = _arg(expected_environment)
    return _read_document(
        lambda buf, cap: int(
            _lib.vy_cer_account(
                reported,
                len(reported),
                env,
                len(env),
                want_id,
                len(want_id),
                want_env,
                len(want_env),
                buf,
                cap,
            )
        ),
        "account",
    )


def native_risk_reasons(
    *,
    max_position_qty: float,
    max_order_qty: float,
    max_notional: float | None,
    max_exposure_pct: float | None,
    daily_loss_limit: float | None,
    strategy_loss_limit: float | None,
    cooldown_seconds: float,
    max_orders_per_day: int | None,
    require_fresh_data_seconds: float | None,
) -> tuple[str, ...]:
    """Reasons the active risk policy is unusable (empty = every limit sane).

    Presence travels separately from value because ``0.0`` is an invalid
    limit, not the ``None`` sentinel; the wording itself is kernel-owned.
    """
    optional: tuple[float | int | None, ...] = (
        max_notional,
        max_exposure_pct,
        daily_loss_limit,
        strategy_loss_limit,
        max_orders_per_day,
        require_fresh_data_seconds,
    )
    present = sum(1 << bit for bit, value in enumerate(optional) if value is not None)
    slots = [0.0 if value is None else float(value) for value in optional]
    return _read_document(
        lambda buf, cap: int(
            _lib.vy_cer_risk(
                present,
                float(max_position_qty),
                float(max_order_qty),
                slots[0],
                slots[1],
                slots[2],
                slots[3],
                float(cooldown_seconds),
                int(slots[4]),
                slots[5],
                buf,
                cap,
            )
        ),
        "risk policy",
    )


def native_funds_reasons(*, available: float, equity: float, currency: str) -> tuple[str, ...]:
    """Funds preconditions for LIVE, named by the kernel."""
    unit = _arg(currency)
    return _read_document(
        lambda buf, cap: int(
            _lib.vy_cer_funds(float(available), float(equity), unit, len(unit), buf, cap)
        ),
        "funds",
    )


def native_gate_verdict(
    *,
    adapter_present: bool,
    adapter_error: str,
    health_ok: bool,
    health_detail: str,
    credentials_ok: bool,
    credential_reasons: tuple[str, ...],
    account_evaluated: bool,
    account_ok: bool,
    account_reasons: tuple[str, ...],
    risk_present: bool,
    risk_ok: bool,
    risk_reasons: tuple[str, ...],
    kill_halted: bool,
) -> tuple[bool, tuple[tuple[str, bool, str], ...]]:
    """Assemble the five live gates from collected facts. Names, order, joins
    and fallback wording all come back from Rust."""
    error = _arg(adapter_error)
    detail = _arg(health_detail)
    cred, cred_count = _string_array(credential_reasons)
    account, account_count = _string_array(account_reasons)
    risk, risk_count = _string_array(risk_reasons)
    fields = _read_document(
        lambda buf, cap: int(
            _lib.vy_cer_gates(
                int(adapter_present),
                error,
                len(error),
                int(health_ok),
                detail,
                len(detail),
                int(credentials_ok),
                cred,
                len(cred),
                cred_count,
                int(account_evaluated),
                int(account_ok),
                account,
                len(account),
                account_count,
                int(risk_present),
                int(risk_ok),
                risk,
                len(risk),
                risk_count,
                int(kill_halted),
                buf,
                cap,
            )
        ),
        "gates",
    )
    ready = fields[0] == "1"
    count = int(fields[1])
    gates = tuple(
        (fields[2 + offset], fields[3 + offset] == "1", fields[4 + offset])
        for offset in range(0, count * 3, 3)
    )
    return ready, gates


def native_activation_verdict(
    *,
    broker_name: str,
    environment: str,
    credentials_ok: bool,
    credential_reasons: tuple[str, ...],
    account_ok: bool,
    account_reasons: tuple[str, ...],
    market_healthy: bool,
    market_reason: str,
    funds_ok: bool,
    funds_reasons: tuple[str, ...],
    risk_ok: bool,
    risk_reasons: tuple[str, ...],
    verdict_present: bool,
    verdict_blocks: bool,
    verdict_reasons: tuple[str, ...],
    kill_halted: bool,
    gates_present: bool,
    gates_ready: bool,
    failed_gate_names: tuple[str, ...],
    failed_gate_details: tuple[str, ...],
    armed_value: str,
) -> tuple[bool, tuple[tuple[int, str, bool, str], ...], tuple[str, ...]]:
    """Evaluate the twelve activation steps in ceremony order."""
    broker = _arg(broker_name)
    env = _arg(environment)
    market = _arg(market_reason)
    armed = _arg(armed_value)
    cred, cred_count = _string_array(credential_reasons)
    account, account_count = _string_array(account_reasons)
    funds, funds_count = _string_array(funds_reasons)
    risk, risk_count = _string_array(risk_reasons)
    verdict, verdict_count = _string_array(verdict_reasons)
    names, names_count = _string_array(failed_gate_names)
    details, details_count = _string_array(failed_gate_details)
    fields = _read_document(
        lambda buf, cap: int(
            _lib.vy_cer_activation(
                broker,
                len(broker),
                env,
                len(env),
                int(credentials_ok),
                cred,
                len(cred),
                cred_count,
                int(account_ok),
                account,
                len(account),
                account_count,
                int(market_healthy),
                market,
                len(market),
                int(funds_ok),
                funds,
                len(funds),
                funds_count,
                int(risk_ok),
                risk,
                len(risk),
                risk_count,
                int(verdict_present),
                int(verdict_blocks),
                verdict,
                len(verdict),
                verdict_count,
                int(kill_halted),
                int(gates_present),
                int(gates_ready),
                names,
                len(names),
                names_count,
                details,
                len(details),
                details_count,
                armed,
                len(armed),
                buf,
                cap,
            )
        ),
        "activation",
    )
    ready = fields[0] == "1"
    step_count = int(fields[1])
    steps = tuple(
        (
            int(fields[2 + offset]),
            fields[3 + offset],
            fields[4 + offset] == "1",
            fields[5 + offset],
        )
        for offset in range(0, step_count * 4, 4)
    )
    after_steps = 2 + step_count * 4
    blocker_count = int(fields[after_steps])
    blockers = fields[after_steps + 1 : after_steps + 1 + blocker_count]
    return ready, steps, blockers


def native_settle_decision(policy: str) -> tuple[str, str, float | None]:
    """The kernel's answer for one scripted sandbox policy.

    `kind` names the action, `detail` is the next policy text for a wait and
    the reason for a rejection, `quantity` is the scripted partial size.
    """
    script = _arg(policy)
    fields = _read_document(
        lambda buf, cap: int(_lib.vy_sbx_settle_decision(script, len(script), buf, cap)),
        "settle",
    )
    kind, detail, has_number = fields[0], fields[1], fields[2] == "1"
    return kind, detail, float(fields[3]) if has_number else None
