//! VAYREN Rust-owned kernels — C ABI for the Python boundary.
//!
//! The Python layer (execution/backtest/market) consumes these symbols via
//! `ctypes`. This is the single authoritative implementation of the order
//! lifecycle table, backtest numeric kernels and timeframe aggregation; the
//! Python side holds no duplicate logic (AI_ENTRY.md §1).
//!
//! ABI rules: fixed-width integers, `f64` doubles, caller-allocated buffers,
//! no panics across the boundary (every entry point is guarded), unknown
//! inputs fail closed.

pub mod aggregate;
pub mod backtest;
pub mod backtest_engine;
pub mod backtest_events;
pub mod backtest_runner;
pub mod backtest_validation;
pub mod chart_events;
pub mod chart_math;
pub mod chart_model;
pub mod data_events;
pub mod download;
pub mod download_engine;
pub mod event_bus;
pub mod execution;
pub mod execution_engine;
pub mod execution_events;
pub mod indicator;
pub mod kill_switch;
pub mod live_readiness;
pub mod live_session;
pub mod market;
pub mod market_events;
pub mod metrics;
pub mod normalizer;
pub mod order_state;
pub mod pytext;
pub mod registry;
pub mod resilience;
pub mod risk;
pub mod risk_engine;
pub mod sandbox_policy;
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

// ── backtest form validation ──────────────────────────────────────────

/// Evaluate backtest form checks. Returns the failure bitmask (bit i =
/// `backtest_validation::MESSAGES[i]`); 0 = valid. Wrong-shape inputs fail
/// closed to all-set (every message); panics are caught.
#[no_mangle]
pub extern "C" fn vy_backtest_validate_form(
    symbol_ok: i32,
    strategy_ok: i32,
    timeframe_ok: i32,
    dates_ordered: i32,
    initial_capital: f64,
    has_cap: i32,
    max_position_size: f64,
) -> u32 {
    let result = std::panic::catch_unwind(|| {
        backtest_validation::validate_form(
            symbol_ok != 0,
            strategy_ok != 0,
            timeframe_ok != 0,
            dates_ordered != 0,
            initial_capital,
            if has_cap != 0 {
                Some(max_position_size)
            } else {
                None
            },
        )
    });
    result.unwrap_or(backtest_validation::ALL_BITS)
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
        let mut pnl =
            (fill_price - pos_avg_price) * closing * (if pos_qty > 0.0 { 1.0 } else { -1.0 });
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

#[no_mangle]
pub extern "C" fn vy_exec_verdict_blocks_live(status_code: i32) -> i32 {
    // 0 = SAFE, 1 = WARNING, 2 = BLOCKED
    let v = match status_code {
        0 => execution_engine::Verdict::Safe,
        1 => execution_engine::Verdict::Warning,
        _ => execution_engine::Verdict::Blocked,
    };
    if v.blocks_live() {
        1
    } else {
        0
    }
}

#[no_mangle]
pub unsafe extern "C" fn vy_exec_ledger_snapshot(
    starting_capital: f64,
    realized_sum: f64,
    day_pnl: f64,
    pos_count: i32,
    pos_qtys: *const f64,
    pos_avg_prices: *const f64,
    pos_mark_prices: *const f64,
    out_equity: *mut f64,
    out_available_capital: *mut f64,
    out_day_pnl: *mut f64,
) -> i32 {
    let count = if pos_count > 0 { pos_count as usize } else { 0 };
    let qtys = slice(pos_qtys, count);
    let avg_prices = slice(pos_avg_prices, count);
    let mark_prices = slice(pos_mark_prices, count);

    let mut unrealized = 0.0;
    for i in 0..count {
        unrealized += execution::position_unrealized(qtys[i], avg_prices[i], mark_prices[i]);
    }

    let equity = starting_capital + realized_sum + unrealized;
    if !out_equity.is_null() {
        *out_equity = equity;
    }
    if !out_available_capital.is_null() {
        *out_available_capital = equity;
    }
    if !out_day_pnl.is_null() {
        *out_day_pnl = day_pnl + unrealized;
    }
    0
}

/// Signed-quantity verdict of one position: 0 flat, 1 long, 2 short.
///
/// The Python ledger asks instead of deciding, so `flat` and the
/// `LONG`/`SHORT` label come from the same read of the sign.
#[no_mangle]
pub extern "C" fn vy_exec_position_state(quantity: f64) -> i32 {
    execution::position_state(quantity)
}

/// Mark-to-market P&L of one leg; returns 1 on success.
///
/// # Safety
/// `out_unrealized` must point to one writable `f64`.
#[no_mangle]
pub unsafe extern "C" fn vy_exec_position_unrealized(
    quantity: f64,
    avg_price: f64,
    mark_price: f64,
    out_unrealized: *mut f64,
) -> i32 {
    if out_unrealized.is_null() {
        return -1;
    }
    *out_unrealized = execution::position_unrealized(quantity, avg_price, mark_price);
    1
}

#[no_mangle]
pub unsafe extern "C" fn vy_exec_paper_calculate_fill(
    is_limit: i32,
    has_limit: i32,
    limit_price: f64,
    is_buy: i32,
    reference_price: f64,
    slippage_pct: f64,
    commission_pct: f64,
    capital: f64,
    remaining_qty: f64,
    out_fill_price: *mut f64,
    out_fill_qty: *mut f64,
    out_notional: *mut f64,
    out_commission: *mut f64,
    out_new_capital: *mut f64,
) -> i32 {
    if reference_price <= 0.0 || remaining_qty <= 0.0 {
        return -1;
    }
    let slip = reference_price * (slippage_pct / 100.0);
    let fill_price = if is_limit != 0 && has_limit != 0 {
        if is_buy != 0 {
            limit_price.min(reference_price + slip)
        } else {
            limit_price.max(reference_price - slip)
        }
    } else if is_buy != 0 {
        reference_price + slip
    } else {
        reference_price - slip
    };

    if fill_price <= 0.0 {
        return -1;
    }

    let fill_qty = if is_buy != 0 {
        let affordable = if fill_price > 0.0 {
            capital / fill_price
        } else {
            0.0
        };
        remaining_qty.min(affordable)
    } else {
        remaining_qty
    };

    if fill_qty <= 0.0 {
        return -1;
    }

    let notional = fill_price * fill_qty;
    let commission = notional * (commission_pct / 100.0);
    let new_capital = if is_buy != 0 {
        capital - (notional + commission)
    } else {
        capital + (notional - commission)
    };

    if !out_fill_price.is_null() {
        *out_fill_price = fill_price;
    }
    if !out_fill_qty.is_null() {
        *out_fill_qty = fill_qty;
    }
    if !out_notional.is_null() {
        *out_notional = notional;
    }
    if !out_commission.is_null() {
        *out_commission = commission;
    }
    if !out_new_capital.is_null() {
        *out_new_capital = new_capital;
    }

    0
}

#[no_mangle]
pub unsafe extern "C" fn vy_exec_order_apply_fill(
    prev_qty: f64,
    prev_avg: f64,
    fill_qty: f64,
    fill_price: f64,
    out_new_qty: *mut f64,
    out_new_avg: *mut f64,
) -> i32 {
    let new_qty = prev_qty + fill_qty;
    let new_avg = if prev_qty <= 0.0 {
        fill_price
    } else if new_qty > 0.0 {
        (prev_avg * prev_qty + fill_price * fill_qty) / new_qty
    } else {
        0.0
    };
    if !out_new_qty.is_null() {
        *out_new_qty = new_qty;
    }
    if !out_new_avg.is_null() {
        *out_new_avg = new_avg;
    }
    0
}

#[no_mangle]
pub extern "C" fn vy_exec_check_live_readiness_basic(
    warmup_have: i32,
    warmup_need: i32,
    risk_ok: i32,
    account_ok: i32,
    clock_ok: i32,
    reconcile_ok: i32,
    persistence_ok: i32,
    kill_ok: i32,
    observability_ok: i32,
) -> i32 {
    let mut ok = 1;
    if warmup_have < warmup_need {
        ok = 0;
    }
    if risk_ok == 0 {
        ok = 0;
    }
    if account_ok == 0 {
        ok = 0;
    }
    if clock_ok == 0 {
        ok = 0;
    }
    if reconcile_ok == 0 {
        ok = 0;
    }
    if persistence_ok == 0 {
        ok = 0;
    }
    if kill_ok == 0 {
        ok = 0;
    }
    if observability_ok == 0 {
        ok = 0;
    }
    ok
}

/// Rank percentile of an ascending sample set; 0.0 for an empty set. Returns
/// 1 when `out` was written, -2 for bridge misuse.
///
/// # Safety
/// `values` must be null or point to `n` readable doubles; `out` must be null
/// or point to one writable `f64`.
#[no_mangle]
pub unsafe extern "C" fn vy_exec_percentile(
    values: *const f64,
    n: usize,
    pct: f64,
    out: *mut f64,
) -> i32 {
    if out.is_null() {
        return -2;
    }
    *out = execution_engine::percentile(slice(values, n), pct);
    1
}

// ── live safety gates ─────────────────────────────────────────────────────
//
// The five mandatory gates, their vocabulary, the fail-closed `"true"` flag
// grammar and the LIVE→PAPER degradation rule all live in
// `execution_engine::modes`. Python hands over flags and reads back verdicts.

/// Gate mask from five NUL-separated flag values, in gate order (an empty
/// value is an absent flag). Returns the mask (bit 0 = live trading switch),
/// or -1 when the blob is not five decodable values or undecodable. A NUL
/// separator is what keeps a value that contains a newline from shifting the
/// gate order — no environment value can ever contain one.
///
/// # Safety
/// `values` must point to `values_len` readable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_exec_gates_mask(values: *const u8, values_len: i64) -> i64 {
    let blob = match rs_text(values, values_len) {
        Ok(text) => text,
        Err(_) => return -1,
    };
    let flags: Vec<&str> = blob.split('\0').collect();
    match execution_engine::flags_to_mask(&flags) {
        Some(mask) => mask as i64,
        None => -1,
    }
}

/// Comma-joined names of the gates a mask does not satisfy, in gate order;
/// returns the byte length needed (0 when every gate is on).
///
/// # Safety
/// `buf` must be null or point to `cap` writable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_exec_missing_gates(mask: u32, buf: *mut u8, cap: usize) -> i32 {
    ks_write(&execution_engine::missing_gates(mask).join(","), buf, cap)
}

/// Effective-mode verdict for a requested-mode label plus the gate mask,
/// written as `"MODE"` or `"MODE\nreason…"` (one reason per line); returns the
/// byte length needed. -1 when the label is not a mode, -2 for a bad pointer.
///
/// # Safety
/// `requested` must point to its byte length; `buf` must be null or writable.
#[no_mangle]
pub unsafe extern "C" fn vy_exec_resolve_mode(
    requested: *const u8,
    requested_len: i64,
    mask: u32,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    let label = match rs_text(requested, requested_len) {
        Ok(text) => text,
        Err(code) => return code,
    };
    match execution_engine::resolve_mode_mask(label, mask) {
        Some((mode, reasons)) => {
            let mut doc = String::from(mode.as_str());
            for reason in reasons {
                doc.push('\n');
                doc.push_str(&reason);
            }
            ks_write(&doc, buf, cap)
        }
        None => -1,
    }
}

// ── reconciliation reports ────────────────────────────────────────────────
//
// The tolerance compare, the sorted symbol union, the parse-or-zero broker
// quantity rule, the `local-only`/`broker-only` labelling and the
// SAFE/WARNING/BLOCKED combination all live in `execution_engine::reconcile_*`
// / `verdict_status`. Python hands over payload text and reads back a
// `<count>\n` + four-NUL-fields-per-mismatch document.
//
// Field lists travel trailing-NUL terminated so an empty field stays
// distinguishable from an empty list; a negative length means `None`.

/// NUL-terminated field list, or `None` for a negative length.
///
/// # Safety
/// `ptr` must point to `len` readable bytes when `len >= 0`.
unsafe fn rs_fields<'a>(ptr: *const u8, len: i64) -> Result<Option<Vec<&'a str>>, i32> {
    let Some(text) = rs_bound(ptr, len)? else {
        return Ok(None);
    };
    if text.is_empty() {
        return Ok(Some(Vec::new()));
    }
    let Some(body) = text.strip_suffix('\0') else {
        return Err(-2);
    };
    Ok(Some(body.split('\0').collect()))
}

/// Field list re-paired two by two; an odd field count is a contract break.
///
/// # Safety
/// Same contract as [`rs_fields`].
unsafe fn rs_pairs<'a>(ptr: *const u8, len: i64) -> Result<Option<Vec<(&'a str, &'a str)>>, i32> {
    match rs_fields(ptr, len)? {
        None => Ok(None),
        Some(fields) if fields.len() % 2 != 0 => Err(-2),
        Some(fields) => {
            let mut pairs = Vec::with_capacity(fields.len() / 2);
            let mut index = 0;
            while index < fields.len() {
                pairs.push((fields[index], fields[index + 1]));
                index += 2;
            }
            Ok(Some(pairs))
        }
    }
}

/// Position report from local `(symbol, quantity)` and broker
/// `(symbol, raw quantity)` pair blobs. A broker quantity that is not a
/// number counts as zero — the parse-or-zero rule is decided here.
///
/// # Safety
/// Pair blobs must point to their lengths; `buf` null or writable capacity.
#[no_mangle]
pub unsafe extern "C" fn vy_exec_reconcile_positions(
    local: *const u8,
    local_len: i64,
    broker: *const u8,
    broker_len: i64,
    tolerance: f64,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    let local_pairs = match rs_pairs(local, local_len) {
        Ok(value) => value.unwrap_or_default(),
        Err(code) => return code,
    };
    let broker_pairs = match rs_pairs(broker, broker_len) {
        Ok(value) => value.unwrap_or_default(),
        Err(code) => return code,
    };
    let mut local_qty = Vec::with_capacity(local_pairs.len());
    for (symbol, quantity) in local_pairs {
        match quantity.parse::<f64>() {
            Ok(value) => local_qty.push((symbol.to_string(), value)),
            Err(_) => return -2,
        }
    }
    let broker_qty: Vec<(String, String)> = broker_pairs
        .into_iter()
        .map(|(symbol, quantity)| (symbol.to_string(), quantity.to_string()))
        .collect();
    let report = execution_engine::reconcile_positions(&local_qty, &broker_qty, tolerance, "");
    ks_write(&execution_engine::report_doc(&report), buf, cap)
}

/// Open-order report from two trailing-NUL terminated id lists.
///
/// # Safety
/// Same contract as [`rs_fields`].
#[no_mangle]
pub unsafe extern "C" fn vy_exec_reconcile_orders(
    local: *const u8,
    local_len: i64,
    broker: *const u8,
    broker_len: i64,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    let local_ids = match rs_fields(local, local_len) {
        Ok(value) => value.unwrap_or_default(),
        Err(code) => return code,
    };
    let broker_ids = match rs_fields(broker, broker_len) {
        Ok(value) => value.unwrap_or_default(),
        Err(code) => return code,
    };
    let local_owned: Vec<String> = local_ids.iter().map(|id| (*id).to_string()).collect();
    let broker_owned: Vec<String> = broker_ids.iter().map(|id| (*id).to_string()).collect();
    let report = execution_engine::reconcile_orders(&local_owned, &broker_owned, "");
    ks_write(&execution_engine::report_doc(&report), buf, cap)
}

/// Funds report. `raw` is the broker equity payload value as text; a negative
/// length (or text that will not parse) means the snapshot has no usable
/// equity, which is a mismatch — never a silent zero.
///
/// # Safety
/// `raw` must point to its length when non-negative; `buf` null or writable.
#[no_mangle]
pub unsafe extern "C" fn vy_exec_reconcile_funds(
    local_equity: f64,
    raw: *const u8,
    raw_len: i64,
    tolerance: f64,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    let raw_text = match rs_bound(raw, raw_len) {
        Ok(value) => value,
        Err(code) => return code,
    };
    let broker_equity = raw_text.and_then(|text| text.parse::<f64>().ok());
    let report = execution_engine::reconcile_funds(local_equity, broker_equity, tolerance, "");
    ks_write(&execution_engine::report_doc(&report), buf, cap)
}

/// Combined verdict status from the evaluation flag plus the total mismatch
/// count across reports: 0 SAFE, 1 WARNING, 2 BLOCKED.
#[no_mangle]
pub extern "C" fn vy_exec_verdict_status(evaluated: i32, mismatch_total: i64) -> i32 {
    execution_engine::verdict_status(evaluated != 0, mismatch_total) as i32
}

/// Report-level blocking rule for a `matched` flag: 1 blocks, 0 does not.
#[no_mangle]
pub extern "C" fn vy_exec_report_blocks_live(matched: i32) -> i32 {
    i32::from(execution_engine::report_blocks_live(matched != 0))
}

// ── kill switch ───────────────────────────────────────────────────────────
//
// Handle-based: the Python wrapper holds a `Path` plus the bytes on either
// side of `read_text`/`write_text`. Latch state, the level vocabulary,
// timestamps, the reload rule and the persisted document shape stay in Rust.
//
// String contract: inputs are `(ptr, len)` UTF-8; outputs are written into a
// caller buffer and the call returns the byte length required (excluding the
// NUL). Nothing is written when the capacity is too small, so callers use the
// two-call pattern (`len` probe, then a buffer of `len + 1`).
//
// Return codes: `0` success, `>=1` kernel result or byte length,
// `-1` rejection (message via `vy_ks_last_message`), `-2` bridge misuse
// (bad handle, undecodable argument).

use kill_switch::{KillSwitch, KillSwitchField, KillSwitchLevel, LoadOutcome};
use std::sync::{Mutex, MutexGuard};

struct KsSlot {
    switch: KillSwitch,
    message: Option<String>,
}

/// Slot 0 backs handle 1, so a freed/never-issued handle can't alias a live
/// switch. Freed slots are recycled.
static KS_SLOTS: Mutex<Vec<Option<KsSlot>>> = Mutex::new(Vec::new());

fn ks_slots() -> MutexGuard<'static, Vec<Option<KsSlot>>> {
    KS_SLOTS
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn ks_index(handle: i64) -> Option<usize> {
    usize::try_from(handle.checked_sub(1)?).ok()
}

unsafe fn ks_arg<'a>(ptr: *const u8, len: usize) -> Option<&'a str> {
    std::str::from_utf8(slice(ptr, len)).ok()
}

unsafe fn ks_write(text: &str, buf: *mut u8, cap: usize) -> i32 {
    let bytes = text.as_bytes();
    if !buf.is_null() && cap > bytes.len() {
        std::ptr::copy_nonoverlapping(bytes.as_ptr(), buf, bytes.len());
        *buf.add(bytes.len()) = 0;
    }
    bytes.len() as i32
}

#[no_mangle]
pub extern "C" fn vy_ks_new() -> i64 {
    let mut slots = ks_slots();
    let slot = Some(KsSlot {
        switch: KillSwitch::in_memory(),
        message: None,
    });
    if let Some(free) = slots.iter().position(Option::is_none) {
        slots[free] = slot;
        return free as i64 + 1;
    }
    slots.push(slot);
    slots.len() as i64
}

