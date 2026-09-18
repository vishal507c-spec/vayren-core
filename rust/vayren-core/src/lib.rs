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
pub mod backtest;
pub mod backtest_directional;
pub mod backtest_engine;
pub mod backtest_events;
pub mod chart_events;
pub mod chart_math;
pub mod chart_model;
pub mod data_events;
pub mod download;
pub mod event_bus;
pub mod execution;
pub mod execution_engine;
pub mod execution_events;
pub mod indicator;
pub mod kill_switch;
pub mod market;
pub mod market_events;
pub mod metrics;
pub mod order_state;
pub mod registry;
pub mod risk;
pub mod risk_engine;
pub mod stats;
pub mod throttle;

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

// ── technical indicators ─────────────────────────────────────────────────

/// SMA: write output into `out` (capacity >= n - period + 1). Returns count
/// written. Period zero or too-short input -> 0.
#[no_mangle]
pub unsafe extern "C" fn vy_sma(
    prices: *const f64,
    n: usize,
    period: usize,
    out: *mut f64,
    out_cap: usize,
) -> usize {
    let result = indicator::sma(slice(prices, n), period);
    let count = result.len().min(out_cap);
    let out_slice = slice_mut(out, count);
    out_slice.copy_from_slice(&result[..count]);
    result.len()
}

/// EMA: write output into `out` (capacity >= n). Returns 1 on success, 0 on
/// empty/zero-period input.
#[no_mangle]
pub unsafe extern "C" fn vy_ema(prices: *const f64, n: usize, period: usize, out: *mut f64) -> i32 {
    match indicator::ema(slice(prices, n), period) {
        Some(result) => {
            let out_slice = slice_mut(out, n);
            out_slice.copy_from_slice(&result);
            1
        }
        None => 0,
    }
}

/// RSI: write output into `out` (capacity >= n). Each element is either a
/// valid RSI (0-100) or NaN (not enough data). Returns n.
#[no_mangle]
pub unsafe extern "C" fn vy_rsi(
    prices: *const f64,
    n: usize,
    period: usize,
    out: *mut f64,
) -> usize {
    let result = indicator::rsi(slice(prices, n), period);
    let out_slice = slice_mut(out, n);
    for (i, val) in result.iter().enumerate() {
        out_slice[i] = val.unwrap_or(f64::NAN);
    }
    n
}

/// ATR: write output into `out` (capacity >= n). Each element is either a
/// valid ATR or NaN (not enough data). Returns n.
#[no_mangle]
pub unsafe extern "C" fn vy_atr(
    highs: *const f64,
    lows: *const f64,
    closes: *const f64,
    n: usize,
    period: usize,
    out: *mut f64,
) -> usize {
    let result = indicator::atr(slice(highs, n), slice(lows, n), slice(closes, n), period);
    let out_slice = slice_mut(out, n);
    for (i, val) in result.iter().enumerate() {
        out_slice[i] = val.unwrap_or(f64::NAN);
    }
    n
}

/// VWAP: write output into `out` (capacity >= n). Returns 1 on success, 0 on
/// mismatched/empty input.
#[no_mangle]
pub unsafe extern "C" fn vy_vwap(
    highs: *const f64,
    lows: *const f64,
    closes: *const f64,
    volumes: *const f64,
    n: usize,
    out: *mut f64,
) -> i32 {
    match indicator::vwap(
        slice(highs, n),
        slice(lows, n),
        slice(closes, n),
        slice(volumes, n),
    ) {
        Some(result) => {
            let out_slice = slice_mut(out, n);
            out_slice.copy_from_slice(&result);
            1
        }
        None => 0,
    }
}

// ── risk policy kernel (agent-migrated) ───────────────────────────────

