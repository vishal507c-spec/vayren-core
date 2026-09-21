"""Loader for the Rust `vayren_core` cdylib (constitution §8: Rust owns the
numeric/lifecycle kernels; this module is the typed interop boundary).

Fail-closed: a missing library, ABI mismatch, or handshake failure raises
`NativeBridgeError` with an actionable message. There is deliberately NO
silent Python fallback — the Rust implementation is the single authority
for the kernels it owns (migration §12).
"""

from __future__ import annotations

import ctypes
import os
import platform
from pathlib import Path

ABI_VERSION = 1
ORDER_STATE_COUNT = 13


class NativeBridgeError(RuntimeError):
    """The Rust native library is unavailable or incompatible."""


def _candidate_names() -> tuple[str, ...]:
    system = platform.system().lower()
    if system.startswith("win"):
        return ("vayren_core.dll",)
    if system == "darwin":
        return ("libvayren_core.dylib", "vayren_core.dylib")
    return ("libvayren_core.so", "vayren_core.so")


def find_library() -> Path:
    """Locate the built cdylib. Env `VAYREN_NATIVE_LIB` wins; otherwise the
    release (then debug) target dir of the repo `rust/` workspace."""
    override = os.environ.get("VAYREN_NATIVE_LIB", "").strip()
    if override:
        path = Path(override)
        if path.is_file():
            return path
        raise NativeBridgeError(f"VAYREN_NATIVE_LIB points at a missing file: {override!r}")
    root = Path(__file__).resolve().parent.parent.parent.parent
    for profile in ("release", "debug"):
        for name in _candidate_names():
            candidate = root / "rust" / "target" / profile / name
            if candidate.is_file():
                return candidate
    raise NativeBridgeError(
        "Rust native library not found. Build it first: "
        "`python scripts/build_rust.py` (requires a Rust toolchain)."
    )