#[no_mangle]
pub extern "C" fn vy_ks_free(handle: i64) -> i32 {
    let Some(index) = ks_index(handle) else {
        return -2;
    };
    let mut slots = ks_slots();
    let freed = slots
        .get_mut(index)
        .is_some_and(|entry| entry.take().is_some());
    if freed {
        0
    } else {
        -2
    }
}

#[no_mangle]
pub extern "C" fn vy_ks_level_count() -> i32 {
    KillSwitchLevel::ALL.len() as i32
}

#[no_mangle]
pub unsafe extern "C" fn vy_ks_level_name(index: i32, buf: *mut u8, cap: usize) -> i32 {
    let Ok(index) = usize::try_from(index) else {
        return -2;
    };
    match KillSwitchLevel::ALL.get(index) {
        Some(level) => ks_write(level.as_str(), buf, cap),
        None => -2,
    }
}

#[no_mangle]
pub unsafe extern "C" fn vy_ks_engage(
    handle: i64,
    reason: *const u8,
    reason_len: usize,
    level: *const u8,
    level_len: usize,
) -> i32 {
    let Some(index) = ks_index(handle) else {
        return -2;
    };
    let (Some(reason_text), Some(level_text)) =
        (ks_arg(reason, reason_len), ks_arg(level, level_len))
    else {
        return -2;
    };
    let mut slots = ks_slots();
    let Some(slot) = slots.get_mut(index).and_then(Option::as_mut) else {
        return -2;
    };
    slot.message = None;
    match slot.switch.engage(reason_text, level_text) {
        Ok(()) => 0,
        Err(err) => {
            slot.message = Some(err);
            -1
        }
    }
}

#[no_mangle]
pub unsafe extern "C" fn vy_ks_disengage(handle: i64, level: *const u8, level_len: usize) -> i32 {
    let Some(index) = ks_index(handle) else {
        return -2;
    };
    let Some(level_text) = ks_arg(level, level_len) else {
        return -2;
    };
    let mut slots = ks_slots();
    let Some(slot) = slots.get_mut(index).and_then(Option::as_mut) else {
        return -2;
    };
    slot.message = None;
    match slot.switch.disengage(level_text) {
        Ok(()) => 0,
        Err(err) => {
            slot.message = Some(err);
            -1
        }
    }
}

#[no_mangle]
pub unsafe extern "C" fn vy_ks_is_halted(handle: i64, level: *const u8, level_len: usize) -> i32 {
    ks_probe(handle, level, level_len, KillSwitch::is_halted)
}

#[no_mangle]
pub unsafe extern "C" fn vy_ks_state_engaged(
    handle: i64,
    level: *const u8,
    level_len: usize,
) -> i32 {
    ks_probe(handle, level, level_len, KillSwitch::state_engaged)
}

fn ks_probe(
    handle: i64,
    level: *const u8,
    level_len: usize,
    read: impl Fn(&KillSwitch, &str) -> Result<bool, String>,
) -> i32 {
    let index = match ks_index(handle) {
        Some(index) => index,
        None => return -2,
    };
    let level_text = match unsafe { ks_arg(level, level_len) } {
        Some(text) => text,
        None => return -2,
    };
    let mut slots = ks_slots();
    let Some(slot) = slots.get_mut(index).and_then(Option::as_mut) else {
        return -2;
    };
    slot.message = None;
    match read(&slot.switch, level_text) {
        Ok(value) => i32::from(value),
        Err(err) => {
            slot.message = Some(err);
            -1
        }
    }
}

#[no_mangle]
pub unsafe extern "C" fn vy_ks_state_reason(
    handle: i64,
    level: *const u8,
    level_len: usize,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    ks_read_field(handle, level, level_len, KillSwitchField::Reason, buf, cap)
}

#[no_mangle]
pub unsafe extern "C" fn vy_ks_state_engaged_at(
    handle: i64,
    level: *const u8,
    level_len: usize,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    ks_read_field(
        handle,
        level,
        level_len,
        KillSwitchField::EngagedAt,
        buf,
        cap,
    )
}

unsafe fn ks_read_field(
    handle: i64,
    level: *const u8,
    level_len: usize,
    field: KillSwitchField,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    let index = match ks_index(handle) {
        Some(index) => index,
        None => return -2,
    };
    let level_text = match ks_arg(level, level_len) {
        Some(text) => text,
        None => return -2,
    };
    let mut slots = ks_slots();
    let Some(slot) = slots.get_mut(index).and_then(Option::as_mut) else {
        return -2;
    };
    slot.message = None;
    match slot.switch.field(level_text, field) {
        Ok(value) => ks_write(&value, buf, cap),
        Err(err) => {
            slot.message = Some(err);
            -1
        }
    }
}

#[no_mangle]
pub unsafe extern "C" fn vy_ks_serialize(handle: i64, buf: *mut u8, cap: usize) -> i32 {
    let Some(index) = ks_index(handle) else {
        return -2;
    };
    let mut slots = ks_slots();
    let Some(slot) = slots.get_mut(index).and_then(Option::as_mut) else {
        return -2;
    };
    slot.message = None;
    ks_write(&slot.switch.serialize(), buf, cap)
}

/// Feed persisted text back in: `0` applied, `1` unparseable (historically
/// ignored), `-1` un-walkable document (message available, partial levels
/// already applied just as the Python loop did).
#[no_mangle]
pub unsafe extern "C" fn vy_ks_load(handle: i64, text: *const u8, text_len: usize) -> i32 {
    let Some(index) = ks_index(handle) else {
        return -2;
    };
    let Some(text) = ks_arg(text, text_len) else {
        return -2;
    };
    let mut slots = ks_slots();
    let Some(slot) = slots.get_mut(index).and_then(Option::as_mut) else {
        return -2;
    };
    slot.message = None;
    match slot.switch.apply_saved(text) {
        Ok(LoadOutcome::Applied) => 0,
        Ok(LoadOutcome::Garbage) => 1,
        Err(err) => {
            slot.message = Some(err);
            -1
        }
    }
}

#[no_mangle]
pub unsafe extern "C" fn vy_ks_last_message(handle: i64, buf: *mut u8, cap: usize) -> i32 {
    let Some(index) = ks_index(handle) else {
        return -2;
    };
    let mut slots = ks_slots();
    let Some(slot) = slots.get_mut(index).and_then(Option::as_mut) else {
        return -2;
    };
    ks_write(slot.message.as_deref().unwrap_or(""), buf, cap)
}

// ── timeframe ladder ──────────────────────────────────────────────────────
//
// `market::TIMEFRAME_LADDER` is the single authority for the candidate
// granularities; these exports are the only way Python reaches it.

/// Write `text` as UTF-8 plus a NUL terminator when `cap` has room for both;
/// always return the byte length needed.
///
/// # Safety
/// `buf` must be null or point to `cap` writable bytes.
unsafe fn tf_write(text: &str, buf: *mut u8, cap: usize) -> i32 {
    let bytes = text.as_bytes();
    if !buf.is_null() && cap > bytes.len() {
        std::ptr::copy_nonoverlapping(bytes.as_ptr(), buf, bytes.len());
        *buf.add(bytes.len()) = 0;
    }
    bytes.len() as i32
}

/// Number of ladder entries.
#[no_mangle]
pub extern "C" fn vy_timeframe_ladder_count() -> i32 {
    market::TIMEFRAME_LADDER.len() as i32
}

/// Ladder granularity at `index`, or -1 when the index is out of range.
#[no_mangle]
pub extern "C" fn vy_timeframe_ladder_seconds(index: i32) -> i64 {
    market::TIMEFRAME_LADDER
        .get(usize::try_from(index).unwrap_or(usize::MAX))
        .map_or(-1, |(_, seconds)| *seconds)
}

/// Ladder label at `index`; returns the needed byte length, or -1 when the
/// index is out of range.
#[no_mangle]
pub unsafe extern "C" fn vy_timeframe_ladder_label(index: i32, buf: *mut u8, cap: usize) -> i32 {
    match market::TIMEFRAME_LADDER.get(usize::try_from(index).unwrap_or(usize::MAX)) {
        Some((label, _)) => tf_write(label, buf, cap),
        None => -1,
    }
}

/// Seconds for a timeframe label (ladder or generated). 0 means the label is
/// invalid — every real granularity is a positive number of seconds. Malformed
/// (non-UTF-8) input returns -1, which the bridge treats as misuse.
#[no_mangle]
pub unsafe extern "C" fn vy_timeframe_seconds(name: *const u8, name_len: usize) -> i64 {
    match std::str::from_utf8(slice(name, name_len)).ok() {
        Some(text) => market::timeframe_seconds(text).unwrap_or(0),
        None => -1,
    }
}

/// Ladder label for a granularity; returns the needed byte length, or 0 when
/// the granularity is not in the ladder.
#[no_mangle]
pub unsafe extern "C" fn vy_timeframe_label(seconds: i64, buf: *mut u8, cap: usize) -> i32 {
    match market::timeframe_name(seconds) {
        Some(label) => tf_write(label, buf, cap),
        None => 0,
    }
}

/// Human label for an arbitrary granularity.
#[no_mangle]
pub unsafe extern "C" fn vy_timeframe_generate_label(
    seconds: i64,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    tf_write(&market::generate_label(seconds), buf, cap)
}

/// Comma-joined timeframes producible from a detected base bar duration;
/// returns the needed byte length (0 when no base duration is valid).
#[no_mangle]
pub unsafe extern "C" fn vy_timeframe_available(
    base_seconds: i64,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    tf_write(
        &market::available_timeframes(base_seconds).join(","),
        buf,
        cap,
    )
}

/// Higher-timeframe query plan for the candle repository: resolves `label`
/// to a granularity and plans the read in one step. Writes
/// `[plain, seconds, row_budget, keep_last]` into `out` (4 slots; a `-1` word
/// means "none": read the stored rows / whole history / keep everything;
/// `seconds` is the resolved granularity, `0` for an unknown label).
/// `base` and `limit` each pass `-1` for Python's `None`.
/// Returns 1 on success, -2 for bridge misuse.
///
/// # Safety
/// `label` must point to `label_len` readable bytes; `out` must be null or
/// point to 4 writable, aligned `i64` slots.
#[no_mangle]
pub unsafe extern "C" fn vy_timeframe_fetch_plan(
    label: *const u8,
    label_len: i64,
    base: i64,
    limit: i64,
    out: *mut i64,
) -> i32 {
    let slots = slice_mut(out, 4);
    if slots.len() < 4 {
        return -2;
    }
    let label = match rs_text(label, label_len) {
        Ok(text) => text,
        Err(_) => return -2,
    };
    let absent = |value: i64| if value < 0 { None } else { Some(value) };
    let seconds = market::timeframe_seconds(label);
    let plan =
        market::timeframe_fetch_plan(seconds, absent(base), absent(limit).map(|v| v as usize));
    slots[0] = i64::from(plan.plain);
    slots[1] = seconds.unwrap_or(0);
    slots[2] = plan.row_budget.map_or(-1, |v| v as i64);
    slots[3] = plan.keep_last.map_or(-1, |v| v as i64);
    1
}

// ── backtest position kernel ──────────────────────────────────────────────

/// Fill `out` with the priced close; 1 on success, -2 on bridge misuse.
///
/// # Safety
/// `out` must be null or point to one writable `TradeOut`.
unsafe fn bt_write_close(
    out: *mut backtest::TradeOut,
    side: backtest::Side,
    entry_price: f64,
    quantity: f64,
    commission_entry: f64,
    exit_price: f64,
    commission_pct: f64,
    stop: Option<f64>,
    reason: backtest::ExitReason,
) -> i32 {
    if out.is_null() {
        return -2;
    }
    let economics = backtest::close_economics(
        side,
        entry_price,
        quantity,
        commission_entry,
        exit_price,
        commission_pct,
        stop,
    );
    out.write(backtest::TradeOut::from_economics(
        exit_price, reason, &economics,
    ));
    1
}

/// Decide the exit one bar triggers and price the closed trade in one call.
///
/// Returns 1 when the leg closes (`out` filled), 0 while the position stays
/// open, -1 for an unknown side code, -2 for bridge misuse.
///
/// # Safety
/// `out` must be null or point to one writable `TradeOut`.
#[no_mangle]
pub unsafe extern "C" fn vy_bt_try_close(
    side: i32,
    sl_defined: i32,
    sl_price: f64,
    tp_defined: i32,
    tp_price: f64,
    bar_high: f64,
    bar_low: f64,
    bar_close: f64,
    entry_price: f64,
    quantity: f64,
    commission_entry: f64,
    commission_pct: f64,
    exit_signal: i32,
    out: *mut backtest::TradeOut,
) -> i32 {
    let Some(side) = backtest::Side::from_code(side) else {
        return -1;
    };
    let stop = (sl_defined != 0).then_some(sl_price);
    let Some((exit_price, reason)) = backtest::exit_for_bar(
        side,
        stop,
        (tp_defined != 0).then_some(tp_price),
        bar_high,
        bar_low,
        bar_close,
        exit_signal != 0,
    ) else {
        return 0;
    };
    bt_write_close(
        out,
        side,
        entry_price,
        quantity,
        commission_entry,
        exit_price,
        commission_pct,
        stop,
        reason,
    )
}

/// Price an explicitly requested close (signal or end-of-run). `reason_code`
/// is 1 SIGNAL, 2 SL, 3 TP or 4 END. Same return codes as
/// [`vy_bt_try_close`].
///
/// # Safety
/// `out` must be null or point to one writable `TradeOut`.
#[no_mangle]
pub unsafe extern "C" fn vy_bt_close_trade(
    side: i32,
    reason_code: i32,
    entry_price: f64,
    quantity: f64,
    commission_entry: f64,
    exit_price: f64,
    commission_pct: f64,
    sl_defined: i32,
    sl_price: f64,
    out: *mut backtest::TradeOut,
) -> i32 {
    let (Some(side), Some(reason)) = (
        backtest::Side::from_code(side),
        backtest::ExitReason::from_code(reason_code),
    ) else {
        return -1;
    };
    bt_write_close(
        out,
        side,
        entry_price,
        quantity,
        commission_entry,
        exit_price,
        commission_pct,
        (sl_defined != 0).then_some(sl_price),
        reason,
    )
}

/// Price one entry fill. `side` is the raw signal text; 1 when the entry is
/// affordable (`out` filled), 0 when the kernel rejects it, -1 for
/// undecodable text, -2 for bridge misuse.
///
/// # Safety
/// `side` must be null or point to `side_len` readable bytes; `out` must be
/// null or point to one writable `FillOut`.
#[no_mangle]
pub unsafe extern "C" fn vy_bt_fill(
    side: *const u8,
    side_len: i64,
    bar_close: f64,
    available_equity: f64,
    slippage_pct: f64,
    commission_pct: f64,
    out: *mut backtest::FillOut,
) -> i32 {
    let side = match rs_text(side, side_len) {
        Ok(text) => text,
        Err(code) => return code,
    };
    let Some(fill) = backtest::fill_entry(
        side,
        bar_close,
        available_equity,
        slippage_pct,
        commission_pct,
    ) else {
        return 0;
    };
    if out.is_null() {
        return -2;
    }
    out.write(fill);
    1
}

/// Decide what a strategy signal does to the open leg. Returns 1 when the
/// opposite signal closes it (`out` holds the slipped exit price), 0 while the
/// leg holds, -1 for an unknown side code, -2 for bridge misuse.
///
/// # Safety
/// `out` must be null or point to one writable `f64`.
#[no_mangle]
pub unsafe extern "C" fn vy_bt_signal_exit(
    side: i32,
    is_buy: i32,
    bar_close: f64,
    slippage_pct: f64,
    out: *mut f64,
) -> i32 {
    let Some(side) = backtest::Side::from_code(side) else {
        return -1;
    };
    if out.is_null() {
        return -2;
    }
    if !backtest::closes_on_signal(side, is_buy != 0) {
        return 0;
    }
    *out = backtest::slipped_exit_price(side, bar_close, slippage_pct);
    1
}

/// Price the end-of-run close of the open leg. Returns 1 (`out` filled),
/// -1 for an unknown side code, -2 for bridge misuse.
///
/// # Safety
/// `out` must be null or point to one writable `f64`.
#[no_mangle]
pub unsafe extern "C" fn vy_bt_exit_price(
    side: i32,
    bar_close: f64,
    slippage_pct: f64,
    out: *mut f64,
) -> i32 {
    let Some(side) = backtest::Side::from_code(side) else {
        return -1;
    };
    if out.is_null() {
        return -2;
    }
    *out = backtest::slipped_exit_price(side, bar_close, slippage_pct);
    1
}

/// Assemble the display metrics: the whole aggregate rule (gross profit/loss,
/// win rate, profit factor, average, expectancy, Sharpe, drawdown read off
/// `equities`) is decided in `backtest_engine::report_for_curve`. Returns 1
/// when `out` was written, -2 for bridge misuse.
///
/// # Safety
/// Pointers must be null or point to their (non-negative) element counts;
/// `out` must be null or point to one writable `ReportOut`.
#[no_mangle]
pub unsafe extern "C" fn vy_bt_report(
    pnls: *const f64,
    pnls_len: usize,
    bars_held: *const f64,
    bars_len: usize,
    equities: *const f64,
    equities_len: usize,
    initial: f64,
    out: *mut backtest_engine::ReportOut,
) -> i32 {
    if out.is_null() {
        return -2;
    }
    out.write(backtest_engine::ReportOut::from_report(
        &backtest_engine::report_for_curve(
            slice(pnls, pnls_len),
            slice(bars_held, bars_len),
            slice(equities, equities_len),
            initial,
        ),
    ));
    1
}

/// Replay window rule: which bars fall inside the `[start, end]` date range?
/// `timestamps` carries the caller's bar timestamps joined by `\n` (a
/// timestamp never contains one); the kept indices are written to `out` in
/// ascending order. Returns the count written, -1 when `cap` is too small or
/// a bound is undecodable UTF-8, -2 when the timestamp pointer is null.
///
/// # Safety
/// Text pointers must be null or point to their (non-negative) byte lengths;
/// `out` must be null or point to `cap` writable `u32` slots.
#[no_mangle]
#[allow(clippy::too_many_arguments)]
pub unsafe extern "C" fn vy_bt_slice(
    timestamps: *const u8,
    timestamps_len: i64,
    start: *const u8,
    start_len: i64,
    end: *const u8,
    end_len: i64,
    out: *mut u32,
    cap: usize,
) -> i32 {
    let blob = match rs_bound(timestamps, timestamps_len) {
        Ok(Some(text)) => text,
        Ok(None) => return -2,
        Err(code) => return code,
    };
    let start = match rs_bound(start, start_len) {
        Ok(bound) => bound,
        Err(code) => return code,
    };
    let end = match rs_bound(end, end_len) {
        Ok(bound) => bound,
        Err(code) => return code,
    };
    let stamps: Vec<&str> = if blob.is_empty() {
        Vec::new()
    } else {
        blob.split('\n').collect()
    };
    if out.is_null() {
        return -2;
    }
    let picked = backtest_engine::slice_indices(&stamps, start, end);
    if picked.len() > cap {
        return -1;
    }
    let written = picked.len();
    for (slot, index) in slice_mut(out, cap).iter_mut().zip(picked) {
        *slot = index as u32;
    }
    written as i32
}