/// Evaluate pure scalar risk checks. Returns the check bitmask.
/// Wrong-length inputs fail closed (mask 0 = deny); panics are caught.
#[no_mangle]
pub unsafe extern "C" fn vy_risk_kernel(
    f64_values: *const f64,
    f64_len: usize,
    i64_values: *const i64,
    i64_len: usize,
) -> u32 {
    let result = std::panic::catch_unwind(|| {
        if f64_len != 20 || i64_len != 14 {
            return 0u32;
        }
        let f64_values = slice(f64_values, f64_len);
        let i64_values = slice(i64_values, i64_len);
        let request_data_age_seconds = f64_values.get(0).copied().unwrap_or(0.0);
        let policy_require_fresh_data_seconds = f64_values.get(1).copied().unwrap_or(0.0);
        let request_spread_pct = f64_values.get(2).copied().unwrap_or(0.0);
        let policy_spread_limit_pct = f64_values.get(3).copied().unwrap_or(0.0);
        let policy_cooldown_seconds = f64_values.get(4).copied().unwrap_or(0.0);
        let request_now_epoch = f64_values.get(5).copied().unwrap_or(0.0);
        let request_last_order_epoch = f64_values.get(6).copied().unwrap_or(0.0);
        let request_quantity = f64_values.get(7).copied().unwrap_or(0.0);
        let request_price = f64_values.get(8).copied().unwrap_or(0.0);
        let policy_max_order_qty = f64_values.get(9).copied().unwrap_or(0.0);
        let policy_max_notional = f64_values.get(10).copied().unwrap_or(0.0);
        let request_position_qty = f64_values.get(11).copied().unwrap_or(0.0);
        let policy_max_position_qty = f64_values.get(12).copied().unwrap_or(0.0);
        let request_equity = f64_values.get(13).copied().unwrap_or(0.0);
        let policy_max_exposure_pct = f64_values.get(14).copied().unwrap_or(0.0);
        let request_day_pnl = f64_values.get(15).copied().unwrap_or(0.0);
        let policy_daily_loss_limit = f64_values.get(16).copied().unwrap_or(0.0);
        let request_strategy_day_pnl = f64_values.get(17).copied().unwrap_or(0.0);
        let policy_strategy_loss_limit = f64_values.get(18).copied().unwrap_or(0.0);
        let request_available_capital = f64_values.get(19).copied().unwrap_or(0.0);
        let request_orders_today = i64_values.get(0).copied().unwrap_or(0);
        let policy_max_orders_per_day = i64_values.get(1).copied().unwrap_or(0);
        let request_broker_healthy = i64_values.get(2).copied().unwrap_or(0) != 0;
        let policy_require_fresh_data_seconds_present =
            i64_values.get(3).copied().unwrap_or(0) != 0;
        let request_data_age_seconds_present = i64_values.get(4).copied().unwrap_or(0) != 0;
        let policy_spread_limit_pct_present = i64_values.get(5).copied().unwrap_or(0) != 0;
        let request_spread_pct_present = i64_values.get(6).copied().unwrap_or(0) != 0;
        let request_last_order_epoch_present = i64_values.get(7).copied().unwrap_or(0) != 0;
        let policy_max_orders_per_day_present = i64_values.get(8).copied().unwrap_or(0) != 0;
        let policy_max_notional_present = i64_values.get(9).copied().unwrap_or(0) != 0;
        let side_is_buy = i64_values.get(10).copied().unwrap_or(0) != 0;
        let policy_max_exposure_pct_present = i64_values.get(11).copied().unwrap_or(0) != 0;
        let policy_daily_loss_limit_present = i64_values.get(12).copied().unwrap_or(0) != 0;
        let policy_strategy_loss_limit_present = i64_values.get(13).copied().unwrap_or(0) != 0;
        let inputs = risk::RiskKernelInputs {
            request_broker_healthy,
            policy_require_fresh_data_seconds_present,
            request_data_age_seconds_present,
            request_data_age_seconds,
            policy_require_fresh_data_seconds,
            policy_spread_limit_pct_present,
            request_spread_pct_present,
            request_spread_pct,
            policy_spread_limit_pct,
            policy_cooldown_seconds,
            request_last_order_epoch_present,
            request_now_epoch,
            request_last_order_epoch,
            policy_max_orders_per_day_present,
            request_orders_today,
            policy_max_orders_per_day,
            request_quantity,
            request_price,
            policy_max_order_qty,
            policy_max_notional_present,
            policy_max_notional,
            request_position_qty,
            side_is_buy,
            policy_max_position_qty,
            policy_max_exposure_pct_present,
            request_equity,
            policy_max_exposure_pct,
            policy_daily_loss_limit_present,
            request_day_pnl,
            policy_daily_loss_limit,
            policy_strategy_loss_limit_present,
            request_strategy_day_pnl,
            policy_strategy_loss_limit,
            request_available_capital,
        };
        risk::evaluate_risk_kernel(&inputs)
    });
    result.unwrap_or(0)
}

// ── execution core kernels ────────────────────────────────────────────────

#[no_mangle]
pub extern "C" fn vy_exec_arm_transition(current: i32, target: i32) -> i32 {
    use execution_engine::LiveArm;
    let map = |c: i32| match c {
        0 => Some(LiveArm::Disarmed),
        1 => Some(LiveArm::Arming),
        2 => Some(LiveArm::Armed),
        3 => Some(LiveArm::Running),
        4 => Some(LiveArm::Halted),
        _ => None,
    };
    let unmap = |a: LiveArm| match a {
        LiveArm::Disarmed => 0,
        LiveArm::Arming => 1,
        LiveArm::Armed => 2,
        LiveArm::Running => 3,
        LiveArm::Halted => 4,
    };
    let (Some(cur), Some(tgt)) = (map(current), map(target)) else {
        return -1;
    };
    match execution_engine::arm_transition(cur, tgt) {
        Ok(res) => unmap(res),
        Err(_) => -1,
    }
}

