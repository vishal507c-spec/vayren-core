//! Backtest numeric kernels — the Rust-owned authority (constitution §1:
//! Backtesting / Numerical calculations / Performance-critical).
//!
//! These functions reproduce the exact IEEE-754 double semantics of the
//! original Python kernels (same iteration order, same comparison order), so
//! backtest metrics are bit-for-bit identical. Python retains only the domain
//! dataclass construction; the math lives here.

/// Maximum drawdown of an equity curve, matching the Python reference:
/// peak starts at the first point; `dd_pct` is guarded against a zero peak;
/// `max_pct` and `max_abs` are tracked independently (they may peak at
/// different points). Empty curve -> (0.0, 0.0).
pub fn max_drawdown(equities: &[f64]) -> (f64, f64) {
    let Some(&first) = equities.first() else {
        return (0.0, 0.0);
    };
    let mut peak = first;
    let mut max_pct = 0.0;
    let mut max_abs = 0.0;
    for &equity in equities {
        if equity > peak {
            peak = equity;
        }
        let dd_abs = peak - equity;
        let dd_pct = if peak != 0.0 {
            dd_abs / peak * 100.0
        } else {
            0.0
        };
        if dd_pct > max_pct {
            max_pct = dd_pct;
        }
        if dd_abs > max_abs {
            max_abs = dd_abs;
        }
    }
    (max_pct, max_abs)
}

/// Running equity + per-point drawdown percentage for a sequence of realized
/// trade PnLs, starting at `initial`. Returns `(equity_after_each_trade,
/// drawdown_pct_after_each_trade)` in trade order. The caller prepends the
/// starting point (it owns timestamps).
pub fn equity_curve(initial: f64, pnls: &[f64]) -> (Vec<f64>, Vec<f64>) {
    let mut equity = initial;
    let mut peak = initial;
    let mut equities = Vec::with_capacity(pnls.len());
    let mut drawdowns = Vec::with_capacity(pnls.len());
    for &pnl in pnls {
        equity += pnl;
        if equity > peak {
            peak = equity;
        }
        let dd_pct = if peak != 0.0 {
            (peak - equity) / peak * 100.0
        } else {
            0.0
        };
        equities.push(equity);
        drawdowns.push(dd_pct);
    }
    (equities, drawdowns)
}

/// Sharpe ratio of per-trade equity returns, annualized by mean holding
/// period (252 trading days x 25 bars/day over mean bars held). Returns
/// `None` when fewer than two usable returns or non-positive variance, exactly
/// as the Python reference did. `bars_held` is summed over ALL trades (not
/// just usable returns), matching the original.
pub fn sharpe(pnls: &[f64], bars_held: &[f64], initial: f64) -> Option<f64> {
    if pnls.len() < 2 {
        return None;
    }
    let mut equity = initial;
    let mut returns: Vec<f64> = Vec::with_capacity(pnls.len());
    for &pnl in pnls {
        let before = equity;
        equity += pnl;
        if before <= 0.0 {
            continue;
        }
        returns.push(equity / before - 1.0);
    }
    if returns.len() < 2 {
        return None;
    }
    let n = returns.len() as f64;
    let mean = returns.iter().sum::<f64>() / n;
    let variance = returns.iter().map(|r| (r - mean) * (r - mean)).sum::<f64>() / (n - 1.0);
    if variance <= 0.0 {
        return None;
    }
    let std = variance.sqrt();
    let trade_count = pnls.len() as f64;
    let mean_bars = if trade_count > 0.0 {
        bars_held.iter().sum::<f64>() / trade_count
    } else {
        1.0
    };
    let trades_per_year = (252.0 * 25.0) / mean_bars.max(1.0);
    let annual_factor = trades_per_year.sqrt();
    if std != 0.0 {
        Some((mean / std) * annual_factor)
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn approx(a: f64, b: f64) -> bool {
        (a - b).abs() < 1e-12
    }

    #[test]
    fn max_drawdown_empty_is_zero() {
        assert_eq!(max_drawdown(&[]), (0.0, 0.0));
    }

    #[test]
    fn max_drawdown_monotonic_up_is_zero() {
        assert_eq!(max_drawdown(&[100.0, 110.0, 120.0]), (0.0, 0.0));
    }

    #[test]
    fn max_drawdown_tracks_peak_and_trough() {
        let (pct, abs) = max_drawdown(&[100.0, 80.0, 90.0]);
        assert!(approx(abs, 20.0));
        assert!(approx(pct, 20.0));
    }

    #[test]
    fn max_drawdown_zero_peak_guard() {
        let (pct, abs) = max_drawdown(&[0.0, 0.0]);
        assert!(approx(pct, 0.0));
        assert!(approx(abs, 0.0));
    }

    #[test]
    fn equity_curve_accumulates_in_order() {
        let (eq, dd) = equity_curve(100.0, &[10.0, -30.0, 5.0]);
        assert_eq!(eq, vec![110.0, 80.0, 85.0]);
        assert!(approx(dd[0], 0.0));
        assert!(approx(dd[1], (110.0 - 80.0) / 110.0 * 100.0));
        assert!(approx(dd[2], (110.0 - 85.0) / 110.0 * 100.0));
    }

    #[test]
    fn sharpe_needs_two_returns() {
        assert!(sharpe(&[5.0], &[1.0], 100.0).is_none());
        assert!(sharpe(&[], &[], 100.0).is_none());
    }

    #[test]
    fn sharpe_zero_variance_is_none() {
        assert!(sharpe(&[0.0, 0.0], &[1.0, 1.0], 100.0).is_none());
    }

    #[test]
    fn sharpe_positive_when_returns_vary() {
        let value = sharpe(&[10.0, -5.0, 8.0], &[2.0, 3.0, 1.0], 100.0);
        assert!(value.is_some());
    }

    #[test]
    fn sharpe_skips_nonpositive_before() {
        // Equity driven to <=0 mid-series: those trades contribute no return.
        let value = sharpe(&[-200.0, 50.0, 50.0], &[1.0, 1.0, 1.0], 100.0);
        assert!(value.is_none());
    }
}