/// Aggregation-window bounds for a `[start, end]` date range, written as
/// `"lower\nupper"` into `buf` (two-call convention: a null or too-small
/// buffer only reports the byte length). Returns -1 when either date is not a
/// real `YYYY-MM-DD` calendar day, -2 for bridge misuse.
///
/// # Safety
/// `start`/`end` must point to their (non-negative) byte lengths; `buf` must
/// be null or point to `cap` writable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_bt_window_bounds(
    start: *const u8,
    start_len: i64,
    end: *const u8,
    end_len: i64,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    let start = match rs_text(start, start_len) {
        Ok(text) => text,
        Err(code) => return code,
    };
    let end = match rs_text(end, end_len) {
        Ok(text) => text,
        Err(code) => return code,
    };
    match backtest_runner::window_bounds(start, end) {
        Some((lower, upper)) => ks_write(&format!("{lower}\n{upper}"), buf, cap),
        None => -1,
    }
}

/// Bounded batch worker count for a machine with `cpu_count` cpus (0 means
/// "unknown", which the kernel resolves like Python's `or 4`).
#[no_mangle]
pub extern "C" fn vy_bt_default_workers(cpu_count: i64) -> i64 {
    backtest_runner::default_workers(cpu_count.max(0) as usize) as i64
}

// ── risk session window ───────────────────────────────────────────────────
//
// `risk_engine::within_session` / `clock_sane` are the single authority for
// the trading-hours and clock-anomaly gates; these exports are the only way
// Python reaches them.

/// `(ptr, len)` text argument where a negative length means Python's `None`.
///
/// `Err(-1)` = undecodable bytes, `Err(-2)` = pointer/length contract break.
///
/// # Safety
/// `ptr` must be null or point to `len` readable bytes when `len >= 0`.
unsafe fn rs_bound<'a>(ptr: *const u8, len: i64) -> Result<Option<&'a str>, i32> {
    if len < 0 {
        return Ok(None);
    }
    if ptr.is_null() {
        return Err(-2);
    }
    ks_arg(ptr, len as usize).map(Some).ok_or(-1)
}

/// Trading-hours gate: is `timestamp` inside the inclusive `HH:MM` window
/// `[start, end]`? A negative bound length means that bound is `None`, and
/// `None`/`None` disables the gate (both return `1`).
///
/// # Safety
/// `timestamp` must point to `timestamp_len` readable bytes; `start`/`end`
/// must be null or point to their (non-negative) lengths.
#[no_mangle]
pub unsafe extern "C" fn vy_risk_within_session(
    timestamp: *const u8,
    timestamp_len: i64,
    start: *const u8,
    start_len: i64,
    end: *const u8,
    end_len: i64,
) -> i32 {
    let text = match rs_bound(timestamp, timestamp_len) {
        Ok(Some(text)) => text,
        Ok(None) => return -2,
        Err(code) => return code,
    };
    let start = match rs_bound(start, start_len) {
        Ok(bound) => bound,
        Err(code) => return code,
    };
    let end = match rs_bound(end, end_len) {
        Ok(bound) => bound,
        Err(code) => return code,
    };
    i32::from(risk_engine::within_session(text, start, end))
}

/// Clock-anomaly gate: is `timestamp` no more than `max_future_skew_seconds`
/// ahead of `now_epoch`? Unparseable timestamps fail closed (`0`).
///
/// # Safety
/// `timestamp` must point to `timestamp_len` readable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_risk_clock_sane(
    timestamp: *const u8,
    timestamp_len: i64,
    now_epoch: f64,
    max_future_skew_seconds: f64,
) -> i32 {
    let text = match rs_bound(timestamp, timestamp_len) {
        Ok(Some(text)) => text,
        Ok(None) => return -2,
        Err(code) => return code,
    };
    i32::from(risk_engine::clock_sane(
        text,
        now_epoch,
        max_future_skew_seconds,
    ))
}

// ── risk engine (handle-based) ────────────────────────────────────────────
//
// `risk_engine::RiskEngine` is the sole authority for the decision checklist:
// the 18 named gates, their order, their verdicts, every reason string and the
// duplicate-intent memory. Python packs a policy plus a request and reads back
// one JSON document — no rule is evaluated on that side.

struct RiskSlot {
    engine: risk_engine::RiskEngine,
    decision: Option<risk_engine::RiskDecision>,
}

/// Slot 0 backs handle 1, so a freed/never-issued handle can't alias a live
/// engine. Freed slots are recycled.
static RISK_SLOTS: Mutex<Vec<Option<RiskSlot>>> = Mutex::new(Vec::new());

fn risk_slots() -> MutexGuard<'static, Vec<Option<RiskSlot>>> {
    RISK_SLOTS
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn risk_index(handle: i64) -> Option<usize> {
    usize::try_from(handle.checked_sub(1)?).ok()
}

/// Required `(ptr, len)` text: `Err(-2)` when the negative-length `None`
/// sentinel is used where a value is mandatory.
///
/// # Safety
/// `ptr` must be null or point to `len` readable bytes.
unsafe fn rs_text<'a>(ptr: *const u8, len: i64) -> Result<&'a str, i32> {
    match rs_bound(ptr, len)? {
        Some(text) => Ok(text),
        None => Err(-2),
    }
}

fn rs_opt(defined: i32, value: f64) -> Option<f64> {
    (defined != 0).then_some(value)
}

/// Build an engine for one policy. Returns the handle, or `0` when a text
/// argument is undecodable — the bridge raises and nothing is evaluated.
///
/// # Safety
/// `session_start`/`session_end` must be null or point to their (non-negative)
/// lengths.
#[no_mangle]
#[allow(clippy::too_many_arguments)]
pub unsafe extern "C" fn vy_risk_engine_new(
    max_position_qty: f64,
    max_order_qty: f64,
    cooldown_seconds: f64,
    max_notional_defined: i32,
    max_notional: f64,
    max_exposure_pct_defined: i32,
    max_exposure_pct: f64,
    daily_loss_limit_defined: i32,
    daily_loss_limit: f64,
    strategy_loss_limit_defined: i32,
    strategy_loss_limit: f64,
    spread_limit_pct_defined: i32,
    spread_limit_pct: f64,
    require_fresh_defined: i32,
    require_fresh_data_seconds: f64,
    max_orders_per_day_defined: i32,
    max_orders_per_day: i64,
    session_start: *const u8,
    session_start_len: i64,
    session_end: *const u8,
    session_end_len: i64,
) -> i64 {
    let start = match rs_bound(session_start, session_start_len) {
        Ok(bound) => bound,
        Err(_) => return 0,
    };
    let end = match rs_bound(session_end, session_end_len) {
        Ok(bound) => bound,
        Err(_) => return 0,
    };
    let policy = risk_engine::RiskPolicy {
        max_position_qty,
        max_order_qty,
        max_notional: rs_opt(max_notional_defined, max_notional),
        max_exposure_pct: rs_opt(max_exposure_pct_defined, max_exposure_pct),
        daily_loss_limit: rs_opt(daily_loss_limit_defined, daily_loss_limit),
        strategy_loss_limit: rs_opt(strategy_loss_limit_defined, strategy_loss_limit),
        allowed_symbols: Vec::new(),
        spread_limit_pct: rs_opt(spread_limit_pct_defined, spread_limit_pct),
        require_fresh_data_seconds: rs_opt(require_fresh_defined, require_fresh_data_seconds),
        session_start: start.map(str::to_string),
        session_end: end.map(str::to_string),
        cooldown_seconds,
        max_orders_per_day: (max_orders_per_day_defined != 0).then_some(max_orders_per_day),
    };
    let slot = Some(RiskSlot {
        engine: risk_engine::RiskEngine::new(policy),
        decision: None,
    });
    let mut slots = risk_slots();
    if let Some(free) = slots.iter().position(Option::is_none) {
        slots[free] = slot;
        return free as i64 + 1;
    }
    slots.push(slot);
    slots.len() as i64
}

/// Append one allowed symbol (the allow-list is empty = universe unrestricted).
///
/// # Safety
/// `symbol` must point to `symbol_len` readable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_risk_engine_allow(
    handle: i64,
    symbol: *const u8,
    symbol_len: i64,
) -> i32 {
    let Some(index) = risk_index(handle) else {
        return -2;
    };
    let text = match rs_text(symbol, symbol_len) {
        Ok(text) => text,
        Err(code) => return code,
    };
    let mut slots = risk_slots();
    let Some(slot) = slots.get_mut(index).and_then(Option::as_mut) else {
        return -2;
    };
    slot.engine
        .policy_mut()
        .allowed_symbols
        .push(text.to_string());
    0
}

/// Evaluate one request against the engine's policy. `1` approved, `0` denied
/// (the verdict itself), `-1` undecodable text, `-2` bridge misuse. A
/// successful call stores the decision document for
/// `vy_risk_engine_decision`.
///
/// # Safety
/// Every text pointer must be null or point to its (non-negative) length.
#[no_mangle]
#[allow(clippy::too_many_arguments)]
pub unsafe extern "C" fn vy_risk_engine_evaluate(
    handle: i64,
    kill_halted: i32,
    intent_id: *const u8,
    intent_id_len: i64,
    strategy_id: *const u8,
    strategy_id_len: i64,
    symbol: *const u8,
    symbol_len: i64,
    side: *const u8,
    side_len: i64,
    timestamp: *const u8,
    timestamp_len: i64,
    quantity: f64,
    price: f64,
    position_qty: f64,
    day_pnl: f64,
    strategy_day_pnl: f64,
    equity: f64,
    available_capital: f64,
    now_epoch: f64,
    orders_today: i64,
    spread_pct_defined: i32,
    spread_pct: f64,
    data_age_defined: i32,
    data_age_seconds: f64,
    last_order_defined: i32,
    last_order_epoch: f64,
    broker_healthy: i32,
) -> i32 {
    let Some(index) = risk_index(handle) else {
        return -2;
    };
    let intent_id = match rs_text(intent_id, intent_id_len) {
        Ok(text) => text.to_string(),
        Err(code) => return code,
    };
    let strategy_id = match rs_text(strategy_id, strategy_id_len) {
        Ok(text) => text.to_string(),
        Err(code) => return code,
    };
    let symbol = match rs_text(symbol, symbol_len) {
        Ok(text) => text.to_string(),
        Err(code) => return code,
    };
    let side = match rs_text(side, side_len) {
        Ok(text) => text.to_string(),
        Err(code) => return code,
    };
    let timestamp = match rs_text(timestamp, timestamp_len) {
        Ok(text) => text.to_string(),
        Err(code) => return code,
    };
    let request = risk_engine::RiskRequest {
        intent_id,
        strategy_id,
        symbol,
        side,
        quantity,
        price,
        timestamp,
        position_qty,
        day_pnl,
        strategy_day_pnl,
        equity,
        available_capital,
        spread_pct: rs_opt(spread_pct_defined, spread_pct),
        data_age_seconds: rs_opt(data_age_defined, data_age_seconds),
        broker_healthy: broker_healthy != 0,
        orders_today,
        last_order_epoch: rs_opt(last_order_defined, last_order_epoch),
        now_epoch,
    };
    let mut slots = risk_slots();
    let Some(slot) = slots.get_mut(index).and_then(Option::as_mut) else {
        return -2;
    };
    slot.decision = None;
    let decision = slot.engine.evaluate(&request, kill_halted != 0);
    let approved = i32::from(decision.approved);
    slot.decision = Some(decision);
    approved
}

/// The decision document of the last successful evaluate, as JSON: `approved`,
/// `intent_id`, `reasons` and `checks` (name/passed/detail in checklist order).
/// Returns the byte length needed (NUL excluded), `-1` when no evaluate has
/// succeeded on this handle, `-2` bridge misuse.
///
/// # Safety
/// `buf` must be null or point to `cap` writable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_risk_engine_decision(handle: i64, buf: *mut u8, cap: usize) -> i32 {
    let Some(index) = risk_index(handle) else {
        return -2;
    };
    let slots = risk_slots();
    let Some(Some(slot)) = slots.get(index) else {
        return -2;
    };
    match &slot.decision {
        Some(decision) => ks_write(&risk_engine::decision_to_json(decision), buf, cap),
        None => -1,
    }
}

/// Release an engine: `0` freed, `-2` unknown handle.
#[no_mangle]
pub extern "C" fn vy_risk_engine_free(handle: i64) -> i32 {
    let Some(index) = risk_index(handle) else {
        return -2;
    };
    let mut slots = risk_slots();
    let freed = slots
        .get_mut(index)
        .is_some_and(|entry| entry.take().is_some());
    if freed {
        0
    } else {
        -2
    }
}

// ── streaming market data, execution sizing, download jobs ────────────────
//
// Each export here is a pure question a live Python path used to answer with
// its own copy of a rule the kernel already owns. They carry no state: the
// caller keeps the dict, the kernel keeps the decision.

/// Session anchor `"HH:MM"` → seconds of day; 09:15 when it does not read as
/// two integers. `-1` means the bytes were not UTF-8.
///
/// # Safety
/// `text` must point to `text_len` readable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_agg_anchor_seconds(text: *const u8, text_len: i64) -> i64 {
    match rs_bound(text, text_len) {
        Ok(Some(value)) => aggregate::anchor_seconds(value),
        Ok(None) => aggregate::anchor_seconds(""),
        Err(_) => -1,
    }
}

/// Bucket start for one quote stamp, as `"YYYY-MM-DD HH:MM:SS"` text.
/// `-1` undecodable stamp, `-2` malformed stamp.
///
/// # Safety
/// `stamp` must point to `stamp_len` readable bytes; `buf` must be null or
/// point to `cap` writable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_agg_bucket_start(
    stamp: *const u8,
    stamp_len: i64,
    size_s: i64,
    anchor_s: i64,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    let text = match rs_text(stamp, stamp_len) {
        Ok(text) => text,
        Err(code) => return code,
    };
    match aggregate::bucket_start(text, size_s, anchor_s) {
        Some(start) => ks_write(&start, buf, cap),
        None => -2,
    }
}

/// Fold a quote into its bucket, writing `(open, high, low, close, volume)` to
/// `out`. Returns `5`, or `-1` when `out` cannot hold five doubles.
///
/// # Safety
/// `out` must point to `out_cap` writable doubles.
#[no_mangle]
#[allow(clippy::too_many_arguments)]
pub unsafe extern "C" fn vy_agg_fold_tick(
    fresh: i32,
    open: f64,
    high: f64,
    low: f64,
    volume: f64,
    price: f64,
    dayvol: f64,
    has_last_dayvol: i32,
    last_dayvol: f64,
    out: *mut f64,
    out_cap: usize,
) -> i32 {
    if out_cap < 5 {
        return -1;
    }
    let folded = aggregate::fold_tick(
        fresh != 0,
        open,
        high,
        low,
        volume,
        price,
        dayvol,
        rs_opt(has_last_dayvol, last_dayvol),
    );
    slice_mut(out, 5).copy_from_slice(&folded);
    5
}

/// Rows to emit from a fresh ascending batch; the still-forming newest row is
/// withheld by the kernel, not by the caller.
#[no_mangle]
pub extern "C" fn vy_agg_closed_count(fresh: i64, newest_fresh_is_forming: i32) -> i64 {
    aggregate::closed_count(fresh, newest_fresh_is_forming != 0)
}

/// The usable floor for a staleness window.
#[no_mangle]
pub extern "C" fn vy_norm_stale_floor(seconds: f64) -> f64 {
    normalizer::stale_floor(seconds)
}

/// `1` when the stream has been quiet longer than the window, else `0`.
#[no_mangle]
pub extern "C" fn vy_norm_gap_stale(now_epoch: f64, last_seen: f64, stale_after: f64) -> i32 {
    normalizer::gap_stale(now_epoch, last_seen, stale_after) as i32
}

/// Sequence watermark after an arriving event's sequence.
#[no_mangle]
pub extern "C" fn vy_norm_watermark(last_seq: i64, incoming_seq: i64) -> i64 {
    normalizer::watermark(last_seq, incoming_seq)
}

/// Why a stalled stream is unhealthy, as text — the wording `check_health`
/// reports.
///
/// # Safety
/// `buf` must be null or point to `cap` writable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_norm_gap_reason(buf: *mut u8, cap: usize) -> i32 {
    ks_write(normalizer::STALE_GAP_REASON, buf, cap)
}

/// Default order quantity: affordable at `price`, capped by the policy ceiling.
#[no_mangle]
pub extern "C" fn vy_exec_default_quantity(
    available_capital: f64,
    price: f64,
    max_order_qty: f64,
) -> f64 {
    execution_engine::default_quantity(available_capital, price, max_order_qty)
}

/// Advisory multiplier as the planner can use it (`1.0` = no shrink).
#[no_mangle]
pub extern "C" fn vy_exec_narrow_multiplier(defined: i32, raw: f64) -> f64 {
    execution_engine::narrow_multiplier(defined != 0, raw)
}

/// Why a `size_multiplier` is unusable; an empty answer means it is usable.
///
/// # Safety
/// `buf` must be null or point to `cap` writable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_exec_multiplier_message(
    size_multiplier: f64,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    let problem = execution_engine::multiplier_problem(size_multiplier).unwrap_or("");
    ks_write(problem, buf, cap)
}

/// Gate booleans in declaration order → mask. `-1` when the count is not the
/// five mandatory gates, or the pointer is missing.
///
/// # Safety
/// `flags` must point to `count` readable i32s.
#[no_mangle]
pub unsafe extern "C" fn vy_exec_flags_to_mask(flags: *const i32, count: usize) -> i64 {
    if flags.is_null() {
        return -1;
    }
    let values: Vec<bool> = slice(flags, count).iter().map(|on| *on != 0).collect();
    match execution_engine::bool_flags_to_mask(&values) {
        Some(mask) => mask as i64,
        None => -1,
    }
}

/// Mask → the five gate flags, in gate order. Returns `5`, or `-1` when `out`
/// cannot hold five i32s.
///
/// # Safety
/// `out` must point to `out_cap` writable i32s.
#[no_mangle]
pub unsafe extern "C" fn vy_exec_mask_to_flags(mask: i64, out: *mut i32, out_cap: usize) -> i32 {
    if out_cap < execution_engine::GATE_COUNT {
        return -1;
    }
    let gates = execution_engine::gates_from_mask(mask as u32)
        .as_flags()
        .map(i32::from);
    slice_mut(out, execution_engine::GATE_COUNT).copy_from_slice(&gates);
    execution_engine::GATE_COUNT as i32
}