#[no_mangle]
pub extern "C" fn vy_exec_lifecycle_transition_allowed(from: i32, to: i32) -> i32 {
    use execution_engine::LifecycleState;
    let map = |c: i32| match c {
        0 => Some(LifecycleState::Created),
        1 => Some(LifecycleState::Validating),
        2 => Some(LifecycleState::WarmingUp),
        3 => Some(LifecycleState::Ready),
        4 => Some(LifecycleState::Running),
        5 => Some(LifecycleState::Paused),
        6 => Some(LifecycleState::Stopping),
        7 => Some(LifecycleState::Stopped),
        8 => Some(LifecycleState::Error),
        9 => Some(LifecycleState::Recovering),
        10 => Some(LifecycleState::Reconciling),
        _ => None,
    };
    let (Some(f), Some(t)) = (map(from), map(to)) else {
        return 0;
    };
    let mut sl = execution_engine::StrategyLifecycle {
        state: f,
        reason: String::new(),
    };
    if sl.transition(t, "").is_ok() {
        1
    } else {
        0
    }
}

#[no_mangle]
pub unsafe extern "C" fn vy_exec_planner_plan(
    quantity: f64,
    order_type: i32, // 0 = MARKET, 1 = LIMIT
    reference_price: f64,
    prefer_limit: i32,
    size_multiplier: f64,
    out_qty: *mut f64,
    out_order_type: *mut i32,
    out_has_limit: *mut i32,
    out_limit_price: *mut f64,
) -> i32 {
    if size_multiplier <= 0.0 || size_multiplier > 1.0 {
        return -1;
    }
    let planned_qty = quantity * size_multiplier;
    let is_limit = prefer_limit != 0 || order_type == 1;
    if !out_qty.is_null() {
        *out_qty = planned_qty;
    }
    if !out_order_type.is_null() {
        *out_order_type = if is_limit { 1 } else { 0 };
    }
    if !out_has_limit.is_null() {
        *out_has_limit = if is_limit { 1 } else { 0 };
    }
    if !out_limit_price.is_null() {
        *out_limit_price = if is_limit { reference_price } else { 0.0 };
    }
    0
}

#[no_mangle]
pub unsafe extern "C" fn vy_exec_ledger_apply_fill(
    pos_qty: f64,
    pos_avg_price: f64,
    pos_realized_pnl: f64,
    side: i32, // 0 = BUY, 1 = SELL
    fill_qty: f64,
    fill_price: f64,
    commission: f64,
    out_qty: *mut f64,
    out_avg_price: *mut f64,
    out_realized_pnl: *mut f64,
    out_day_pnl_delta: *mut f64,
) -> i32 {
    use execution::Side;
    let side = if side == 0 { Side::Buy } else { Side::Sell };
    let signed = side.direction() * fill_qty;
    let new_qty = pos_qty + signed;

    let is_flat = pos_qty == 0.0;
    let same_sign = (pos_qty > 0.0) == (signed > 0.0);

    let (final_qty, final_avg, final_realized, day_pnl_delta) = if is_flat || same_sign {
        let total_cost = pos_avg_price * pos_qty.abs() + fill_price * fill_qty;
        let denom = new_qty.abs();
        let avg = if denom > 0.0 { total_cost / denom } else { 0.0 };
        (new_qty, avg, pos_realized_pnl, 0.0)
    } else {
        let closing = pos_qty.abs().min(fill_qty);
        let mut pnl = (fill_price - pos_avg_price)
            * closing
            * (if pos_qty > 0.0 { 1.0 } else { -1.0 });
        if fill_qty != 0.0 {
            pnl -= commission * (closing / fill_qty);
        }
        let realized = pos_realized_pnl + pnl;
        let avg = if new_qty.abs() > 0.0 { fill_price } else { 0.0 };
        (new_qty, avg, realized, pnl)
    };

    if !out_qty.is_null() {
        *out_qty = final_qty;
    }
    if !out_avg_price.is_null() {
        *out_avg_price = final_avg;
    }
    if !out_realized_pnl.is_null() {
        *out_realized_pnl = final_realized;
    }
    if !out_day_pnl_delta.is_null() {
        *out_day_pnl_delta = day_pnl_delta;
    }
    0
}