def _configure(lib: ctypes.CDLL) -> ctypes.CDLL:
    lib.vy_abi_version.restype = ctypes.c_uint32
    lib.vy_abi_version.argtypes = []
    lib.vy_order_state_count.restype = ctypes.c_int32
    lib.vy_order_state_count.argtypes = []
    lib.vy_order_transition_allowed.restype = ctypes.c_int32
    lib.vy_order_transition_allowed.argtypes = [ctypes.c_int32, ctypes.c_int32]
    lib.vy_order_is_terminal.restype = ctypes.c_int32
    lib.vy_order_is_terminal.argtypes = [ctypes.c_int32]
    lib.vy_order_transitions.restype = ctypes.c_uint32
    lib.vy_order_transitions.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32]
    lib.vy_order_terminal_states.restype = ctypes.c_uint32
    lib.vy_order_terminal_states.argtypes = [
        ctypes.POINTER(ctypes.c_int32),
        ctypes.c_uint32,
    ]
    lib.vy_max_drawdown.restype = None
    lib.vy_max_drawdown.argtypes = [
        ctypes.POINTER(ctypes.c_double),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_double),
    ]
    lib.vy_equity_curve.restype = ctypes.c_size_t
    lib.vy_equity_curve.argtypes = [
        ctypes.c_double,
        ctypes.POINTER(ctypes.c_double),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_double),
    ]
    lib.vy_sharpe.restype = ctypes.c_int32
    lib.vy_sharpe.argtypes = [
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_double),
        ctypes.c_size_t,
        ctypes.c_double,
        ctypes.POINTER(ctypes.c_double),
    ]
    lib.vy_mode.restype = ctypes.c_int32
    lib.vy_mode.argtypes = [
        ctypes.POINTER(ctypes.c_int64),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_int64),
    ]
    if hasattr(lib, "vy_exec_arm_transition"):
        lib.vy_exec_arm_transition.restype = ctypes.c_int32
        lib.vy_exec_arm_transition.argtypes = [ctypes.c_int32, ctypes.c_int32]
    if hasattr(lib, "vy_exec_lifecycle_transition_allowed"):
        lib.vy_exec_lifecycle_transition_allowed.restype = ctypes.c_int32
        lib.vy_exec_lifecycle_transition_allowed.argtypes = [ctypes.c_int32, ctypes.c_int32]
    if hasattr(lib, "vy_exec_planner_plan"):
        lib.vy_exec_planner_plan.restype = ctypes.c_int32
        lib.vy_exec_planner_plan.argtypes = [
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
    if hasattr(lib, "vy_exec_ledger_apply_fill"):
        lib.vy_exec_ledger_apply_fill.restype = ctypes.c_int32
        lib.vy_exec_ledger_apply_fill.argtypes = [
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
    if hasattr(lib, "vy_ks_new"):
        _configure_kill_switch(lib)
    if hasattr(lib, "vy_timeframe_ladder_count"):
        _configure_timeframe(lib)
    if hasattr(lib, "vy_bt_try_close"):
        _configure_backtest_position(lib)
    if hasattr(lib, "vy_risk_within_session"):
        _configure_risk_session(lib)
    if hasattr(lib, "vy_norm_new"):
        _configure_stream_normalizer(lib)
    if hasattr(lib, "vy_bar_return_pct"):
        _configure_market_bar(lib)
    if hasattr(lib, "vy_cal_market_open"):
        _configure_download_kernel(lib)
    if hasattr(lib, "vy_agg_anchor_seconds"):
        _configure_rule_kernels(lib)
    return lib


def _configure_risk_session(lib: ctypes.CDLL) -> None:
    """Risk session/clock ABI (UTF-8 text in, one verdict code out).

    Bound lengths are `c_int64` because `-1` is the sentinel for an absent
    (`None`) bound; the plain `c_char_p` slots accept `None` as NULL.
    """
    lib.vy_risk_within_session.restype = ctypes.c_int32
    lib.vy_risk_within_session.argtypes = [
        ctypes.c_char_p,
        ctypes.c_int64,
        ctypes.c_char_p,
        ctypes.c_int64,
        ctypes.c_char_p,
        ctypes.c_int64,
    ]
    lib.vy_risk_clock_sane.restype = ctypes.c_int32
    lib.vy_risk_clock_sane.argtypes = [
        ctypes.c_char_p,
        ctypes.c_int64,
        ctypes.c_double,
        ctypes.c_double,
    ]


def _configure_timeframe(lib: ctypes.CDLL) -> None:
    """Timeframe ladder ABI (labels cross as UTF-8 buffer pairs)."""
    char_out = ctypes.POINTER(ctypes.c_char)
    text_out = [char_out, ctypes.c_size_t]
    lib.vy_timeframe_ladder_count.restype = ctypes.c_int32
    lib.vy_timeframe_ladder_count.argtypes = []
    lib.vy_timeframe_ladder_seconds.restype = ctypes.c_int64
    lib.vy_timeframe_ladder_seconds.argtypes = [ctypes.c_int32]
    lib.vy_timeframe_ladder_label.restype = ctypes.c_int32
    lib.vy_timeframe_ladder_label.argtypes = [ctypes.c_int32, *text_out]
    lib.vy_timeframe_seconds.restype = ctypes.c_int64
    lib.vy_timeframe_seconds.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
    lib.vy_timeframe_label.restype = ctypes.c_int32
    lib.vy_timeframe_label.argtypes = [ctypes.c_int64, *text_out]
    lib.vy_timeframe_generate_label.restype = ctypes.c_int32
    lib.vy_timeframe_generate_label.argtypes = [ctypes.c_int64, *text_out]
    lib.vy_timeframe_available.restype = ctypes.c_int32
    lib.vy_timeframe_available.argtypes = [ctypes.c_int64, *text_out]


def _configure_backtest_position(lib: ctypes.CDLL) -> None:
    """Backtest position kernel ABI (floats in, one packed 48-byte result out).

    The result slot is typed `c_void_p`: the bridge owns the struct layout and
    hands over `ctypes.byref(...)` of it, which ctypes rejects against a
    concrete `POINTER(...)` argtype.
    """
    trade_out = ctypes.c_void_p
    lib.vy_bt_try_close.restype = ctypes.c_int32
    lib.vy_bt_try_close.argtypes = [
        ctypes.c_int32,
        ctypes.c_int32,
        ctypes.c_double,
        ctypes.c_int32,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_int32,
        trade_out,
    ]
    lib.vy_bt_close_trade.restype = ctypes.c_int32
    lib.vy_bt_close_trade.argtypes = [
        ctypes.c_int32,
        ctypes.c_int32,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_int32,
        ctypes.c_double,
        trade_out,
    ]


def _configure_kill_switch(lib: ctypes.CDLL) -> None:
    """Handle-based kill-switch ABI (strings cross as UTF-8 buffer pairs)."""
    char_out = ctypes.POINTER(ctypes.c_char)
    lib.vy_ks_new.restype = ctypes.c_int64
    lib.vy_ks_new.argtypes = []
    lib.vy_ks_free.restype = ctypes.c_int32
    lib.vy_ks_free.argtypes = [ctypes.c_int64]
    lib.vy_ks_level_count.restype = ctypes.c_int32
    lib.vy_ks_level_count.argtypes = []
    lib.vy_ks_level_name.restype = ctypes.c_int32
    lib.vy_ks_level_name.argtypes = [ctypes.c_int32, char_out, ctypes.c_size_t]
    lib.vy_ks_engage.restype = ctypes.c_int32
    lib.vy_ks_engage.argtypes = [
        ctypes.c_int64,
        ctypes.c_char_p,
        ctypes.c_size_t,
        ctypes.c_char_p,
        ctypes.c_size_t,
    ]
    lib.vy_ks_disengage.restype = ctypes.c_int32
    lib.vy_ks_disengage.argtypes = [ctypes.c_int64, ctypes.c_char_p, ctypes.c_size_t]
    lib.vy_ks_is_halted.restype = ctypes.c_int32
    lib.vy_ks_is_halted.argtypes = [ctypes.c_int64, ctypes.c_char_p, ctypes.c_size_t]
    lib.vy_ks_state_engaged.restype = ctypes.c_int32
    lib.vy_ks_state_engaged.argtypes = [ctypes.c_int64, ctypes.c_char_p, ctypes.c_size_t]
    lib.vy_ks_state_reason.restype = ctypes.c_int32
    lib.vy_ks_state_reason.argtypes = [
        ctypes.c_int64,
        ctypes.c_char_p,
        ctypes.c_size_t,
        char_out,
        ctypes.c_size_t,
    ]
    lib.vy_ks_state_engaged_at.restype = ctypes.c_int32
    lib.vy_ks_state_engaged_at.argtypes = lib.vy_ks_state_reason.argtypes
    lib.vy_ks_serialize.restype = ctypes.c_int32
    lib.vy_ks_serialize.argtypes = [ctypes.c_int64, char_out, ctypes.c_size_t]
    lib.vy_ks_load.restype = ctypes.c_int32
    lib.vy_ks_load.argtypes = [ctypes.c_int64, ctypes.c_char_p, ctypes.c_size_t]
    lib.vy_ks_last_message.restype = ctypes.c_int32
    lib.vy_ks_last_message.argtypes = [ctypes.c_int64, char_out, ctypes.c_size_t]


def _configure_stream_normalizer(lib: ctypes.CDLL) -> None:
    """Handle-based stream-gate ABI (symbol in as UTF-8, verdict document out).

    Symbol lengths are `c_int64` to match the kernel's signed byte counts; both
    verdicts (observation and health) are read back through a separate
    non-mutating call so a capacity probe can never replay or recount.
    """
    char_out = ctypes.POINTER(ctypes.c_char)
    lib.vy_norm_new.restype = ctypes.c_int64
    lib.vy_norm_new.argtypes = [ctypes.c_int64, ctypes.c_double, ctypes.c_double]
    lib.vy_norm_free.restype = ctypes.c_int32
    lib.vy_norm_free.argtypes = [ctypes.c_int64]
    lib.vy_norm_observe.restype = ctypes.c_int32
    lib.vy_norm_observe.argtypes = [
        ctypes.c_int64,
        ctypes.c_char_p,
        ctypes.c_int64,
        ctypes.c_int64,
        ctypes.c_int64,
        ctypes.c_double,
        ctypes.c_int32,
    ]
    lib.vy_norm_last_document.restype = ctypes.c_int32
    lib.vy_norm_last_document.argtypes = [ctypes.c_int64, char_out, ctypes.c_size_t]
    lib.vy_norm_last_health.restype = ctypes.c_int32
    lib.vy_norm_last_health.argtypes = [ctypes.c_int64, char_out, ctypes.c_size_t]
    lib.vy_norm_check_health.restype = ctypes.c_int32
    lib.vy_norm_check_health.argtypes = [
        ctypes.c_int64,
        ctypes.c_char_p,
        ctypes.c_int64,
        ctypes.c_double,
    ]
    lib.vy_norm_is_stale.restype = ctypes.c_int32
    lib.vy_norm_is_stale.argtypes = [ctypes.c_int64, ctypes.c_char_p, ctypes.c_int64]
    lib.vy_norm_expected_seq.restype = ctypes.c_int64
    lib.vy_norm_expected_seq.argtypes = [ctypes.c_int64, ctypes.c_char_p, ctypes.c_int64]
    lib.vy_norm_stats.restype = ctypes.c_int32
    lib.vy_norm_stats.argtypes = [ctypes.c_int64, char_out, ctypes.c_size_t]


def _configure_market_bar(lib: ctypes.CDLL) -> None:
    """Market bar ABI (plain doubles in, the kernel's answer out)."""
    lib.vy_bar_return_pct.restype = ctypes.c_double
    lib.vy_bar_return_pct.argtypes = [ctypes.c_double, ctypes.c_double]


def _configure_download_kernel(lib: ctypes.CDLL) -> None:
    """Download kernel ABI (naive seconds in, verdict documents out).

    Day counts and window bounds are `c_int64` wall-clock seconds; the holiday
    set, the listing-boundary label and the candle key cross as UTF-8 text
    pairs, where a negative length means the row is absent.
    """
    char_out = ctypes.POINTER(ctypes.c_char)
    lib.vy_cal_market_open.restype = ctypes.c_int32
    lib.vy_cal_market_open.argtypes = [
        ctypes.c_int64,
        ctypes.c_int64,
        ctypes.c_int64,
        ctypes.c_int64,
        ctypes.c_int64,
    ]
    lib.vy_cal_is_trading_day.restype = ctypes.c_int32
    lib.vy_cal_is_trading_day.argtypes = [ctypes.c_int64, ctypes.c_char_p, ctypes.c_int64]
    lib.vy_cal_count_trading_days.restype = ctypes.c_int64
    lib.vy_cal_count_trading_days.argtypes = [
        ctypes.c_int64,
        ctypes.c_int64,
        ctypes.c_char_p,
        ctypes.c_int64,
    ]
    lib.vy_cal_target_start.restype = ctypes.c_int64
    lib.vy_cal_target_start.argtypes = [ctypes.c_int64, ctypes.c_int64]
    lib.vy_cal_today_end.restype = ctypes.c_int64
    lib.vy_cal_today_end.argtypes = [ctypes.c_int64]
    lib.vy_dl_coverage_pct.restype = ctypes.c_double
    lib.vy_dl_coverage_pct.argtypes = [ctypes.c_int64, ctypes.c_int64]
    lib.vy_dl_decide_coverage.restype = ctypes.c_int32
    lib.vy_dl_decide_coverage.argtypes = [
        ctypes.c_int64,
        ctypes.c_int64,
        ctypes.c_int64,
        ctypes.c_int64,
        ctypes.c_int64,
        ctypes.c_char_p,
        ctypes.c_int64,
        ctypes.c_int64,
        ctypes.c_int64,
        ctypes.c_int64,
        ctypes.c_int64,
        ctypes.c_int64,
        ctypes.c_int64,
        ctypes.c_char_p,
        ctypes.c_int64,
        char_out,
        ctypes.c_size_t,
    ]
    lib.vy_ts_candle_time.restype = ctypes.c_int32
    lib.vy_ts_candle_time.argtypes = [ctypes.c_char_p, ctypes.c_int64, char_out, ctypes.c_size_t]
    lib.vy_ts_from_secs.restype = ctypes.c_int32
    lib.vy_ts_from_secs.argtypes = [ctypes.c_int64, char_out, ctypes.c_size_t]
    lib.vy_sym_filename.restype = ctypes.c_int32
    lib.vy_sym_filename.argtypes = [ctypes.c_char_p, ctypes.c_int64, char_out, ctypes.c_size_t]
    lib.vy_dl_chunk_count.restype = ctypes.c_int64
    lib.vy_dl_chunk_count.argtypes = [ctypes.c_int64, ctypes.c_int64, ctypes.c_int64]
    lib.vy_dl_chunk_windows.restype = ctypes.c_int32
    lib.vy_dl_chunk_windows.argtypes = [
        ctypes.c_int64,
        ctypes.c_int64,
        ctypes.c_int64,
        char_out,
        ctypes.c_size_t,
    ]
    lib.vy_dl_head_sweep_eligible.restype = ctypes.c_int32
    lib.vy_dl_head_sweep_eligible.argtypes = [
        ctypes.c_int64,
        ctypes.c_int64,
        ctypes.c_int64,
    ]
    lib.vy_dl_job_window.restype = ctypes.c_int32
    lib.vy_dl_job_window.argtypes = [
        ctypes.c_int32,
        ctypes.c_int64,
        ctypes.c_int64,
        ctypes.c_int64,
        char_out,
        ctypes.c_size_t,
    ]


def _configure_rule_kernels(lib: ctypes.CDLL) -> None:
    """Pure-rule ABI for the live paths that used to hold their own copies.

    Every entry answers one question and holds no state: text in as UTF-8 with
    its length (`-1` marks an absent slot), the kernel's answer out as doubles,
    integers or a length-delimited document.
    """
    char_in = ctypes.c_char_p
    char_out = ctypes.POINTER(ctypes.c_char)
    lib.vy_agg_anchor_seconds.restype = ctypes.c_int64
    lib.vy_agg_anchor_seconds.argtypes = [char_in, ctypes.c_int64]
    lib.vy_agg_bucket_start.restype = ctypes.c_int32
    lib.vy_agg_bucket_start.argtypes = [
        char_in,
        ctypes.c_int64,
        ctypes.c_int64,
        ctypes.c_int64,
        char_out,
        ctypes.c_size_t,
    ]
    lib.vy_agg_fold_tick.restype = ctypes.c_int32
    lib.vy_agg_fold_tick.argtypes = [
        ctypes.c_int32,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_int32,
        ctypes.c_double,
        ctypes.POINTER(ctypes.c_double),
        ctypes.c_size_t,
    ]
    lib.vy_agg_closed_count.restype = ctypes.c_int64
    lib.vy_agg_closed_count.argtypes = [ctypes.c_int64, ctypes.c_int32]
    lib.vy_norm_stale_floor.restype = ctypes.c_double
    lib.vy_norm_stale_floor.argtypes = [ctypes.c_double]
    lib.vy_norm_gap_stale.restype = ctypes.c_int32
    lib.vy_norm_gap_stale.argtypes = [
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
    ]
    lib.vy_norm_watermark.restype = ctypes.c_int64
    lib.vy_norm_watermark.argtypes = [ctypes.c_int64, ctypes.c_int64]
    lib.vy_norm_gap_reason.restype = ctypes.c_int32
    lib.vy_norm_gap_reason.argtypes = [char_out, ctypes.c_size_t]
    lib.vy_dl_validate_range.restype = ctypes.c_int32
    lib.vy_dl_validate_range.argtypes = [
        char_in,
        ctypes.c_int64,
        char_in,
        ctypes.c_int64,
        char_out,
        ctypes.c_size_t,
    ]
    lib.vy_dl_job_rank.restype = ctypes.c_int64
    lib.vy_dl_job_rank.argtypes = [char_in, ctypes.c_int64]


def load_vayren_core() -> ctypes.CDLL:
    """Load the cdylib and verify the ABI handshake (version + state count)."""
    path = find_library()
    try:
        lib = _configure(ctypes.CDLL(str(path)))
    except OSError as exc:
        raise NativeBridgeError(f"cannot load Rust native library {path}: {exc}") from exc
    if lib.vy_abi_version() != ABI_VERSION:
        raise NativeBridgeError(
            f"native ABI mismatch at {path}: library={lib.vy_abi_version()} "
            f"expected={ABI_VERSION} (rebuild: `python scripts/build_rust.py`)"
        )
    if lib.vy_order_state_count() != ORDER_STATE_COUNT:
        raise NativeBridgeError(
            f"native order-state vocabulary drift at {path}: "
            f"library={lib.vy_order_state_count()} expected={ORDER_STATE_COUNT}"
        )
    return lib