/// Download window for `"YYYY-MM-DD"` bounds, as `"<ok>|<from>|<to>|<reason>"`.
/// `-1` means undecodable input.
///
/// # Safety
/// `from_date`/`to_date` must point to their (non-negative) lengths; `buf` must
/// be null or point to `cap` writable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_dl_validate_range(
    from_date: *const u8,
    from_len: i64,
    to_date: *const u8,
    to_len: i64,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    let from = match rs_text(from_date, from_len) {
        Ok(text) => text,
        Err(_) => return -1,
    };
    let to = match rs_text(to_date, to_len) {
        Ok(text) => text,
        Err(_) => return -1,
    };
    match download::validate_range(from, to) {
        Ok((from_secs, to_secs)) => ks_write(&format!("1|{from_secs}|{to_secs}|"), buf, cap),
        Err(reason) => ks_write(&format!("0|-1|-1|{reason}"), buf, cap),
    }
}

/// Queue rank for a download state's spelling; lower runs first.
///
/// # Safety
/// `name` must point to `name_len` readable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_dl_job_rank(name: *const u8, name_len: i64) -> i64 {
    match rs_bound(name, name_len) {
        Ok(Some(value)) => download::job_rank_name(value),
        Ok(None) => 9,
        Err(_) => -1,
    }
}

#[cfg(test)]
mod ffi_rule_tests {
    use super::*;

    fn drain(invoke: impl Fn(*mut u8, usize) -> i32) -> String {
        let needed = invoke(std::ptr::null_mut(), 0);
        assert!(needed >= 0, "kernel rejected the call: {needed}");
        let mut buf = vec![0u8; (needed as usize) + 1];
        assert_eq!(invoke(buf.as_mut_ptr(), buf.len()), needed);
        String::from_utf8(buf[..needed as usize].to_vec()).unwrap()
    }

    #[test]
    fn the_streaming_abi_answers_like_the_kernel() {
        assert_eq!(
            unsafe { vy_agg_anchor_seconds(b"09:15".as_ptr(), 5) },
            33_300
        );
        assert_eq!(
            unsafe { vy_agg_anchor_seconds(b"junk".as_ptr(), 4) },
            33_300
        );
        assert_eq!(
            unsafe { vy_agg_anchor_seconds(std::ptr::null(), -1) },
            33_300
        );
        assert_eq!(unsafe { vy_agg_anchor_seconds(std::ptr::null(), 4) }, -1);
        assert_eq!(vy_agg_closed_count(3, 1), 2);
        assert_eq!(vy_agg_closed_count(3, 0), 3);
        assert_eq!(vy_agg_closed_count(0, 1), 0);
        assert_eq!(
            drain(|b, c| unsafe {
                vy_agg_bucket_start(b"2026-01-05 09:29:59".as_ptr(), 19, 900, 33_300, b, c)
            }),
            "2026-01-05 09:15:00"
        );
        assert_eq!(
            unsafe {
                vy_agg_bucket_start(b"short".as_ptr(), 5, 900, 33_300, std::ptr::null_mut(), 0)
            },
            -2
        );
        let mut bucket = [0f64; 5];
        assert_eq!(
            unsafe {
                vy_agg_fold_tick(
                    0,
                    10.0,
                    12.0,
                    9.0,
                    100.0,
                    13.0,
                    900.0,
                    1,
                    800.0,
                    bucket.as_mut_ptr(),
                    5,
                )
            },
            5
        );
        assert_eq!(bucket, [10.0, 13.0, 9.0, 13.0, 200.0]);
        assert_eq!(
            unsafe {
                vy_agg_fold_tick(
                    1,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    13.0,
                    900.0,
                    0,
                    0.0,
                    bucket.as_mut_ptr(),
                    5,
                )
            },
            5
        );
        assert_eq!(bucket, [13.0, 13.0, 13.0, 13.0, 0.0]);
        assert_eq!(
            unsafe {
                vy_agg_fold_tick(
                    1,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    1.0,
                    1.0,
                    0,
                    0.0,
                    std::ptr::null_mut(),
                    0,
                )
            },
            -1
        );
    }

    #[test]
    fn stream_gates_and_sizing_answer_from_the_kernel() {
        assert_eq!(vy_norm_stale_floor(0.25), 1.0);
        assert_eq!(vy_norm_gap_stale(1_000.0, 600.0, 300.0), 1);
        assert_eq!(vy_norm_gap_stale(900.0, 600.0, 300.0), 0);
        assert_eq!(vy_norm_watermark(4, 2), 5);
        assert_eq!(vy_norm_watermark(4, 40), 40);
        assert_eq!(vy_exec_default_quantity(100_000.0, 250.0, 200.0), 200.0);
        assert_eq!(vy_exec_default_quantity(1_000.0, 250.0, 200.0), 4.0);
        assert_eq!(vy_exec_default_quantity(1_000.0, 0.0, 200.0), 0.0);
        assert_eq!(vy_exec_narrow_multiplier(0, 0.4), 1.0);
        assert_eq!(vy_exec_narrow_multiplier(1, 1.4), 1.0);
        assert_eq!(vy_exec_narrow_multiplier(1, 0.4), 0.4);
        assert_eq!(
            drain(|b, c| unsafe { vy_exec_multiplier_message(0.4, b, c) }),
            ""
        );
        assert_eq!(
            drain(|b, c| unsafe { vy_exec_multiplier_message(0.0, b, c) }),
            "size_multiplier must be in (0, 1]"
        );
        let flags = [1i32, 0, 1, 0, 0];
        let mask = unsafe { vy_exec_flags_to_mask(flags.as_ptr(), 5) };
        assert_eq!(mask, 5);
        assert_eq!(unsafe { vy_exec_flags_to_mask(flags.as_ptr(), 4) }, -1);
        let mut back = [0i32; 5];
        assert_eq!(
            unsafe { vy_exec_mask_to_flags(mask, back.as_mut_ptr(), 5) },
            5
        );
        assert_eq!(back, [1, 0, 1, 0, 0]);
    }

    #[test]
    fn download_bounds_and_queue_rank_come_from_the_kernel() {
        let usable = drain(|b, c| unsafe {
            vy_dl_validate_range(b"2026-01-05".as_ptr(), 10, b"2026-02-05".as_ptr(), 10, b, c)
        });
        let fields: Vec<&str> = usable.split('|').collect();
        assert_eq!(fields.len(), 4);
        assert_eq!(fields[0], "1");
        assert!(fields[3].is_empty(), "a usable range carries no reason");
        assert!(fields[1].parse::<i64>().unwrap() < fields[2].parse::<i64>().unwrap());
        assert_eq!(
            drain(|b, c| unsafe {
                vy_dl_validate_range(b"2026-02-05".as_ptr(), 10, b"2026-01-05".as_ptr(), 10, b, c)
            }),
            "0|-1|-1|from date after to date"
        );
        assert_eq!(
            drain(|b, c| unsafe {
                vy_dl_validate_range(b"nope".as_ptr(), 4, b"2026-01-05".as_ptr(), 10, b, c)
            }),
            "0|-1|-1|invalid date range"
        );
        assert_eq!(
            unsafe { vy_dl_job_rank(b"PARTIAL_DOWNLOAD".as_ptr(), 16) },
            1
        );
        assert_eq!(unsafe { vy_dl_job_rank(b"NOT_STARTED".as_ptr(), 11) }, 2);
        assert_eq!(
            unsafe { vy_dl_job_rank(b"DOWNLOAD_COMPLETE".as_ptr(), 17) },
            3
        );
        assert_eq!(unsafe { vy_dl_job_rank(b"WHATEVER".as_ptr(), 8) }, 9);
    }
}

#[cfg(test)]
mod kill_switch_ffi_tests {
    use super::*;

    /// Drain a `-> i32` length-returning call into a `String`.
    unsafe fn drain(len: i32, fill: impl Fn(*mut u8, usize) -> i32) -> String {
        assert!(len >= 0, "call rejected: {len}");
        let mut buf = vec![0u8; len as usize + 1];
        assert_eq!(fill(buf.as_mut_ptr(), buf.len()), len);
        assert_eq!(buf[len as usize], 0, "kernel must NUL-terminate");
        String::from_utf8(buf[..len as usize].to_vec()).unwrap()
    }

    unsafe fn message(handle: i64) -> String {
        let len = vy_ks_last_message(handle, std::ptr::null_mut(), 0);
        drain(len, |buf, cap| vy_ks_last_message(handle, buf, cap))
    }

    unsafe fn engage(handle: i64, reason: &str, level: &str) -> i32 {
        vy_ks_engage(
            handle,
            reason.as_ptr(),
            reason.len(),
            level.as_ptr(),
            level.len(),
        )
    }

    unsafe fn halted(handle: i64, level: &str) -> i32 {
        vy_ks_is_halted(handle, level.as_ptr(), level.len())
    }

    unsafe fn load(handle: i64, text: &str) -> i32 {
        vy_ks_load(handle, text.as_ptr(), text.len())
    }

    /// The slot registry is process-global, so handle numbering and slot
    /// recycling are observable facts: these tests serialize on one lock.
    static KS_TEST_LOCK: Mutex<()> = Mutex::new(());

    fn ks_test_guard() -> MutexGuard<'static, ()> {
        KS_TEST_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    #[test]
    fn level_vocabulary_is_owned_by_the_kernel() {
        let _guard = ks_test_guard();
        assert_eq!(vy_ks_level_count(), 3);
        for (index, expected) in KillSwitchLevel::ALL.iter().enumerate() {
            let len = unsafe { vy_ks_level_name(index as i32, std::ptr::null_mut(), 0) };
            let name = unsafe { drain(len, |buf, cap| vy_ks_level_name(index as i32, buf, cap)) };
            assert_eq!(name, expected.as_str());
        }
        assert_eq!(unsafe { vy_ks_level_name(3, std::ptr::null_mut(), 0) }, -2);
    }

    #[test]
    fn rejection_text_survives_the_boundary() {
        let _guard = ks_test_guard();
        let handle = unsafe {
            let h = vy_ks_new();
            assert!(h > 0);
            assert_eq!(engage(h, "operator halt", "nope"), -1);
            assert_eq!(message(h), "unknown kill-switch level: nope");
            assert_eq!(halted(h, "nope"), -1);
            assert_eq!(message(h), "unknown kill-switch level: nope");
            assert_eq!(vy_ks_disengage(h, "nope".as_ptr(), 4), -1);
            assert_eq!(message(h), "unknown kill-switch level: nope");
            h
        };
        assert_eq!(vy_ks_free(handle), 0);
    }

    #[test]
    fn buffers_are_only_written_when_capacity_fits() {
        let _guard = ks_test_guard();
        let handle = vy_ks_new();
        unsafe {
            assert_eq!(engage(handle, "halt", "global"), 0);
            let needed = vy_ks_serialize(handle, std::ptr::null_mut(), 0);
            assert!(needed > 0);
            let mut short = vec![0xAAu8; 4];
            assert_eq!(
                vy_ks_serialize(handle, short.as_mut_ptr(), short.len()),
                needed
            );
            assert!(short.iter().all(|&b| b == 0xAA), "undersized call wrote");
            let text = drain(needed, |buf, cap| vy_ks_serialize(handle, buf, cap));
            assert_eq!(text.as_bytes().len(), needed as usize);
            assert!(text.starts_with("{\n  \"broker\": {\n"));
        }
        assert_eq!(vy_ks_free(handle), 0);
    }

    #[test]
    fn state_reads_and_halt_gate_cross_the_boundary() {
        let _guard = ks_test_guard();
        let handle = vy_ks_new();
        unsafe {
            assert_eq!(halted(handle, "strategy"), 0);
            assert_eq!(engage(handle, "bug", "strategy"), 0);
            assert_eq!(halted(handle, "strategy"), 1);
            assert_eq!(vy_ks_state_engaged(handle, "global".as_ptr(), 6), 0);
            let len = vy_ks_state_reason(handle, "strategy".as_ptr(), 8, std::ptr::null_mut(), 0);
            assert_eq!(
                drain(len, |buf, cap| vy_ks_state_reason(
                    handle,
                    "strategy".as_ptr(),
                    8,
                    buf,
                    cap
                )),
                "bug"
            );
            let stamp_len =
                vy_ks_state_engaged_at(handle, "strategy".as_ptr(), 8, std::ptr::null_mut(), 0);
            let stamp = drain(stamp_len, |buf, cap| {
                vy_ks_state_engaged_at(handle, "strategy".as_ptr(), 8, buf, cap)
            });
            assert!(stamp.ends_with("+00:00"), "{stamp}");
            assert_eq!(vy_ks_disengage(handle, "strategy".as_ptr(), 8), 0);
            assert_eq!(halted(handle, "strategy"), 0);
        }
        assert_eq!(vy_ks_free(handle), 0);
    }

    #[test]
    fn load_reports_applied_garbage_and_unwalkable() {
        let _guard = ks_test_guard();
        let handle = vy_ks_new();
        unsafe {
            assert_eq!(load(handle, "not json at all"), 1);
            assert_eq!(message(handle), "");
            assert_eq!(halted(handle, "global"), 0);

            assert_eq!(
                load(
                    handle,
                    "{\"global\": {\"engaged\": true, \"reason\": \"saved\"}}"
                ),
                0
            );
            assert_eq!(halted(handle, "strategy"), 1);

            assert_eq!(load(handle, "[1, 2]"), -1);
            assert_eq!(message(handle), "'list' object has no attribute 'get'");
        }
        assert_eq!(vy_ks_free(handle), 0);
    }

    #[test]
    fn freed_and_unknown_handles_fail_closed() {
        let _guard = ks_test_guard();
        let handle = vy_ks_new();
        unsafe {
            assert_eq!(vy_ks_free(handle), 0);
            assert_eq!(vy_ks_free(handle), -2);
            assert_eq!(engage(handle, "x", "global"), -2);
            assert_eq!(halted(handle, "global"), -2);
            assert_eq!(vy_ks_serialize(handle, std::ptr::null_mut(), 0), -2);
            assert_eq!(load(handle, "{}"), -2);
            assert_eq!(vy_ks_last_message(handle, std::ptr::null_mut(), 0), -2);
            for bad in [0i64, -1, i64::MIN, 1 << 40] {
                assert_eq!(halted(bad, "global"), -2);
            }
            // Freed slots are recycled, so a fresh switch never aliases a live one.
            let again = vy_ks_new();
            assert_eq!(again, handle);
            assert_eq!(halted(again, "global"), 0);
            assert_eq!(vy_ks_free(again), 0);
        }
    }
}

#[cfg(test)]
mod timeframe_ffi_tests {
    use super::*;

    unsafe fn text(len: i32, fill: impl Fn(*mut u8, usize) -> i32) -> String {
        assert!(len >= 0, "call rejected: {len}");
        let mut buf = vec![0u8; len as usize + 1];
        assert_eq!(fill(buf.as_mut_ptr(), buf.len()), len);
        assert_eq!(buf[len as usize], 0, "kernel must NUL-terminate");
        buf.truncate(len as usize);
        String::from_utf8(buf).expect("kernel emitted non-UTF-8")
    }

    fn seconds_of(name: &str) -> i64 {
        unsafe { vy_timeframe_seconds(name.as_ptr(), name.len()) }
    }

    fn label_of(seconds: i64) -> Option<String> {
        unsafe {
            let len = vy_timeframe_label(seconds, std::ptr::null_mut(), 0);
            if len == 0 {
                return None;
            }
            Some(text(len, |buf, cap| vy_timeframe_label(seconds, buf, cap)))
        }
    }

    #[test]
    fn ladder_vocabulary_comes_from_the_kernel() {
        let count = vy_timeframe_ladder_count();
        assert_eq!(count, 11);
        let labels: Vec<String> = (0..count)
            .map(|index| unsafe {
                text(
                    vy_timeframe_ladder_label(index, std::ptr::null_mut(), 0),
                    |buf, cap| vy_timeframe_ladder_label(index, buf, cap),
                )
            })
            .collect();
        assert_eq!(labels.first().expect("empty ladder"), "1m");
        assert_eq!(labels.last().expect("empty ladder"), "1W");
        for (index, label) in labels.iter().enumerate() {
            assert_eq!(seconds_of(label), vy_timeframe_ladder_seconds(index as i32));
        }
        assert_eq!(vy_timeframe_ladder_seconds(count), -1);
        assert_eq!(vy_timeframe_ladder_seconds(-1), -1);
        unsafe {
            assert_eq!(
                vy_timeframe_ladder_label(count, std::ptr::null_mut(), 0),
                -1
            );
        }
    }

    #[test]
    fn label_lookup_maps_absence_to_zero() {
        assert_eq!(seconds_of("15m"), 900);
        assert_eq!(seconds_of("1D"), 86_400);
        assert_eq!(seconds_of("90m"), 5_400);
        assert_eq!(seconds_of("nonsense"), 0);
        assert_eq!(seconds_of(""), 0);
        let ragged = [0xF0u8, 0x9F, 0x99];
        assert_eq!(
            unsafe { vy_timeframe_seconds(ragged.as_ptr(), ragged.len()) },
            -1
        );
    }

    #[test]
    fn generated_labels_and_availability_cross_as_text() {
        assert_eq!(label_of(900).as_deref(), Some("15m"));
        assert_eq!(label_of(120), None);
        unsafe {
            assert_eq!(
                text(
                    vy_timeframe_generate_label(1_209_600, std::ptr::null_mut(), 0),
                    |buf, cap| vy_timeframe_generate_label(1_209_600, buf, cap)
                ),
                "2W"
            );
            assert_eq!(
                text(
                    vy_timeframe_available(120, std::ptr::null_mut(), 0),
                    |buf, cap| vy_timeframe_available(120, buf, cap)
                ),
                "2m,30m,1h,2h,4h,1D,1W"
            );
            assert_eq!(vy_timeframe_available(0, std::ptr::null_mut(), 0), 0);
        }
    }

    #[test]
    fn buffers_are_only_written_when_capacity_fits() {
        unsafe {
            let needed = vy_timeframe_available(60, std::ptr::null_mut(), 0);
            assert!(needed > 2);
            let mut short = vec![0xAAu8; 2];
            assert_eq!(
                vy_timeframe_available(60, short.as_mut_ptr(), short.len()),
                needed
            );
            assert!(short.iter().all(|&b| b == 0xAA), "undersized call wrote");
        }
    }
}

#[cfg(test)]
mod risk_session_ffi_tests {
    use super::*;

    const NOW: f64 = 1_767_700_000.0;

    /// `(ptr, len)` pair for an optional bound; `None` crosses as `-1`.
    fn bound(value: Option<&str>) -> (*const u8, i64) {
        match value {
            Some(text) => (text.as_ptr(), text.len() as i64),
            None => (std::ptr::null(), -1),
        }
    }

    fn session(timestamp: &str, start: Option<&str>, end: Option<&str>) -> i32 {
        let (start_ptr, start_len) = bound(start);
        let (end_ptr, end_len) = bound(end);
        unsafe {
            vy_risk_within_session(
                timestamp.as_ptr(),
                timestamp.len() as i64,
                start_ptr,
                start_len,
                end_ptr,
                end_len,
            )
        }
    }

