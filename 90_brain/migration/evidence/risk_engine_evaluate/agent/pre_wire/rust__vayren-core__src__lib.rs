//! VAYREN Rust-owned kernels — C ABI for the Python boundary.
//!
//! The Python layer (execution/backtest/market) consumes these symbols via
//! `ctypes`. This is the single authoritative implementation of the order
//! lifecycle table, backtest numeric kernels and timeframe aggregation; the
//! Python side holds no duplicate logic (constitution §1, migration §12).
//!
//! ABI rules: fixed-width integers, `f64` doubles, caller-allocated buffers,
//! no panics across the boundary (every entry point is guarded), unknown
//! inputs fail closed.

pub mod aggregate;
pub mod metrics;
pub mod order_state;
pub mod stats;

use aggregate::AggBucket;

/// ABI handshake version. Bumped on any breaking layout/semantics change.
pub const ABI_VERSION: u32 = 1;

/// Safe slice from a raw pointer + length (empty slice for null/zero).
///
/// # Safety
/// `ptr` must be null or point to `len` initialized, aligned elements that
/// outlive the call.
unsafe fn slice<'a, T>(ptr: *const T, len: usize) -> &'a [T] {
    if ptr.is_null() || len == 0 {
        &[]
    } else {
        std::slice::from_raw_parts(ptr, len)
    }
}

/// # Safety
/// `ptr` must be null or point to `len` writable, aligned elements.
unsafe fn slice_mut<'a, T>(ptr: *mut T, len: usize) -> &'a mut [T] {
    if ptr.is_null() || len == 0 {
        &mut []
    } else {
        std::slice::from_raw_parts_mut(ptr, len)
    }
}

#[no_mangle]
pub extern "C" fn vy_abi_version() -> u32 {
    ABI_VERSION
}

// ── order lifecycle ───────────────────────────────────────────────────────

#[no_mangle]
pub extern "C" fn vy_order_state_count() -> i32 {
    order_state::STATE_COUNT
}

#[no_mangle]
pub extern "C" fn vy_order_transition_allowed(from: i32, to: i32) -> i32 {
    i32::from(order_state::is_legal_transition(from, to))
}

#[no_mangle]
pub extern "C" fn vy_order_is_terminal(state: i32) -> i32 {
    i32::from(order_state::is_terminal(state))
}

/// Write every legal edge as a packed `from<<16 | to` u32 into `out`.
/// Returns the number of edges in the table (caller sizes `out` >= that).
#[no_mangle]
pub unsafe extern "C" fn vy_order_transitions(out: *mut u32, cap: u32) -> u32 {
    let mut written = 0u32;
    for from in 0..order_state::STATE_COUNT {
        let targets: Vec<i32> = (0..order_state::STATE_COUNT)
            .filter(|&to| order_state::is_legal_transition(from, to))
            .collect();
        for to in targets {
            if written < cap {
                *out.add(written as usize) = ((from as u32) << 16) | (to as u32);
            }
            written += 1;
        }
    }
    written
}

/// Write the terminal state codes into `out`; returns how many were written.
#[no_mangle]
pub unsafe extern "C" fn vy_order_terminal_states(out: *mut i32, cap: u32) -> u32 {
    let terminals = [
        order_state::FILLED,
        order_state::REJECTED,
        order_state::CANCELLED,
        order_state::EXPIRED,
    ];
    let count = terminals.len().min(cap as usize);
    for (i, &state) in terminals.iter().take(count).enumerate() {
        *out.add(i) = state;
    }
    count as u32
}

// ── backtest metrics ──────────────────────────────────────────────────────

#[no_mangle]
pub unsafe extern "C" fn vy_max_drawdown(
    equities: *const f64,
    n: usize,
    out_pct: *mut f64,
    out_abs: *mut f64,
) {
    let (pct, abs) = metrics::max_drawdown(slice(equities, n));
    if !out_pct.is_null() {
        *out_pct = pct;
    }
    if !out_abs.is_null() {
        *out_abs = abs;
    }
}

/// Fill `out` (capacity >= 2*n doubles) with interleaved
/// `(equity, drawdown_pct)` pairs in trade order. Returns n.
#[no_mangle]
pub unsafe extern "C" fn vy_equity_curve(
    initial: f64,
    pnls: *const f64,
    n: usize,
    out: *mut f64,
) -> usize {
    let (equities, drawdowns) = metrics::equity_curve(initial, slice(pnls, n));
    let flat = slice_mut(out, n.saturating_mul(2));
    for i in 0..n {
        flat[2 * i] = equities[i];
        flat[2 * i + 1] = drawdowns[i];
    }
    n
}

/// Sharpe ratio; returns 1 and writes `out` when defined, else 0 (None).
#[no_mangle]
pub unsafe extern "C" fn vy_sharpe(
    pnls: *const f64,
    bars_held: *const f64,
    n: usize,
    initial: f64,
    out: *mut f64,
) -> i32 {
    match metrics::sharpe(slice(pnls, n), slice(bars_held, n), initial) {
        Some(value) => {
            if !out.is_null() {
                *out = value;
            }
            1
        }
        None => 0,
    }
}

// ── small statistics ─────────────────────────────────────────────────────

/// Most-frequent value with first-seen tie-break (Python Counter mode
/// semantics). Returns 1 and writes `out` when the input is non-empty,
/// else 0 (None).
#[no_mangle]
pub unsafe extern "C" fn vy_mode(values: *const i64, n: usize, out: *mut i64) -> i32 {
    match stats::mode(slice(values, n)) {
        Some(value) => {
            if !out.is_null() {
                *out = value;
            }
            1
        }
        None => 0,
    }
}

// ── timeframe aggregation ─────────────────────────────────────────────────

/// Aggregate ascending base rows into buckets. All input arrays share length
/// `n`. Writes up to `out_cap` buckets and returns the number produced (the
/// number never exceeds `n`).
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn vy_aggregate(
    days: *const i32,
    secs: *const i32,
    opens: *const f64,
    highs: *const f64,
    lows: *const f64,
    closes: *const f64,
    volumes: *const f64,
    n: usize,
    timeframe_seconds: i64,
    session_start: i64,
    out: *mut AggBucket,
    out_cap: usize,
) -> usize {
    let buckets = aggregate::aggregate(
        slice(days, n),
        slice(secs, n),
        slice(opens, n),
        slice(highs, n),
        slice(lows, n),
        slice(closes, n),
        slice(volumes, n),
        timeframe_seconds,
        session_start,
    );
    let count = buckets.len().min(out_cap);
    let out_slice = slice_mut(out, count);
    out_slice.copy_from_slice(&buckets[..count]);
    buckets.len()
}