    fn sane(timestamp: &str) -> i32 {
        unsafe { vy_risk_clock_sane(timestamp.as_ptr(), timestamp.len() as i64, NOW, 300.0) }
    }

    #[test]
    fn session_window_answers_over_the_abi() {
        assert_eq!(
            session("2026-01-06T10:00:00+00:00", Some("09:15"), Some("15:30")),
            1
        );
        assert_eq!(
            session("2026-01-06T09:15:00+00:00", Some("09:15"), Some("15:30")),
            1
        );
        assert_eq!(
            session("2026-01-06T18:00:00+00:00", Some("09:15"), Some("15:30")),
            0
        );
        assert_eq!(session("2026-01-06T18:00:00+00:00", None, None), 1);
        assert_eq!(session("2026-01-06T18:00:00+00:00", None, Some("15:30")), 0);
        // Preserved Python quirk: an unparsable stamp still sorts inside an
        // end-only window, because the empty slice compares below the bound.
        assert_eq!(session("x", None, Some("15:30")), 1);
    }

    #[test]
    fn session_gate_rejects_broken_bridge_calls() {
        let ragged = [0xF0u8, 0x9F, 0x99];
        unsafe {
            assert_eq!(
                vy_risk_within_session(
                    ragged.as_ptr(),
                    ragged.len() as i64,
                    std::ptr::null(),
                    -1,
                    std::ptr::null(),
                    -1
                ),
                -1
            );
            assert_eq!(
                vy_risk_within_session(
                    std::ptr::null(),
                    4,
                    std::ptr::null(),
                    -1,
                    std::ptr::null(),
                    -1
                ),
                -2
            );
            assert_eq!(
                vy_risk_within_session(
                    "2026-01-06T10:00:00".as_ptr(),
                    19,
                    std::ptr::null(),
                    5,
                    std::ptr::null(),
                    -1
                ),
                -2
            );
            assert_eq!(vy_risk_clock_sane(std::ptr::null(), 4, NOW, 300.0), -2);
        }
    }

    #[test]
    fn clock_gate_fails_closed_on_garbage() {
        assert_eq!(sane("2026-01-06T09:30:00+00:00"), 1);
        assert_eq!(sane("2999-01-01T00:00:00+00:00"), 0);
        assert_eq!(sane("not-a-time"), 0);
        assert_eq!(sane("2026-02-30T09:30:00+00:00"), 0);
        assert_eq!(sane("2026-01-05 09:15:00"), 1);
    }
}

// ── stream normalizer (handle-based) ─────────────────────────────────────
//
// `normalizer::StreamNormalizer<SeqToken>` is the sole authority for the
// transport-edge stream policy: the per-symbol watermark, gap accounting,
// reorder-buffer overflow, ordered delivery, heartbeat/stall staleness and
// every counter. Python hands over one arrival (its symbol, sequence number
// and an opaque token of its own) and reads back the verdict; the market
// event objects themselves never cross the boundary.
//
// A verdict document has three lines: `accepted,duplicates,gaps,evicted,
// heartbeats,stale_flags`, then the delivered tokens and the dropped tokens,
// each list comma-separated and empty when nothing left the buffer. Because
// observing mutates the gate, the transition and the read are separate calls:
// `vy_norm_observe` applies one arrival and keeps its document in the slot,
// `vy_norm_last_document` reads that document back without touching state, so
// the bridge's two-call buffer probe can never replay an arrival.
//
// Return codes match the other bridges: `0` success, `-1` undecodable
// argument, `-2` bridge misuse (bad or freed handle).

use normalizer::{NormalizerConfig, SeqToken, StreamInput, StreamNormalizer};

struct NormSlot {
    gate: StreamNormalizer<SeqToken>,
    document: String,
    health: String,
}

static NORM_SLOTS: Mutex<Vec<Option<NormSlot>>> = Mutex::new(Vec::new());

fn norm_slots() -> MutexGuard<'static, Vec<Option<NormSlot>>> {
    NORM_SLOTS
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn norm_index(handle: i64) -> Option<usize> {
    usize::try_from(handle.checked_sub(1)?).ok()
}

fn norm_list(values: &[i64]) -> String {
    values
        .iter()
        .map(i64::to_string)
        .collect::<Vec<_>>()
        .join(",")
}

fn norm_stats_line(stats: &normalizer::StreamStats) -> String {
    format!(
        "{},{},{},{},{},{}",
        stats.accepted,
        stats.duplicates,
        stats.gaps,
        stats.evicted,
        stats.heartbeats,
        stats.stale_flags
    )
}

/// Build one stream gate. Returns the handle (`>= 1`), or `0` when the
/// configuration is unusable — then no gate exists and the bridge raises.
#[no_mangle]
pub extern "C" fn vy_norm_new(
    max_reorder_buffer: i64,
    stale_after_seconds: f64,
    heartbeat_timeout_seconds: f64,
) -> i64 {
    if max_reorder_buffer < 1
        || !stale_after_seconds.is_finite()
        || !heartbeat_timeout_seconds.is_finite()
    {
        return 0;
    }
    let Ok(max_reorder_buffer) = usize::try_from(max_reorder_buffer) else {
        return 0;
    };
    let slot = NormSlot {
        gate: StreamNormalizer::new(NormalizerConfig {
            max_reorder_buffer,
            stale_after_seconds,
            heartbeat_timeout_seconds,
        }),
        document: String::new(),
        health: String::new(),
    };
    let mut slots = norm_slots();
    if let Some(free) = slots.iter().position(Option::is_none) {
        slots[free] = Some(slot);
        return free as i64 + 1;
    }
    slots.push(Some(slot));
    slots.len() as i64
}

#[no_mangle]
pub extern "C" fn vy_norm_free(handle: i64) -> i32 {
    let Some(index) = norm_index(handle) else {
        return -2;
    };
    let mut slots = norm_slots();
    let freed = slots
        .get_mut(index)
        .is_some_and(|entry| entry.take().is_some());
    if freed {
        0
    } else {
        -2
    }
}

/// Apply one arrival to the gate and keep its verdict document. `heartbeat`
/// (non-zero) selects the health branch, where `seq`/`token` are unused.
///
/// # Safety
/// `symbol` must point to `symbol_len` readable bytes.
#[no_mangle]
#[allow(clippy::too_many_arguments)]
pub unsafe extern "C" fn vy_norm_observe(
    handle: i64,
    symbol: *const u8,
    symbol_len: i64,
    seq: i64,
    token: i64,
    now_epoch: f64,
    heartbeat: i32,
) -> i32 {
    let Ok(text) = rs_text(symbol, symbol_len) else {
        return -1;
    };
    let input = if heartbeat != 0 {
        StreamInput::Heartbeat {
            symbol: text.to_string(),
            timestamp: String::new(),
            seq: 0,
        }
    } else {
        StreamInput::Event(SeqToken {
            symbol: text.to_string(),
            seq,
            token,
        })
    };
    let mut slots = norm_slots();
    let Some(index) = norm_index(handle) else {
        return -2;
    };
    let Some(slot) = slots.get_mut(index).and_then(Option::as_mut) else {
        return -2;
    };
    let Ok(observed) = slot.gate.observe_report(input, now_epoch) else {
        return -1;
    };
    let delivered: Vec<i64> = observed
        .delivered
        .iter()
        .map(|arrival| arrival.token)
        .collect();
    let dropped: Vec<i64> = observed
        .dropped
        .iter()
        .map(|arrival| arrival.token)
        .collect();
    slot.document = format!(
        "{}\n{}\n{}\n",
        norm_stats_line(slot.gate.stats()),
        norm_list(&delivered),
        norm_list(&dropped),
    );
    0
}

/// The last verdict document, without applying anything. Returns the byte
/// length needed; empty when the gate has not observed an arrival yet.
///
/// # Safety
/// `buf` must be null or writable up to `cap`.
#[no_mangle]
pub unsafe extern "C" fn vy_norm_last_document(handle: i64, buf: *mut u8, cap: usize) -> i32 {
    let mut slots = norm_slots();
    match norm_index(handle).and_then(|index| slots.get_mut(index)) {
        Some(Some(slot)) => ks_write(&slot.document, buf, cap),
        _ => -2,
    }
}

/// Apply one health check and keep its verdict document. Every failing call
/// counts in the kernel, so the verdict is read back without re-applying it.
///
/// # Safety
/// `symbol` must point to `symbol_len` readable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_norm_check_health(
    handle: i64,
    symbol: *const u8,
    symbol_len: i64,
    now_epoch: f64,
) -> i32 {
    let Ok(text) = rs_text(symbol, symbol_len) else {
        return -1;
    };
    let mut slots = norm_slots();
    let Some(index) = norm_index(handle) else {
        return -2;
    };
    let Some(slot) = slots.get_mut(index).and_then(Option::as_mut) else {
        return -2;
    };
    let (healthy, reason) = slot.gate.check_health(text, now_epoch);
    slot.health = format!("{}|{}", u8::from(healthy), reason);
    0
}

/// The last health verdict as `healthy|reason`, without checking anything.
/// Returns the byte length needed; empty before the first health check.
///
/// # Safety
/// `buf` must be null or point to `cap` writable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_norm_last_health(handle: i64, buf: *mut u8, cap: usize) -> i32 {
    let mut slots = norm_slots();
    match norm_index(handle).and_then(|index| slots.get_mut(index)) {
        Some(Some(slot)) => ks_write(&slot.health, buf, cap),
        _ => -2,
    }
}

/// `1` while a symbol is flagged stale (until a fresh arrival clears it).
///
/// # Safety
/// `symbol` must point to `symbol_len` readable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_norm_is_stale(handle: i64, symbol: *const u8, symbol_len: i64) -> i32 {
    let Ok(text) = rs_text(symbol, symbol_len) else {
        return -1;
    };
    let mut slots = norm_slots();
    match norm_index(handle).and_then(|index| slots.get_mut(index)) {
        Some(Some(slot)) => i32::from(slot.gate.is_stale(text)),
        _ => -2,
    }
}

/// The sequence number the next in-order arrival must carry.
///
/// # Safety
/// `symbol` must point to `symbol_len` readable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_norm_expected_seq(
    handle: i64,
    symbol: *const u8,
    symbol_len: i64,
) -> i64 {
    let Ok(text) = rs_text(symbol, symbol_len) else {
        return -1;
    };
    let mut slots = norm_slots();
    match norm_index(handle).and_then(|index| slots.get_mut(index)) {
        Some(Some(slot)) => slot.gate.expected_seq(text),
        _ => -2,
    }
}

/// Counter snapshot (a document's first line, without its newline) for calls
/// that mutate stats without returning a document.
///
/// # Safety
/// `buf` must be null or writable up to `cap`.
#[no_mangle]
pub unsafe extern "C" fn vy_norm_stats(handle: i64, buf: *mut u8, cap: usize) -> i32 {
    let mut slots = norm_slots();
    match norm_index(handle).and_then(|index| slots.get_mut(index)) {
        Some(Some(slot)) => ks_write(&norm_stats_line(slot.gate.stats()), buf, cap),
        _ => -2,
    }
}

#[cfg(test)]
mod stream_normalizer_ffi_tests {
    use super::*;

    fn drain_i32(needed: i32, call: impl Fn(*mut u8, usize) -> i32) -> String {
        let mut buffer = vec![0u8; needed as usize + 1];
        assert_eq!(call(buffer.as_mut_ptr(), buffer.len()), needed);
        String::from_utf8(buffer[..needed as usize].to_vec()).unwrap()
    }

    fn raw(symbol: &str) -> (*const u8, i64) {
        (symbol.as_ptr(), symbol.len() as i64)
    }

    /// Apply one arrival, then read the kept document back through the
    /// non-mutating probe path — exactly what the ctypes bridge does.
    unsafe fn observe(
        handle: i64,
        symbol: &str,
        seq: i64,
        token: i64,
        now: f64,
    ) -> (Vec<i64>, Vec<i64>, [u64; 6]) {
        let (ptr, len) = raw(symbol);
        assert_eq!(
            vy_norm_observe(handle, ptr, len, seq, token, now, 0),
            0,
            "kernel rejected the arrival"
        );
        let doc = drain_i32(
            vy_norm_last_document(handle, std::ptr::null_mut(), 0),
            |buf, cap| vy_norm_last_document(handle, buf, cap),
        );
        let mut lines = doc.lines();
        let stats: [u64; 6] = lines
            .next()
            .unwrap()
            .split(',')
            .map(|field| field.parse::<u64>().unwrap())
            .collect::<Vec<_>>()
            .try_into()
            .unwrap();
        let list = |line: Option<&str>| -> Vec<i64> {
            match line.unwrap() {
                "" => Vec::new(),
                text => text.split(',').map(|v| v.parse::<i64>().unwrap()).collect(),
            }
        };
        (list(lines.next()), list(lines.next()), stats)
    }

    #[test]
    fn the_bridge_sees_deliveries_retirements_and_counters() {
        let handle = vy_norm_new(2, 60.0, 30.0);
        assert!(handle >= 1);
        // 1 delivers; 3 gaps (2 missing) and waits; 2 releases both.
        assert_eq!(unsafe { observe(handle, "A", 1, 101, 1.0) }.0, vec![101]);
        let (ready, dropped, _) = unsafe { observe(handle, "A", 3, 103, 2.0) };
        assert!(ready.is_empty() && dropped.is_empty());
        let (ready, dropped, stats) = unsafe { observe(handle, "A", 2, 102, 3.0) };
        assert_eq!(ready, vec![102, 103]);
        assert!(dropped.is_empty());
        assert_eq!(stats, [3, 0, 1, 0, 0, 0]);
        // A late replay below the watermark is retired by the kernel itself.
        let (ready, dropped, stats) = unsafe { observe(handle, "A", 2, 199, 4.0) };
        assert!(ready.is_empty());
        assert_eq!(dropped, vec![199]);
        assert_eq!(stats, [3, 1, 1, 0, 0, 0]);
        assert_eq!(unsafe { vy_norm_expected_seq(handle, raw("A").0, 1) }, 4);
        // Two buffered arrivals fill the two-slot window; the third (a late 4,
        // exactly the next in line) is the eviction candidate, so the buffered
        // window delivers around it.
        let (ready, dropped, _) = unsafe { observe(handle, "A", 5, 105, 5.0) };
        assert!(ready.is_empty() && dropped.is_empty());
        let (ready, dropped, _) = unsafe { observe(handle, "A", 6, 106, 6.0) };
        assert!(ready.is_empty() && dropped.is_empty());
        let (ready, dropped, stats) = unsafe { observe(handle, "A", 4, 104, 7.0) };
        assert_eq!(ready, vec![105, 106]);
        assert_eq!(dropped, vec![104]);
        assert_eq!(stats, [5, 1, 3, 1, 0, 0]);
        assert_eq!(vy_norm_free(handle), 0);
        assert_eq!(vy_norm_free(handle), -2);
        let (ptr, len) = raw("A");
        assert_eq!(
            unsafe { vy_norm_observe(handle, ptr, len, 7, 107, 8.0, 0) },
            -2
        );
        assert_eq!(
            unsafe { vy_norm_last_document(handle, std::ptr::null_mut(), 0) },
            -2
        );
    }

    fn health(handle: i64) -> String {
        drain_i32(
            unsafe { vy_norm_last_health(handle, std::ptr::null_mut(), 0) },
            |buf, cap| unsafe { vy_norm_last_health(handle, buf, cap) },
        )
    }

    #[test]
    fn heartbeats_clear_staleness_through_the_abi() {
        let handle = vy_norm_new(4, 60.0, 30.0);
        let (ptr, len) = raw("A");
        assert_eq!(health(handle), "", "no verdict before the first check");
        let beat = |now: f64| unsafe { vy_norm_observe(handle, ptr, len, 0, 0, now, 1) };
        assert_eq!(beat(100.0), 0);
        assert_eq!(
            drain_i32(
                unsafe { vy_norm_stats(handle, std::ptr::null_mut(), 0) },
                |buf, cap| unsafe { vy_norm_stats(handle, buf, cap) }
            ),
            "0,0,0,0,1,0"
        );
        assert_eq!(unsafe { vy_norm_check_health(handle, ptr, len, 131.0) }, 0);
        assert_eq!(health(handle), "0|heartbeat timeout");
        assert_eq!(unsafe { vy_norm_is_stale(handle, ptr, len) }, 1);
        assert_eq!(beat(200.0), 0);
        assert_eq!(
            drain_i32(
                unsafe { vy_norm_stats(handle, std::ptr::null_mut(), 0) },
                |buf, cap| unsafe { vy_norm_stats(handle, buf, cap) }
            ),
            "0,0,0,0,2,1"
        );
        assert_eq!(unsafe { vy_norm_is_stale(handle, ptr, len) }, 0);
        assert_eq!(unsafe { vy_norm_check_health(handle, ptr, len, 200.0) }, 0);
        assert_eq!(health(handle), "1|ok");
        assert_eq!(vy_norm_free(handle), 0);
    }

    #[test]
    fn an_unusable_gate_config_never_gets_a_handle() {
        assert_eq!(vy_norm_new(0, 60.0, 30.0), 0);
        assert_eq!(vy_norm_new(-1, 60.0, 30.0), 0);
        assert_eq!(vy_norm_new(4, f64::NAN, 30.0), 0);
        assert_eq!(vy_norm_new(4, 60.0, f64::INFINITY), 0);
        let (ptr, len) = raw("A");
        assert_eq!(unsafe { vy_norm_expected_seq(9999, ptr, len) }, -2);
        assert_eq!(unsafe { vy_norm_stats(9999, std::ptr::null_mut(), 0) }, -2);
        assert_eq!(unsafe { vy_norm_check_health(9999, ptr, len, 1.0) }, -2);
        assert_eq!(
            unsafe { vy_norm_last_health(9999, std::ptr::null_mut(), 0) },
            -2
        );
        assert_eq!(unsafe { vy_norm_is_stale(9999, ptr, len) }, -2);
        // An undecodable symbol is rejected before the gate is touched.
        assert_eq!(
            unsafe { vy_norm_observe(9999, b"\xff".as_ptr(), 1, 1, 1, 1.0, 0) },
            -1
        );
    }
}

// ── market bar metrics ────────────────────────────────────────────────────
//
// `market::return_pct` is the single owner of the `(close − open) / open`
// candle-change rule; `market::Bar::return_pct` and this export both route
// to it, so the Python boundary carries the answer and never the arithmetic.

/// Intraday change of one candle, in percent. A zero open answers `0.0`.
#[no_mangle]
pub extern "C" fn vy_bar_return_pct(open: f64, close: f64) -> f64 {
    market::return_pct(open, close)
}

#[cfg(test)]
mod market_bar_ffi_tests {
    use super::vy_bar_return_pct;

    #[test]
    fn the_export_and_the_bar_share_one_change_rule() {
        assert_eq!(vy_bar_return_pct(100.0, 105.0), 5.0);
        assert_eq!(vy_bar_return_pct(100.0, 92.5), -7.5);
        assert_eq!(
            super::market::Bar::new("TEST", "2026-01-06 09:30:00", 100.0, 106.0, 99.0, 105.0, 10)
                .return_pct(),
            vy_bar_return_pct(100.0, 105.0)
        );
    }

    #[test]
    fn a_flat_open_never_divides() {
        assert_eq!(vy_bar_return_pct(0.0, 0.0), 0.0);
        assert_eq!(vy_bar_return_pct(0.0, 500.0), 0.0);
    }
}

// ── execution intent identity ─────────────────────────────────────────────
//
// `execution::make_intent_id` is the single owner of the idempotency key
// shape; Python asks the kernel for the string instead of restating the
// format on its side of the boundary.

/// Deterministic intent id for one input event, written into a caller buffer.
///
/// Two-call string contract: `buf`/`cap` may be `(null, 0)` to probe the byte
/// length, then a buffer of `length + 1` reads the text back.
///
/// # Safety
/// `strategy_id`/`strategy_version` must be null or point to their
/// (non-negative) readable lengths; `buf` must be null or point to `cap`
/// writable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_exec_intent_id(
    strategy_id: *const u8,
    strategy_id_len: i64,
    strategy_version: *const u8,
    strategy_version_len: i64,
    event_seq: i64,
    intent_seq: i64,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    let strategy_id = match rs_bound(strategy_id, strategy_id_len) {
        Ok(Some(text)) => text,
        Ok(None) => return -2,
        Err(code) => return code,
    };
    let strategy_version = match rs_bound(strategy_version, strategy_version_len) {
        Ok(Some(text)) => text,
        Ok(None) => return -2,
        Err(code) => return code,
    };
    let id = execution::make_intent_id(strategy_id, strategy_version, event_seq, intent_seq);
    ks_write(&id, buf, cap)
}

#[cfg(test)]
mod exec_identity_ffi_tests {
    use super::vy_exec_intent_id;

    fn text(value: &str) -> (*const u8, i64) {
        (value.as_ptr(), value.len() as i64)
    }

    fn read(strategy_id: &str, strategy_version: &str, event_seq: i64, intent_seq: i64) -> String {
        let (id_ptr, id_len) = text(strategy_id);
        let (v_ptr, v_len) = text(strategy_version);
        let needed = unsafe {
            vy_exec_intent_id(
                id_ptr,
                id_len,
                v_ptr,
                v_len,
                event_seq,
                intent_seq,
                std::ptr::null_mut(),
                0,
            )
        };
        assert!(needed > 0);
        let mut buf = vec![0u8; (needed as usize) + 1];
        let written = unsafe {
            vy_exec_intent_id(
                id_ptr,
                id_len,
                v_ptr,
                v_len,
                event_seq,
                intent_seq,
                buf.as_mut_ptr(),
                buf.len(),
            )
        };
        assert_eq!(written, needed);
        String::from_utf8(buf[..written as usize].to_vec()).unwrap()
    }

    #[test]
    fn the_kernel_spells_the_idempotency_key() {
        assert_eq!(read("s", "1.0", 10, 1), "s:1.0:10:1");
        assert_eq!(
            read("scalp", "v2", -3, 0),
            super::execution::make_intent_id("scalp", "v2", -3, 0)
        );
    }

    #[test]
    fn a_missing_segment_is_bridge_misuse_not_an_id() {
        // Negative length = Python `None`: rejected, never formatted as "None".
        assert_eq!(
            unsafe {
                vy_exec_intent_id(
                    std::ptr::null(),
                    -1,
                    text("1.0").0,
                    text("1.0").1,
                    1,
                    1,
                    std::ptr::null_mut(),
                    0,
                )
            },
            -2
        );
    }
}

// ── download calendar + coverage ──────────────────────────────────────────
//
// `download.rs` is the single owner of the data-processing calendar and
// coverage rules. Datetimes cross as naive wall-clock seconds — neither side
// interprets a timezone, the caller's calendar convention is already baked
// into the number — and holidays cross as one NUL-joined list of
// `"YYYY-MM-DD"` labels, a separator a holiday date cannot contain.
// `i64::MIN` stands for Python's `None`.

/// Absent timestamp sentinel: Python's `None`.
const NO_TS: i64 = i64::MIN;

/// NUL-joined holiday labels → the kernel's set. `Ok(empty)` for `""`.
///
/// # Safety
/// `blob` must be null or point to `len` readable bytes.
unsafe fn rs_holidays(blob: *const u8, len: i64) -> Result<std::collections::HashSet<String>, i32> {
    let text = match rs_bound(blob, len) {
        Ok(Some(text)) => text,
        Ok(None) => return Err(-2),
        Err(code) => return Err(code),
    };
    Ok(text
        .split('\0')
        .filter(|label| !label.is_empty())
        .map(str::to_string)
        .collect())
}

/// Whole-day floor of a naive-seconds value.
fn day_of(secs: i64) -> i64 {
    secs.div_euclid(download::SECONDS_PER_DAY)
}

/// Trading-hours gate: is `now_secs` inside the inclusive `HH:MM` window on a
/// weekday? The closing minute's own seconds already fall outside it.
#[no_mangle]
pub extern "C" fn vy_cal_market_open(
    now_secs: i64,
    open_h: i64,
    open_m: i64,
    close_h: i64,
    close_m: i64,
) -> i32 {
    let day = day_of(now_secs);
    download::market_open(
        download::weekday(day) as i64,
        now_secs - day * download::SECONDS_PER_DAY,
        open_h * 3600 + open_m * 60,
        close_h * 3600 + close_m * 60,
    ) as i32
}

/// Trading day: a weekday whose date is not in the holiday set.
///
/// # Safety
/// `holidays` must be null or point to `holidays_len` readable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_cal_is_trading_day(
    day_secs: i64,
    holidays: *const u8,
    holidays_len: i64,
) -> i32 {
    match rs_holidays(holidays, holidays_len) {
        Ok(set) => i32::from(download::is_trading_day(day_of(day_secs), &set)),
        Err(code) => code,
    }
}

/// Trading days in `[from_secs, to_secs)`, stepping whole days.
/// `-1` means the holiday blob was unusable.
///
/// # Safety
/// `holidays` must be null or point to `holidays_len` readable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_cal_count_trading_days(
    from_secs: i64,
    to_secs: i64,
    holidays: *const u8,
    holidays_len: i64,
) -> i64 {
    match rs_holidays(holidays, holidays_len) {
        Ok(set) => download::count_trading_days(from_secs, to_secs, &set) as i64,
        Err(_) => -1,
    }
}

/// Midnight `max_history_years * 365` days before the caller's today.
#[no_mangle]
pub extern "C" fn vy_cal_target_start(today_secs: i64, max_history_years: i64) -> i64 {
    download::target_start(day_of(today_secs), max_history_years)
}

/// End of the caller's today: 23:59:59.
#[no_mangle]
pub extern "C" fn vy_cal_today_end(today_secs: i64) -> i64 {
    download::today_end(day_of(today_secs))
}

/// Coverage percent of a scanned database.
#[no_mangle]
pub extern "C" fn vy_dl_coverage_pct(trading_days: i64, max_history_years: i64) -> f64 {
    download::coverage_pct(trading_days.max(0) as u64, max_history_years.max(0) as u64)
}

/// Coverage verdict for one scanned database, written as a one-line document:
/// `STATE|missing_head|missing_tail|coverage_pct|listing_start_verified`.
///
/// # Safety
/// `boundary_date`, `holidays` must be null or point to their (non-negative)
/// readable lengths; `buf` must be null or point to `cap` writable bytes.
#[no_mangle]
#[allow(clippy::too_many_arguments)]
pub unsafe extern "C" fn vy_dl_decide_coverage(
    row_count: i64,
    trading_days: i64,
    corrupt: i64,
    earliest: i64,
    latest: i64,
    boundary_date: *const u8,
    boundary_len: i64,
    boundary_verified: i64,
    target_start: i64,
    today_end: i64,
    max_history_years: i64,
    head_tolerance: i64,
    tail_lag: i64,
    holidays: *const u8,
    holidays_len: i64,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    let holidays = match rs_holidays(holidays, holidays_len) {
        Ok(set) => set,
        Err(code) => return code,
    };
    // An empty or absent label means the database has no LISTING_START row;
    // only a stored flag of exactly 1 counts as verified.
    let boundary = match rs_bound(boundary_date, boundary_len) {
        Ok(Some(label)) if !label.is_empty() => Some((label.to_string(), boundary_verified == 1)),
        Ok(_) => None,
        Err(code) => return code,
    };
    let observed = download::ScanObserved {
        row_count: row_count.max(0) as u64,
        trading_days: trading_days.max(0) as u64,
        earliest: (earliest != NO_TS).then_some(earliest),
        latest: (latest != NO_TS).then_some(latest),
        corrupt: corrupt.max(0) as u64,
        boundary,
    };
    let params = download::CoverageParams {
        target_start,
        today_end,
        max_history_years: max_history_years.max(0) as u64,
        head_tolerance_trading_days: head_tolerance.max(0) as u64,
        tail_lag_tolerance_days: tail_lag,
        holidays: &holidays,
    };
    let decision = download::decide_coverage(&observed, &params);
    let document = format!(
        "{}|{}|{}|{}|{}",
        decision.state.name(),
        i32::from(decision.missing_head),
        i32::from(decision.missing_tail),
        decision.coverage_pct,
        i32::from(decision.listing_start_verified),
    );
    ks_write(&document, buf, cap)
}

#[cfg(test)]
mod download_calendar_ffi_tests {
    use super::*;

    const DAY: i64 = download::SECONDS_PER_DAY;

    fn midnight(label: &str) -> i64 {
        download::parse_date(label).unwrap() * DAY
    }

    #[test]
    fn the_closing_minutes_own_seconds_already_close_the_window() {
        let monday = midnight("2026-08-10");
        assert_eq!(vy_cal_market_open(monday + 10 * 3600, 9, 15, 12, 40), 1);
        assert_eq!(
            vy_cal_market_open(monday + 12 * 3600 + 40 * 60, 9, 15, 12, 40),
            1
        );
        assert_eq!(
            vy_cal_market_open(monday + 12 * 3600 + 40 * 60 + 1, 9, 15, 12, 40),
            0,
            "12:40:01 is already past the 12:40 close"
        );
        assert_eq!(
            vy_cal_market_open(midnight("2026-08-15") + 10 * 3600, 9, 15, 12, 40),
            0,
            "Saturday"
        );
    }

    #[test]
    fn the_holiday_set_moves_the_trading_day_and_the_count() {
        let monday = midnight("2026-08-10");
        let friday = midnight("2026-08-15");
        // Length 0 on a real buffer is an empty set; NULL is bridge misuse.
        let empty = b"x";
        assert_eq!(
            unsafe { vy_cal_is_trading_day(monday, empty.as_ptr(), 0) },
            1
        );
        assert_eq!(
            unsafe { vy_cal_count_trading_days(monday, friday, empty.as_ptr(), 0) },
            5
        );
        assert_eq!(
            unsafe { vy_cal_is_trading_day(monday, std::ptr::null(), 0) },
            -2
        );
        let blob = b"2026-08-10";
        assert_eq!(
            unsafe { vy_cal_is_trading_day(monday, blob.as_ptr(), blob.len() as i64) },
            0
        );
        assert_eq!(
            unsafe { vy_cal_count_trading_days(monday, friday, blob.as_ptr(), blob.len() as i64) },
            4
        );
        // Undecodable labels are rejected, never treated as "no holidays".
        let junk = [0xffu8];
        assert_eq!(
            unsafe { vy_cal_count_trading_days(monday, friday, junk.as_ptr(), 1) },
            -1
        );
    }

    #[allow(clippy::too_many_arguments)]
    fn verdict(
        row_count: i64,
        trading_days: i64,
        corrupt: i64,
        earliest: i64,
        latest: i64,
        boundary: Option<&str>,
        target_start: i64,
        today_end: i64,
        years: i64,
    ) -> String {
        let holidays = b"2026-01-26";
        let label = boundary.unwrap_or("").as_bytes();
        let invoke = |buf: *mut u8, cap: usize| unsafe {
            vy_dl_decide_coverage(
                row_count,
                trading_days,
                corrupt,
                earliest,
                latest,
                label.as_ptr(),
                boundary.map_or(-1, |b| b.len() as i64),
                1,
                target_start,
                today_end,
                years,
                5,
                5,
                holidays.as_ptr(),
                holidays.len() as i64,
                buf,
                cap,
            )
        };
        let needed = invoke(std::ptr::null_mut(), 0);
        assert!(needed > 0, "verdict rejected: {needed}");
        let mut buf = vec![0u8; needed as usize + 1];
        assert_eq!(invoke(buf.as_mut_ptr(), buf.len()), needed);
        String::from_utf8(buf[..needed as usize].to_vec()).unwrap()
    }

    #[test]
    fn an_empty_database_is_not_started_regardless_of_counters() {
        assert_eq!(
            verdict(0, 900, 3, NO_TS, NO_TS, None, midnight("2016-01-01"), 0, 10),
            "NOT_STARTED|0|0|0|0"
        );
    }

    #[test]
    fn a_verified_listing_boundary_suppresses_the_head_gap_only() {
        let today_end = midnight("2026-08-20") + 86_399;
        let target = midnight("2016-08-21");
        // Recent listing: the head gap is real until the boundary vouches for it.
        let missing = verdict(
            100,
            20,
            0,
            midnight("2026-08-10"),
            midnight("2026-08-19"),
            None,
            target,
            today_end,
            10,
        );
        assert_eq!(missing.split('|').next().unwrap(), "PARTIAL_DOWNLOAD");
        assert_eq!(missing.split('|').nth(1).unwrap(), "1");
        let verified = verdict(
            100,
            20,
            0,
            midnight("2026-08-10"),
            midnight("2026-08-19"),
            Some("2026-08-10"),
            target,
            today_end,
            10,
        );
        assert_eq!(verified.split('|').nth(1).unwrap(), "0");
        assert_eq!(verified.split('|').next_back().unwrap(), "1");
        assert_eq!(
            verified.split('|').nth(3).unwrap().parse::<f64>().unwrap(),
            vy_dl_coverage_pct(20, 10)
        );
    }
}

// ── download storage + planning ───────────────────────────────────────────
//
// Storage normalisation and sweep/queue planning rules from `download.rs`.
// A stored `candle_time` is the sort key of the whole database, so its shape is
// a rule, not a formatting detail; it, the filename token, the chunk plan and
// the queued ranges all have exactly one owner here.
// Text outputs follow the two-call `ks_write` convention.

/// Canonical `candle_time` from provider text: seconds and ISO separators
/// stripped, never reordered.
///
/// # Safety
/// `raw` must point to `raw_len` readable bytes; `buf` must be null or point to
/// `cap` writable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_ts_candle_time(
    raw: *const u8,
    raw_len: i64,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    let text = match rs_text(raw, raw_len) {
        Ok(text) => text,
        Err(code) => return code,
    };
    ks_write(&download::normalise_ts_str(text), buf, cap)
}

/// Canonical `candle_time` for a wall-clock timestamp: its own seconds are
/// dropped, everything else is preserved.
///
/// # Safety
/// `buf` must be null or point to `cap` writable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_ts_from_secs(secs: i64, buf: *mut u8, cap: usize) -> i32 {
    ks_write(&download::candle_time_from_secs(secs), buf, cap)
}

/// The `[A-Za-z0-9_]` token a symbol becomes on disk.
///
/// # Safety
/// `symbol` must point to `symbol_len` readable bytes; `buf` must be null or
/// point to `cap` writable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_sym_filename(
    symbol: *const u8,
    symbol_len: i64,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    let text = match rs_text(symbol, symbol_len) {
        Ok(text) => text,
        Err(code) => return code,
    };
    ks_write(&download::sanitize_symbol(text), buf, cap)
}

/// How many chunk windows a sweep from `from_secs` to `to_secs` will run.
#[no_mangle]
pub extern "C" fn vy_dl_chunk_count(from_secs: i64, to_secs: i64, chunk_days: i64) -> i64 {
    download::count_chunks(from_secs, to_secs, chunk_days) as i64
}

/// The sweep's `(start, end)` windows as `start|end;start|end;…`.
///
/// # Safety
/// `buf` must be null or point to `cap` writable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_dl_chunk_windows(
    from_secs: i64,
    to_secs: i64,
    chunk_days: i64,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    let document = download::chunk_windows(from_secs, to_secs, chunk_days)
        .iter()
        .map(|(start, end)| format!("{start}|{end}"))
        .collect::<Vec<_>>()
        .join(";");
    ks_write(&document, buf, cap)
}

/// May a finished head sweep write the `LISTING_START` boundary? `NO_TS` stands
/// for an empty database on either side.
#[no_mangle]
pub extern "C" fn vy_dl_head_sweep_eligible(new_rows: i64, before: i64, after: i64) -> i32 {
    i32::from(download::head_sweep_eligible(
        new_rows.max(0) as usize,
        (before != NO_TS).then_some(before),
        (after != NO_TS).then_some(after),
    ))
}

/// The range one queued job covers, as `from|to|reason`. `kind` is `0` for a
/// symbol with nothing on disk, `1` for a head gap, `2` for a tail gap;
/// `bound` is the observed DB edge, or [`NO_TS`] when there is none.
///
/// # Safety
/// `buf` must be null or point to `cap` writable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_dl_job_window(
    kind: i32,
    bound: i64,
    target_start: i64,
    today_end: i64,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    let kind = match download::JobKind::from_i32(kind) {
        Some(kind) => kind,
        None => return -2,
    };
    let (from, to, reason) = download::job_window(
        kind,
        (bound != NO_TS).then_some(bound),
        target_start,
        today_end,
    );
    ks_write(&format!("{from}|{to}|{reason}"), buf, cap)
}

#[cfg(test)]
mod download_storage_ffi_tests {
    use super::*;

    fn read_i32(invoke: impl Fn(*mut u8, usize) -> i32) -> String {
        let needed = invoke(std::ptr::null_mut(), 0);
        assert!(needed >= 0, "export rejected: {needed}");
        let mut buf = vec![0u8; needed as usize + 1];
        assert_eq!(invoke(buf.as_mut_ptr(), buf.len()), needed);
        String::from_utf8(buf[..needed as usize].to_vec()).unwrap()
    }

    fn candle_time(raw: &str) -> String {
        let bytes = raw.as_bytes();
        read_i32(|buf, cap| unsafe {
            vy_ts_candle_time(bytes.as_ptr(), bytes.len() as i64, buf, cap)
        })
    }

    #[test]
    fn the_candle_key_shape_is_the_kernels() {
        assert_eq!(candle_time("2026-08-10T09:15:30"), "2026-08-10 09:15:00");
        assert_eq!(candle_time("2026-08-10 09:15"), "2026-08-10 09:15");
        assert_eq!(candle_time(""), "");
        // A required segment that is NULL is bridge misuse, not an empty key.
        assert_eq!(
            unsafe { vy_ts_candle_time(std::ptr::null(), -1, std::ptr::null_mut(), 0) },
            -2
        );
        assert_eq!(
            read_i32(|buf, cap| unsafe { vy_ts_from_secs(0, buf, cap) }),
            download::candle_time_from_secs(0)
        );
    }

    #[test]
    fn the_filename_token_drops_everything_unsafe() {
        let bytes = "RELIANCE-EQ.!9".as_bytes();
        assert_eq!(
            read_i32(|buf, cap| unsafe {
                vy_sym_filename(bytes.as_ptr(), bytes.len() as i64, buf, cap)
            }),
            "RELIANCEEQ9"
        );
    }

    #[test]
    fn the_plan_reads_back_as_the_same_windows_the_count_promised() {
        let from = midnight_ffi("2026-01-01");
        let to = midnight_ffi("2026-09-01");
        let document = read_i32(|buf, cap| unsafe { vy_dl_chunk_windows(from, to, 200, buf, cap) });
        let windows: Vec<(i64, i64)> = document
            .split(';')
            .map(|pair| {
                let mut it = pair.split('|');
                (
                    it.next().unwrap().parse().unwrap(),
                    it.next().unwrap().parse().unwrap(),
                )
            })
            .collect();
        assert_eq!(
            windows.len() as i64,
            vy_dl_chunk_count(from, to, 200),
            "count and windows must agree"
        );
        assert_eq!(windows.last().unwrap().1, to);
        assert_eq!(
            read_i32(|buf, cap| unsafe { vy_dl_job_window(1, to, from, to, buf, cap) }),
            format!("{}|{}|PARTIAL: missing head coverage", from, to - 60)
        );
        assert_eq!(
            unsafe { vy_dl_job_window(7, to, from, to, std::ptr::null_mut(), 0) },
            -2,
            "an unknown kind is bridge misuse"
        );
    }

    #[test]
    fn a_boundary_needs_no_new_rows_and_an_earliest_that_did_not_move() {
        let day = download::SECONDS_PER_DAY;
        assert_eq!(vy_dl_head_sweep_eligible(0, NO_TS, NO_TS), 1);
        assert_eq!(
            vy_dl_head_sweep_eligible(0, day, day + 86_399),
            1,
            "same date"
        );
        assert_eq!(vy_dl_head_sweep_eligible(0, day, 2 * day), 0);
        assert_eq!(vy_dl_head_sweep_eligible(0, NO_TS, day), 0);
        assert_eq!(vy_dl_head_sweep_eligible(4, NO_TS, NO_TS), 0);
    }

    fn midnight_ffi(label: &str) -> i64 {
        download::parse_date(label).unwrap() * download::SECONDS_PER_DAY
    }
}

// ── broker resilience policy ─────────────────────────────────────────────
//
// `resilience` is the sole authority for how the system behaves when a venue
// misbehaves: which operations may be retried, the sliding-window quota, the
// clock-drift gate and the timeout/backoff/reconnect budgets. Python names the
// retry vocabulary and holds handles; it evaluates no rule.
//
// Classification/verdict exports return `0` or `1` (or the retry code). The
// `*_problem` exports answer with the *wording* of the rejection: `0` means
// the configuration is usable, `>0` is the byte length required for the reason
// (two-call probe/fill, exactly as the kill-switch text contract).
//
// Return codes otherwise: `-1` undecodable text, `-2` bridge misuse.

use resilience::Limiter;

/// Slot 0 backs handle 1, so a recycled slot can never alias a live limiter.
static RES_SLOTS: Mutex<Vec<Option<Limiter>>> = Mutex::new(Vec::new());

fn res_slots() -> MutexGuard<'static, Vec<Option<Limiter>>> {
    RES_SLOTS
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn res_index(handle: i64) -> Option<usize> {
    usize::try_from(handle.checked_sub(1)?).ok()
}

fn res_problem_text(problem: Option<&str>, buf: *mut u8, cap: usize) -> i32 {
    match problem {
        None => 0,
        Some(text) => unsafe { ks_write(text, buf, cap) },
    }
}

/// Retry class for one venue operation: `0` safe, `1` not safe, `2` reconcile
/// first. Unknown operations answer `1` (fail-closed classification).
///
/// # Safety
/// `operation` must point to `operation_len` readable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_res_retry_kind(operation: *const u8, operation_len: i64) -> i64 {
    match rs_text(operation, operation_len) {
        Ok(text) => resilience::retry_kind(text),
        Err(code) => code as i64,
    }
}

/// Why a throttle configuration is unusable (`0` = usable, `>0` = reason bytes).
#[no_mangle]
pub extern "C" fn vy_res_limiter_problem(
    max_requests: i64,
    window_seconds: f64,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    res_problem_text(
        resilience::limiter_problem(max_requests, window_seconds),
        buf,
        cap,
    )
}

/// Build a limiter handle, or `0` when the policy rejects the configuration.
#[no_mangle]
pub extern "C" fn vy_res_limiter_new(max_requests: i64, window_seconds: f64) -> i64 {
    let slot = Limiter::new(max_requests, window_seconds);
    if slot.is_none() {
        return 0;
    }
    let mut slots = res_slots();
    if let Some(free) = slots.iter().position(Option::is_none) {
        slots[free] = slot;
        return free as i64 + 1;
    }
    slots.push(slot);
    slots.len() as i64
}

/// Consume one quota unit: `1` allowed, `0` denied (the caller backs off).
#[no_mangle]
pub extern "C" fn vy_res_limiter_allow(handle: i64, now_epoch: f64) -> i32 {
    let Some(index) = res_index(handle) else {
        return -2;
    };
    match res_slots().get_mut(index) {
        Some(Some(limiter)) => i32::from(limiter.allow(now_epoch)),
        _ => -2,
    }
}

/// Requests currently inside the window, or `-2` for a bad handle.
#[no_mangle]
pub extern "C" fn vy_res_limiter_used(handle: i64) -> i64 {
    let Some(index) = res_index(handle) else {
        return -2;
    };
    match res_slots().get(index) {
        Some(Some(limiter)) => limiter.used(),
        _ => -2,
    }
}

/// Record a venue 429 (diagnostic counter only; never retries by itself).
#[no_mangle]
pub extern "C" fn vy_res_limiter_record_429(handle: i64) -> i32 {
    let Some(index) = res_index(handle) else {
        return -2;
    };
    match res_slots().get_mut(index) {
        Some(Some(limiter)) => {
            limiter.record_429();
            0
        }
        _ => -2,
    }
}

/// Recorded 429s, or `-2` for a bad handle.
#[no_mangle]
pub extern "C" fn vy_res_limiter_rejections(handle: i64) -> i64 {
    let Some(index) = res_index(handle) else {
        return -2;
    };
    match res_slots().get(index) {
        Some(Some(limiter)) => limiter.rejections(),
        _ => -2,
    }
}

#[no_mangle]
pub extern "C" fn vy_res_limiter_free(handle: i64) -> i32 {
    let Some(index) = res_index(handle) else {
        return -2;
    };
    let mut slots = res_slots();
    let freed = slots
        .get_mut(index)
        .is_some_and(|entry| entry.take().is_some());
    if freed {
        0
    } else {
        -2
    }
}

/// Clock-drift gate. `inputs_valid` carries only whether the bridge could read
/// the two epochs as numbers; the decision (including fail-closed) is here.
#[no_mangle]
pub extern "C" fn vy_res_clock_ok(
    local_epoch: f64,
    reference_epoch: f64,
    max_drift_seconds: f64,
    inputs_valid: i32,
) -> i32 {
    i32::from(resilience::clock_drift_ok(
        local_epoch,
        reference_epoch,
        max_drift_seconds,
        inputs_valid != 0,
    ))
}

/// Why a timeout budget is unusable, naming the first bad field
/// (`0` = usable, `>0` = reason bytes).
#[no_mangle]
pub extern "C" fn vy_res_timeout_problem(
    connect_seconds: f64,
    read_seconds: f64,
    submit_seconds: f64,
    reconcile_seconds: f64,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    let problem = resilience::timeout_problem(
        connect_seconds,
        read_seconds,
        submit_seconds,
        reconcile_seconds,
    );
    res_problem_text(problem.as_deref(), buf, cap)
}

/// Why a backoff configuration is unusable (`0` = usable, `>0` = reason bytes).
#[no_mangle]
pub extern "C" fn vy_res_backoff_problem(
    base_seconds: f64,
    factor: f64,
    max_seconds: f64,
    max_attempts: i64,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    res_problem_text(
        resilience::backoff_problem(base_seconds, factor, max_seconds, max_attempts),
        buf,
        cap,
    )
}

/// Delay before attempt `attempt` (1-indexed), capped at `max_seconds`.
#[no_mangle]
pub extern "C" fn vy_res_backoff_delay(
    base_seconds: f64,
    factor: f64,
    max_seconds: f64,
    attempt: i64,
) -> f64 {
    resilience::backoff_delay(base_seconds, factor, max_seconds, attempt)
}

/// Shared backoff/reconnect answer: are further attempts forbidden?
#[no_mangle]
pub extern "C" fn vy_res_exhausted(attempts_made: i64, max_attempts: i64) -> i32 {
    i32::from(resilience::exhausted(attempts_made, max_attempts))
}

/// Why a reconnect budget is unusable (`0` = usable, `>0` = reason bytes).
#[no_mangle]
pub extern "C" fn vy_res_reconnect_problem(max_attempts: i64, buf: *mut u8, cap: usize) -> i32 {
    res_problem_text(resilience::reconnect_problem(max_attempts), buf, cap)
}

#[cfg(test)]
mod resilience_ffi_tests {
    use super::*;

    fn read_i32(invoke: impl Fn(*mut u8, usize) -> i32) -> String {
        let needed = invoke(std::ptr::null_mut(), 0);
        assert!(needed >= 0, "export rejected: {needed}");
        let buf = vec![0u8; needed as usize + 1];
        assert_eq!(invoke(buf.as_ptr() as *mut u8, buf.len()), needed);
        String::from_utf8(buf[..needed as usize].to_vec()).unwrap()
    }

    fn kind(operation: &str) -> i64 {
        let bytes = operation.as_bytes();
        unsafe { vy_res_retry_kind(bytes.as_ptr(), bytes.len() as i64) }
    }

    #[test]
    fn the_retry_vocabulary_answers_through_one_export() {
        assert_eq!(kind("health"), resilience::RETRY_SAFE);
        assert_eq!(kind("place_order"), resilience::RETRY_NOT_SAFE);
        assert_eq!(kind("cancel_order"), resilience::RETRY_RECONCILE);
        assert_eq!(kind("never_seen"), resilience::RETRY_NOT_SAFE);
        assert_eq!(unsafe { vy_res_retry_kind(std::ptr::null(), 4) }, -2);
        assert_eq!(unsafe { vy_res_retry_kind(std::ptr::null(), -1) }, -2);
    }

    #[test]
    fn unusable_budgets_come_back_with_rust_owned_wording() {
        assert_eq!(vy_res_limiter_problem(10, 60.0, std::ptr::null_mut(), 0), 0);
        assert_eq!(
            read_i32(|buf, cap| vy_res_limiter_problem(0, 60.0, buf, cap)),
            "max_requests must be positive"
        );
        assert_eq!(
            read_i32(|buf, cap| vy_res_timeout_problem(10.0, 0.0, 10.0, 30.0, buf, cap)),
            "read_seconds must be positive"
        );
        assert_eq!(
            read_i32(|buf, cap| vy_res_backoff_problem(1.0, 0.5, 30.0, 5, buf, cap)),
            "backoff requires positive base/max and factor >= 1"
        );
        assert_eq!(
            read_i32(|buf, cap| vy_res_reconnect_problem(0, buf, cap)),
            "reconnect max_attempts must be >= 1"
        );
    }

    #[test]
    fn a_handle_owns_the_window_and_an_invalid_one_never_exists() {
        assert_eq!(vy_res_limiter_new(0, 60.0), 0, "policy rejects the config");
        let handle = vy_res_limiter_new(2, 10.0);
        assert!(handle > 0);
        assert_eq!(vy_res_limiter_allow(handle, 100.0), 1);
        assert_eq!(vy_res_limiter_allow(handle, 101.0), 1);
        assert_eq!(vy_res_limiter_used(handle), 2);
        assert_eq!(vy_res_limiter_allow(handle, 102.0), 0);
        assert_eq!(vy_res_limiter_allow(handle, 110.5), 1, "the window slid");
        assert_eq!(vy_res_limiter_record_429(handle), 0);
        assert_eq!(vy_res_limiter_rejections(handle), 1);
        assert_eq!(vy_res_limiter_free(handle), 0);
        assert_eq!(vy_res_limiter_free(handle), -2);
        assert_eq!(vy_res_limiter_allow(handle, 120.0), -2, "freed aliasing");
        assert_eq!(vy_res_limiter_used(9_999), -2);
        assert_eq!(vy_res_limiter_record_429(-5), -2);
    }

    #[test]
    fn drift_and_backoff_budgets_answer_directly() {
        assert_eq!(vy_res_clock_ok(100.0, 104.0, 5.0, 1), 1);
        assert_eq!(vy_res_clock_ok(100.0, 106.0, 5.0, 1), 0);
        assert_eq!(vy_res_clock_ok(100.0, 100.0, 5.0, 0), 0, "unusable input");
        assert_eq!(vy_res_backoff_delay(1.0, 2.0, 30.0, 5), 16.0);
        assert_eq!(vy_res_backoff_delay(1.0, 2.0, 30.0, 60), 30.0);
        assert_eq!(
            vy_res_backoff_delay(1.0, 2.0, 30.0, 4_096),
            30.0,
            "an overshooting attempt still gets the capped delay"
        );
        assert_eq!(vy_res_exhausted(5, 5), 1);
        assert_eq!(vy_res_exhausted(4, 5), 0);
    }
}

// ── live readiness gates + activation ceremony ────────────────────────────
//
// `live_readiness` owns every LIVE verdict: credential and identity reasons,
// risk-policy and funds wording, the five-gate assembly and the twelve-step
// ceremony. Python only collects facts it must gather by IO (adapter health,
// account identity, secret resolvability, kill state) and materialises the
// frozen dataclasses from the returned document.
//
// Outbound framing: a document is a sequence of length-prefixed fields,
// `<byte length>:<text>`, concatenated with no separator. A reason containing
// `:`, `|` or a newline therefore still frames correctly.
//
// Inbound framing: a string array arrives as `count` plus a blob of items
// joined by NUL with a trailing NUL. A count that disagrees with the blob is
// bridge misuse.
//
// Return codes: `>=0` byte length of the document, `-1` undecodable text,
// `-2` bridge misuse (bad array framing, unusable handle).

/// One outbound document under construction.
struct Doc {
    out: String,
}

impl Doc {
    fn field(&mut self, text: &str) {
        self.out.push_str(&text.len().to_string());
        self.out.push(':');
        self.out.push_str(text);
    }

    fn flag(&mut self, value: bool) {
        self.field(if value { "1" } else { "0" });
    }

    fn number(&mut self, value: i64) {
        self.field(&value.to_string());
    }

    fn fields(texts: &[String]) -> Doc {
        let mut doc = Doc { out: String::new() };
        for text in texts {
            doc.field(text);
        }
        doc
    }
}

/// Read a `count`-item NUL-joined array. Empty counts accept an empty blob.
fn split_array<'a>(blob: &'a str, count: i64) -> Option<Vec<&'a str>> {
    let count = usize::try_from(count).ok()?;
    if count == 0 {
        return Some(Vec::new());
    }
    let items: Vec<&'a str> = blob.split('\0').collect();
    if items.len() != count + 1 || !blob.ends_with('\0') {
        return None;
    }
    Some(items[..count].to_vec())
}

macro_rules! rs_arg {
    ($ptr:expr, $len:expr) => {
        match rs_text($ptr, $len) {
            Ok(text) => text,
            Err(code) => return code,
        }
    };
}

macro_rules! rs_array {
    ($ptr:expr, $len:expr, $count:expr) => {
        match rs_text($ptr, $len) {
            Ok(blob) => match split_array(blob, $count) {
                Some(items) => items,
                None => return -2,
            },
            Err(code) => return code,
        }
    };
}

/// Credential validation: one reason field per failure, empty = ready.
///
/// # Safety
/// Every `*const u8` must be null or point to its (non-negative) length;
/// `resolvable` must point to `resolvable_count` readable `i8`s.
#[no_mangle]
#[allow(clippy::too_many_arguments)]
pub unsafe extern "C" fn vy_cer_credentials(
    account_id: *const u8,
    account_id_len: i64,
    environment: *const u8,
    environment_len: i64,
    expected_environment: *const u8,
    expected_environment_len: i64,
    key_refs: *const u8,
    key_refs_len: i64,
    key_refs_count: i64,
    resolvable: *const i8,
    resolvable_count: i64,
    require_secrets: i32,
    store_present: i32,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    let refs = rs_array!(key_refs, key_refs_len, key_refs_count);
    if resolvable_count != key_refs_count {
        return -2;
    }
    let flags: Vec<bool> = match slice(resolvable, resolvable_count.max(0) as usize) {
        items => items.iter().map(|flag| *flag != 0).collect(),
    };
    let reasons = live_readiness::credential_reasons(
        rs_arg!(account_id, account_id_len),
        rs_arg!(environment, environment_len),
        rs_arg!(expected_environment, expected_environment_len),
        &refs,
        &flags,
        require_secrets != 0,
        store_present != 0,
    );
    let doc = Doc::fields(&reasons);
    ks_write(&doc.out, buf, cap)
}

/// Account confirmation: one reason field per mismatch.
///
/// # Safety
/// Every `*const u8` must be null or point to its (non-negative) length.
#[no_mangle]
pub unsafe extern "C" fn vy_cer_account(
    account_id: *const u8,
    account_id_len: i64,
    environment: *const u8,
    environment_len: i64,
    expected_account_id: *const u8,
    expected_account_id_len: i64,
    expected_environment: *const u8,
    expected_environment_len: i64,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    let reasons = live_readiness::account_reasons(
        rs_arg!(account_id, account_id_len),
        rs_arg!(environment, environment_len),
        rs_arg!(expected_account_id, expected_account_id_len),
        rs_arg!(expected_environment, expected_environment_len),
    );
    let doc = Doc::fields(&reasons);
    ks_write(&doc.out, buf, cap)
}

/// Risk policy validity: the mask and its wording in one answer.
///
/// # Safety
/// `buf` must be null or point to `cap` writable bytes.
#[no_mangle]
#[allow(clippy::too_many_arguments)]
pub unsafe extern "C" fn vy_cer_risk(
    present: i64,
    max_position_qty: f64,
    max_order_qty: f64,
    max_notional: f64,
    max_exposure_pct: f64,
    daily_loss_limit: f64,
    strategy_loss_limit: f64,
    cooldown_seconds: f64,
    max_orders_per_day: i64,
    require_fresh_data_seconds: f64,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    let Ok(present) = u32::try_from(present) else {
        return -2;
    };
    let reasons = live_readiness::risk_reasons(
        present,
        max_position_qty,
        max_order_qty,
        max_notional,
        max_exposure_pct,
        daily_loss_limit,
        strategy_loss_limit,
        cooldown_seconds,
        max_orders_per_day,
        require_fresh_data_seconds,
    );
    let doc = Doc::fields(&reasons);
    ks_write(&doc.out, buf, cap)
}

/// Funds preconditions for LIVE: one reason field per unmet condition.
///
/// # Safety
/// `currency` must be null or point to `currency_len` readable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_cer_funds(
    available: f64,
    equity: f64,
    currency: *const u8,
    currency_len: i64,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    let reasons = live_readiness::funds_reasons(available, equity, rs_arg!(currency, currency_len));
    let doc = Doc::fields(&reasons);
    ks_write(&doc.out, buf, cap)
}

/// The five live gates: `ready`, `5`, then `name/passed/detail` per gate.
///
/// # Safety
/// Every `*const u8` must be null or point to its (non-negative) length.
#[no_mangle]
#[allow(clippy::too_many_arguments)]
pub unsafe extern "C" fn vy_cer_gates(
    adapter_present: i32,
    adapter_error: *const u8,
    adapter_error_len: i64,
    health_ok: i32,
    health_detail: *const u8,
    health_detail_len: i64,
    credentials_ok: i32,
    credential_reasons: *const u8,
    credential_reasons_len: i64,
    credential_reasons_count: i64,
    account_evaluated: i32,
    account_ok: i32,
    account_reasons: *const u8,
    account_reasons_len: i64,
    account_reasons_count: i64,
    risk_present: i32,
    risk_ok: i32,
    risk_reasons: *const u8,
    risk_reasons_len: i64,
    risk_reasons_count: i64,
    kill_halted: i32,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    let cred = rs_array!(
        credential_reasons,
        credential_reasons_len,
        credential_reasons_count
    );
    let account = rs_array!(account_reasons, account_reasons_len, account_reasons_count);
    let risk = rs_array!(risk_reasons, risk_reasons_len, risk_reasons_count);
    let (ready, gates) = live_readiness::evaluate_gates(
        adapter_present != 0,
        rs_arg!(adapter_error, adapter_error_len),
        health_ok != 0,
        rs_arg!(health_detail, health_detail_len),
        credentials_ok != 0,
        &cred,
        account_ok != 0,
        &account,
        account_evaluated != 0,
        risk_present != 0,
        risk_ok != 0,
        &risk,
        kill_halted != 0,
    );
    let mut doc = Doc { out: String::new() };
    doc.flag(ready);
    doc.number(gates.len() as i64);
    for gate in &gates {
        doc.field(gate.name);
        doc.flag(gate.passed);
        doc.field(&gate.detail);
    }
    ks_write(&doc.out, buf, cap)
}

/// The activation ceremony: `ready`, `12`, then `index/name/ok/detail` per
/// step, then `blocker count` and one field per blocker.
///
/// # Safety
/// Every `*const u8` must be null or point to its (non-negative) length.
#[no_mangle]
#[allow(clippy::too_many_arguments)]
pub unsafe extern "C" fn vy_cer_activation(
    broker_name: *const u8,
    broker_name_len: i64,
    environment: *const u8,
    environment_len: i64,
    credentials_ok: i32,
    credential_reasons: *const u8,
    credential_reasons_len: i64,
    credential_reasons_count: i64,
    account_ok: i32,
    account_reasons: *const u8,
    account_reasons_len: i64,
    account_reasons_count: i64,
    market_healthy: i32,
    market_reason: *const u8,
    market_reason_len: i64,
    funds_ok: i32,
    funds_reasons: *const u8,
    funds_reasons_len: i64,
    funds_reasons_count: i64,
    risk_ok: i32,
    risk_reasons: *const u8,
    risk_reasons_len: i64,
    risk_reasons_count: i64,
    verdict_present: i32,
    verdict_blocks: i32,
    verdict_reasons: *const u8,
    verdict_reasons_len: i64,
    verdict_reasons_count: i64,
    kill_halted: i32,
    gates_present: i32,
    gates_ready: i32,
    failed_gate_names: *const u8,
    failed_gate_names_len: i64,
    failed_gate_names_count: i64,
    failed_gate_details: *const u8,
    failed_gate_details_len: i64,
    failed_gate_details_count: i64,
    armed_value: *const u8,
    armed_value_len: i64,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    if failed_gate_names_count != failed_gate_details_count {
        return -2;
    }
    let cred = rs_array!(
        credential_reasons,
        credential_reasons_len,
        credential_reasons_count
    );
    let account = rs_array!(account_reasons, account_reasons_len, account_reasons_count);
    let funds = rs_array!(funds_reasons, funds_reasons_len, funds_reasons_count);
    let risk = rs_array!(risk_reasons, risk_reasons_len, risk_reasons_count);
    let verdict = rs_array!(verdict_reasons, verdict_reasons_len, verdict_reasons_count);
    let names = rs_array!(
        failed_gate_names,
        failed_gate_names_len,
        failed_gate_names_count
    );
    let details = rs_array!(
        failed_gate_details,
        failed_gate_details_len,
        failed_gate_details_count
    );
    let (ready, steps, blockers) = live_readiness::evaluate_ceremony(
        rs_arg!(broker_name, broker_name_len),
        rs_arg!(environment, environment_len),
        credentials_ok != 0,
        &cred,
        account_ok != 0,
        &account,
        market_healthy != 0,
        rs_arg!(market_reason, market_reason_len),
        funds_ok != 0,
        &funds,
        risk_ok != 0,
        &risk,
        verdict_present != 0,
        verdict_blocks != 0,
        &verdict,
        kill_halted != 0,
        gates_present != 0,
        gates_ready != 0,
        &names,
        &details,
        rs_arg!(armed_value, armed_value_len),
    );
    let mut doc = Doc { out: String::new() };
    doc.flag(ready);
    doc.number(steps.len() as i64);
    for step in &steps {
        doc.number(step.index);
        doc.field(step.name);
        doc.flag(step.ok);
        doc.field(&step.detail);
    }
    doc.number(blockers.len() as i64);
    for blocker in &blockers {
        doc.field(blocker);
    }
    ks_write(&doc.out, buf, cap)
}

// ── sandbox fill-script policy ────────────────────────────────────────────
//
// `SandboxBroker::settle` hands its scripted policy text to the kernel and
// applies the answer. The document is `kind`, `detail`, `has_number` and,
// when the number is present, the quantity in Python `repr` form.

/// One settle decision for a scripted policy string.
///
/// # Safety
/// `policy` must point to `policy_len` readable bytes; `buf` must be null or
/// point to `cap` writable bytes.
#[no_mangle]
pub unsafe extern "C" fn vy_sbx_settle_decision(
    policy: *const u8,
    policy_len: i64,
    buf: *mut u8,
    cap: usize,
) -> i32 {
    let script = rs_arg!(policy, policy_len);
    let (kind, detail, quantity) = sandbox_policy::settle_decision(script).parts();
    let mut doc = Doc { out: String::new() };
    doc.field(kind);
    doc.field(&detail);
    match quantity {
        None => doc.flag(false),
        Some(value) => {
            doc.flag(true);
            doc.field(&risk_engine::py_float(value));
        }
    }
    ks_write(&doc.out, buf, cap)
}

#[cfg(test)]
mod live_readiness_ffi_tests {
    use super::*;

    fn read(invoke: impl Fn(*mut u8, usize) -> i32) -> Vec<String> {
        let needed = invoke(std::ptr::null_mut(), 0);
        assert!(needed >= 0, "export rejected: {needed}");
        let buf = vec![0u8; needed as usize + 1];
        assert_eq!(invoke(buf.as_ptr() as *mut u8, buf.len()), needed);
        let mut fields = Vec::new();
        let mut rest = &buf[..needed as usize];
        while !rest.is_empty() {
            let colon = rest.iter().position(|byte| *byte == b':').unwrap();
            let count: usize = std::str::from_utf8(&rest[..colon])
                .unwrap()
                .parse()
                .unwrap();
            let (value, tail) = rest.split_at(colon + 1 + count);
            fields.push(String::from_utf8(value[colon + 1..].to_vec()).unwrap());
            rest = tail;
        }
        fields
    }

    fn text(value: &str) -> (*const u8, i64) {
        let bytes = value.as_bytes();
        (bytes.as_ptr(), bytes.len() as i64)
    }

    fn blob(items: &[&str]) -> String {
        let mut out = String::new();
        for item in items {
            out.push_str(item);
            out.push('\0');
        }
        out
    }

    #[allow(clippy::too_many_arguments)]
    fn credentials(
        account_id: &str,
        environment: &str,
        expected: &str,
        references: &[&str],
        flags: &[bool],
        require_secrets: bool,
        store_present: bool,
    ) -> Vec<String> {
        let joined = blob(references);
        let flags_bytes: Vec<i8> = flags.iter().map(|flag| i8::from(*flag)).collect();
        let account = text(account_id);
        let env = text(environment);
        let want = text(expected);
        let refs = text(&joined);
        read(|buf, cap| unsafe {
            vy_cer_credentials(
                account.0,
                account.1,
                env.0,
                env.1,
                want.0,
                want.1,
                refs.0,
                refs.1,
                references.len() as i64,
                flags_bytes.as_ptr(),
                flags.len() as i64,
                i32::from(require_secrets),
                i32::from(store_present),
                buf,
                cap,
            )
        })
    }

    #[test]
    fn credential_reasons_cross_as_their_own_framed_document() {
        assert!(credentials("a", "live", "", &["K"], &[true], true, true).is_empty());
        assert_eq!(
            credentials("", "live", "live", &["K"], &[false], true, true),
            vec!["missing account_id", "secret not resolvable: K"]
        );
        assert_eq!(
            credentials("a", "sandbox", "live", &[], &[], false, false),
            vec!["environment mismatch: credentials say 'sandbox', expected 'live'"]
        );
        assert_eq!(
            credentials("a", "live", "", &[], &[], true, false),
            vec![
                "no credential store configured",
                "no secret key_refs declared"
            ]
        );
    }

    #[test]
    fn a_reason_full_of_framers_still_frames() {
        let nasty = "boom:|line\nnext";
        assert_eq!(
            credentials("a", "live", "", &[nasty], &[false], true, true),
            vec![format!("secret not resolvable: {nasty}")]
        );
    }

    #[test]
    fn a_mismatched_array_count_is_bridge_misuse() {
        let joined = blob(&["A", "B"]);
        let refs = text(&joined);
        let account = text("a");
        let flags = [1i8, 0i8];
        assert_eq!(
            unsafe {
                vy_cer_credentials(
                    account.0,
                    account.1,
                    account.0,
                    account.1,
                    account.0,
                    account.1,
                    refs.0,
                    refs.1,
                    5,
                    flags.as_ptr(),
                    2,
                    1,
                    1,
                    std::ptr::null_mut(),
                    0,
                )
            },
            -2
        );
    }

    #[test]
    fn the_gate_document_reports_readiness_then_every_gate() {
        let cred = text("missing account_id\0");
        let empty = text("");
        let (adapter_error, health) = (text(""), text("not connected"));
        let gates = read(|buf, cap| unsafe {
            vy_cer_gates(
                1,
                adapter_error.0,
                adapter_error.1,
                0,
                health.0,
                health.1,
                0,
                cred.0,
                cred.1,
                1,
                0,
                0,
                empty.0,
                empty.1,
                0,
                0,
                0,
                empty.0,
                empty.1,
                0,
                1,
                buf,
                cap,
            )
        });
        assert_eq!(gates[0], "0");
        assert_eq!(gates[1], "5");
        assert_eq!(
            gates[2..],
            [
                "BROKER_ADAPTER_READY",
                "0",
                "not connected",
                "CREDENTIALS_READY",
                "0",
                "missing account_id",
                "ACCOUNT_CONFIRMED",
                "0",
                "no adapter to confirm against",
                "RISK_CONFIGURATION_VALID",
                "0",
                "no risk policy active",
                "EXECUTION_SAFETY_ENABLED",
                "0",
                "kill switch engaged",
            ]
        );
    }

    #[test]
    fn the_ceremony_documents_twelve_steps_and_its_blockers() {
        let empty = text("");
        let (broker, environment, armed) = (text("ubl"), text("live"), text("DISARMED"));
        let fields = read(|buf, cap| unsafe {
            vy_cer_activation(
                broker.0,
                broker.1,
                environment.0,
                environment.1,
                1,
                empty.0,
                empty.1,
                0,
                1,
                empty.0,
                empty.1,
                0,
                1,
                empty.0,
                empty.1,
                1,
                empty.0,
                empty.1,
                0,
                1,
                empty.0,
                empty.1,
                0,
                1,
                0,
                empty.0,
                empty.1,
                0,
                0,
                1,
                1,
                empty.0,
                empty.1,
                0,
                empty.0,
                empty.1,
                0,
                armed.0,
                armed.1,
                buf,
                cap,
            )
        });
        let step = |index: usize| {
            let at = 2 + (index - 1) * 4;
            (
                fields[at].as_str(),
                fields[at + 1].as_str(),
                fields[at + 2].as_str(),
                fields[at + 3].as_str(),
            )
        };
        assert_eq!(fields[0], "0", "a disarmed run is never ready");
        assert_eq!(fields[1], "12");
        assert_eq!(
            step(1),
            ("1", "SELECT_BROKER", "1", ""),
            "a named broker clears step one"
        );
        assert_eq!(step(10), ("10", "VERIFY_KILL_SWITCH", "1", ""));
        assert_eq!(
            step(11),
            ("11", "EXPLICIT_ARM", "0", "arming is DISARMED, not ARMED")
        );
        assert_eq!(
            step(12),
            ("12", "START_LIVE", "0", "blocked: ceremony not fully green")
        );
        assert_eq!(fields[50], "1", "one blocker: the arm step");
        assert_eq!(fields[51], "EXPLICIT_ARM: arming is DISARMED, not ARMED");
        assert_eq!(fields.len(), 52);
    }

    #[test]
    fn funds_and_risk_wording_arrives_from_the_kernel() {
        let currency = text("  ");
        assert_eq!(
            read(|buf, cap| unsafe { vy_cer_funds(0.0, -5.0, currency.0, currency.1, buf, cap) }),
            vec![
                "available capital not positive: 0.0",
                "equity not positive: -5.0",
                "funds currency missing"
            ]
        );
        assert!(read(|buf, cap| unsafe {
            vy_cer_risk(0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0, buf, cap)
        })
        .is_empty());
        assert_eq!(
            read(|buf, cap| unsafe {
                vy_cer_risk(0, 0.0, 9_999.0, 0.0, 0.0, 0.0, 0.0, -1.0, 0, 0.0, buf, cap)
            }),
            vec![
                "max_position_qty must be positive",
                "max_order_qty exceeds max_position_qty",
                "cooldown_seconds must not be negative",
            ]
        );
        assert_eq!(
            unsafe {
                vy_cer_risk(
                    -3,
                    1.0,
                    1.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0,
                    0.0,
                    std::ptr::null_mut(),
                    0,
                )
            },
            -2
        );
    }

    #[test]
    fn account_identity_confirms_or_refuses_live() {
        let (id, sandbox, live) = (text("sbx-1"), text("sandbox"), text("live"));
        assert!(read(|buf, cap| unsafe {
            vy_cer_account(
                id.0, id.1, sandbox.0, sandbox.1, id.0, id.1, sandbox.0, sandbox.1, buf, cap,
            )
        })
        .is_empty());
        assert_eq!(
            read(|buf, cap| unsafe {
                vy_cer_account(
                    id.0, id.1, sandbox.0, sandbox.1, id.0, id.1, live.0, live.1, buf, cap,
                )
            }),
            vec![
                "environment mismatch: adapter='sandbox' expected='live'",
                "sandbox account must never be treated as LIVE"
            ]
        );
        let none = text("");
        assert_eq!(
            read(|buf, cap| unsafe {
                vy_cer_account(
                    none.0, none.1, none.0, none.1, none.0, none.1, none.0, none.1, buf, cap,
                )
            }),
            vec!["adapter reports no account_id"]
        );
    }
}

#[cfg(test)]
mod sandbox_policy_ffi_tests {
    use super::*;

    fn decide(policy: &str) -> Vec<String> {
        let bytes = policy.as_bytes();
        let needed = unsafe {
            vy_sbx_settle_decision(bytes.as_ptr(), bytes.len() as i64, std::ptr::null_mut(), 0)
        };
        assert!(needed >= 0, "export rejected: {needed}");
        let buf = vec![0u8; needed as usize + 1];
        assert_eq!(
            unsafe {
                vy_sbx_settle_decision(
                    bytes.as_ptr(),
                    bytes.len() as i64,
                    buf.as_ptr() as *mut u8,
                    buf.len(),
                )
            },
            needed
        );
        let mut fields = Vec::new();
        let mut rest = &buf[..needed as usize];
        while !rest.is_empty() {
            let colon = rest.iter().position(|byte| *byte == b':').unwrap();
            let count: usize = std::str::from_utf8(&rest[..colon])
                .unwrap()
                .parse()
                .unwrap();
            let (value, tail) = rest.split_at(colon + 1 + count);
            fields.push(String::from_utf8(value[colon + 1..].to_vec()).unwrap());
            rest = tail;
        }
        fields
    }

    #[test]
    fn a_wait_carries_the_next_scripted_policy() {
        assert_eq!(decide("delay:3"), vec!["WAIT", "delay:2", "0"]);
        assert_eq!(decide("delay:0"), vec!["FULL", "", "0"]);
        assert_eq!(decide("full"), vec!["FULL", "", "0"]);
    }

    #[test]
    fn a_rejection_carries_the_reason_text() {
        assert_eq!(decide("reject:"), vec!["REJECT", "venue reject", "0"]);
        assert_eq!(
            decide("nonsense"),
            vec!["REJECT", "unknown policy: nonsense", "0"]
        );
        // Hostile framing characters inside the reason survive the document.
        assert_eq!(decide("reject:a:|b\nc"), vec!["REJECT", "a:|b\nc", "0"]);
    }

    #[test]
    fn a_partial_carries_the_quantity_in_repr_form() {
        assert_eq!(decide("partial:2.5"), vec!["PARTIAL", "", "1", "2.5"]);
        assert_eq!(decide("partial:1e-7"), vec!["PARTIAL", "", "1", "1e-07"]);
        assert_eq!(decide("partial:inf"), vec!["PARTIAL", "", "1", "inf"]);
        assert_eq!(decide("partial:-0.0"), vec!["PARTIAL", "", "1", "-0.0"]);
    }

    #[test]
    fn an_unusable_pointer_is_bridge_misuse() {
        assert_eq!(
            unsafe { vy_sbx_settle_decision(std::ptr::null(), 4, std::ptr::null_mut(), 0) },
            -2
        );
        assert_eq!(
            unsafe { vy_sbx_settle_decision(std::ptr::null(), -1, std::ptr::null_mut(), 0) },
            -2
        );
    }
}
