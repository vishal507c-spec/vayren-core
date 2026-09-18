//! Backtest directional/symbol derivation — pure post-processing twin of
//! `06_backtest/backtest/engine/directional.py`.
//!
//! Filters closed trades by side/symbol and recomputes the equity curve +
//! display report through the shared kernels ([`assemble_curve`],
//! [`assemble_report`]) — zero duplicate math, numbers identical to the
//! engine. The Python `directional` module remains the production path;
//! this module is its behavior-identical twin.
//!
//! Differences vs Python, all documented at the item:
//! - Timestamps stay caller-side (the kernels never see them); only PnL
//!   order and index selection live here.
//! - Invalid side: Python raises `ValueError`; Rust has no exceptions, so
//!   the boundary parses with [`Side::from_str`] (`None` = caller handles,
//!   e.g. reject the request). All filters below take a parsed [`Side`].
//! - `None` base: Python returns `None`/`{}`; the Rust caller holds the
//!   `Option` (no trade slice = no call). Empty subsets still produce the
//!   zero-trade report, exactly like `_symbol_view` on `()`.

use crate::backtest::{Side, TradeRecord};
use crate::backtest_engine::{assemble_curve, assemble_report, PerformanceReport};

/// Indices of `side` trades, in input order.
/// Mirrors `_filter_trades` (exact `"LONG"`/`"SHORT"` match on the side).
pub fn indices_for_side(trades: &[TradeRecord], side: Side) -> Vec<usize> {
    trades
        .iter()
        .enumerate()
        .filter(|(_, t)| t.side == side)
        .map(|(i, _)| i)
        .collect()
}

/// Indices of one symbol's trades, in input order.
/// Mirrors the `derive_symbol_result` filter (`TradeRecord.symbol` is the
/// single source of truth; exact string match).
pub fn indices_for_symbol(trades: &[TradeRecord], symbol: &str) -> Vec<usize> {
    trades
        .iter()
        .enumerate()
        .filter(|(_, t)| t.symbol == symbol)
        .map(|(i, _)| i)
        .collect()
}

/// Single-pass per-symbol grouping in requested-symbol order; a requested
/// symbol with no trades yields an empty group (which still assembles the
/// zero-trade report downstream). Mirrors `derive_symbol_results` (identical
/// math to per-symbol calls, one scan instead of N).
pub fn grouped_indices(trades: &[TradeRecord], symbols: &[&str]) -> Vec<Vec<usize>> {
    let mut groups: Vec<Vec<usize>> = symbols.iter().map(|_| Vec::new()).collect();
    for (i, trade) in trades.iter().enumerate() {
        for (slot, want) in symbols.iter().enumerate() {
            if trade.symbol == *want {
                groups[slot].push(i);
                break;
            }
        }
    }
    groups
}

/// Single-pass `(LONG indices, SHORT indices)` split, in input order.
/// Mirrors `split_by_side` (BUY == LONG, SELL == SHORT).
pub fn split_sides(trades: &[TradeRecord]) -> (Vec<usize>, Vec<usize>) {
    let mut longs = Vec::new();
    let mut shorts = Vec::new();
    for (i, trade) in trades.iter().enumerate() {
        match trade.side {
            Side::Long => longs.push(i),
            Side::Short => shorts.push(i),
        }
    }
    (longs, shorts)
}

/// PnLs + holding bars for a subset, in subset order (kernel inputs).
pub fn subset_series(trades: &[TradeRecord], indices: &[usize]) -> (Vec<f64>, Vec<f64>) {
    let mut pnls = Vec::with_capacity(indices.len());
    let mut held = Vec::with_capacity(indices.len());
    for &i in indices {
        pnls.push(trades[i].pnl);
        held.push(trades[i].bars_held as f64);
    }
    (pnls, held)
}

/// Full recompute for a subset: equity curve (starting point prepended) +
/// display report. Mirrors `_symbol_view` / `derive_directional_result`
/// assembly: `final = curve[-1]` (or `initial` when empty — the same value
/// `compute_metrics` reads off its curve).
pub fn derive_curve_report(
    trades: &[TradeRecord],
    indices: &[usize],
    initial: f64,
) -> (Vec<f64>, PerformanceReport) {
    let (pnls, held) = subset_series(trades, indices);
    let (equities, _) = assemble_curve(initial, &pnls);
    let final_equity = equities.last().copied().unwrap_or(initial);
    let report = assemble_report(&pnls, &held, initial, final_equity);
    (equities, report)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backtest::ExitReason;

    /// Fixture mirroring the parity probe (`Temp/opencode/directional_probe.py`):
    /// AAA/LONG/+50, BBB/SHORT/-20, AAA/SHORT/+30, capital 100_000.
    fn probe_trades() -> Vec<TradeRecord> {
        let mk = |symbol: &str, side: Side, day: usize, pnl: f64| TradeRecord {
            symbol: symbol.into(),
            side,
            entry_index: day,
            exit_index: day + 1,
            entry_time: format!("2026-01-{day:02} 09:15:00"),
            exit_time: format!("2026-01-{:02} 09:15:00", day + 1),
            entry_price: 100.0,
            exit_price: 100.0 + pnl,
            quantity: 10.0,
            pnl,
            pnl_pct: pnl,
            commission: 1.0,
            bars_held: 1,
            exit_reason: ExitReason::Signal,
            r_multiple: None,
        };
        vec![
            mk("AAA", Side::Long, 2, 50.0),
            mk("BBB", Side::Short, 3, -20.0),
            mk("AAA", Side::Short, 4, 30.0),
        ]
    }

    fn approx(a: f64, b: f64) -> bool {
        (a - b).abs() < 1e-9
    }

    #[test]
    fn side_filter_matches_python() {
        let trades = probe_trades();
        assert_eq!(indices_for_side(&trades, Side::Long), vec![0]);
        assert_eq!(indices_for_side(&trades, Side::Short), vec![1, 2]);
    }

    #[test]
    fn symbol_filter_matches_python() {
        let trades = probe_trades();
        assert_eq!(indices_for_symbol(&trades, "AAA"), vec![0, 2]);
        assert_eq!(indices_for_symbol(&trades, "BBB"), vec![1]);
        assert!(indices_for_symbol(&trades, "ZZZ").is_empty());
    }

    #[test]
    fn grouped_single_pass_matches_singular() {
        let trades = probe_trades();
        let grouped = grouped_indices(&trades, &["AAA", "BBB", "ZZZ"]);
        assert_eq!(grouped.len(), 3);
        assert_eq!(grouped[0], indices_for_symbol(&trades, "AAA"));
        assert_eq!(grouped[1], indices_for_symbol(&trades, "BBB"));
        assert_eq!(grouped[2], indices_for_symbol(&trades, "ZZZ"));
        // Requested order preserved even when input order differs.
        let regrouped = grouped_indices(&trades, &["BBB", "AAA"]);
        assert_eq!(regrouped[0], vec![1]);
        assert_eq!(regrouped[1], vec![0, 2]);
    }

    #[test]
    fn split_sides_matches_python() {
        let trades = probe_trades();
        let (longs, shorts) = split_sides(&trades);
        assert_eq!(longs, vec![0]);
        assert_eq!(shorts, vec![1, 2]);
    }

    #[test]
    fn long_report_matches_python() {
        // Probe LONG view: 1 trade, net 50, pf/sharpe None, dd 0.
        let trades = probe_trades();
        let idx = indices_for_side(&trades, Side::Long);
        let (equities, r) = derive_curve_report(&trades, &idx, 100_000.0);
        assert_eq!(equities, vec![100_000.0, 100_050.0]);
        assert_eq!(r.total_trades, 1);
        assert!(approx(r.net_profit, 50.0));
        assert!(approx(r.net_profit_pct, 0.05));
        assert_eq!(r.win_rate, Some(1.0));
        assert_eq!(r.profit_factor, None);
        assert_eq!((r.max_drawdown_pct, r.max_drawdown_abs), (0.0, 0.0));
        assert!(approx(r.avg_trade.unwrap(), 50.0));
        assert_eq!(r.expectancy, r.avg_trade);
        assert_eq!(r.sharpe_ratio, None);
        assert!(approx(r.gross_profit, 50.0));
        assert!(approx(r.gross_loss, 0.0));
        assert!(approx(r.ending_capital, 100_050.0));
    }

    #[test]
    fn short_report_matches_python() {
        // Probe SHORT view: -20/+30, net 10, pf 1.5, dd (0.02, 20.0).
        let trades = probe_trades();
        let idx = indices_for_side(&trades, Side::Short);
        let (equities, r) = derive_curve_report(&trades, &idx, 100_000.0);
        assert_eq!(equities, vec![100_000.0, 99_980.0, 100_010.0]);
        assert_eq!(r.total_trades, 2);
        assert!(approx(r.net_profit, 10.0));
        assert_eq!(r.win_rate, Some(0.5));
        assert!(approx(r.profit_factor.unwrap(), 1.5));
        assert!(approx(r.max_drawdown_pct, 0.02));
        assert!(approx(r.max_drawdown_abs, 20.0));
        assert!(approx(r.sharpe_ratio.unwrap(), 11.230360578039203));
        assert!(approx(r.gross_profit, 30.0));
        assert!(approx(r.gross_loss, -20.0));
        assert!(approx(r.ending_capital, 100_010.0));
    }

    #[test]
    fn aaa_report_matches_python() {
        // Probe AAA view: +50/+30, net 80, no losing trade.
        let trades = probe_trades();
        let idx = indices_for_symbol(&trades, "AAA");
        let (equities, r) = derive_curve_report(&trades, &idx, 100_000.0);
        assert_eq!(equities, vec![100_000.0, 100_050.0, 100_080.0]);
        assert_eq!(r.total_trades, 2);
        assert!(approx(r.net_profit, 80.0));
        assert_eq!(r.win_rate, Some(1.0));
        assert_eq!(r.profit_factor, None);
        assert!(approx(r.sharpe_ratio.unwrap(), 224.28923773524892));
        assert!(approx(r.ending_capital, 100_080.0));
    }

    #[test]
    fn zzz_zero_report_matches_python() {
        // Probe ZZZ view: no trades — zero report, capital preserved.
        let trades = probe_trades();
        let idx = indices_for_symbol(&trades, "ZZZ");
        let (equities, r) = derive_curve_report(&trades, &idx, 100_000.0);
        assert_eq!(equities, vec![100_000.0]);
        assert_eq!(r.total_trades, 0);
        assert_eq!(r.win_rate, None);
        assert_eq!(r.profit_factor, None);
        assert_eq!(r.avg_trade, None);
        assert_eq!(r.expectancy, None);
        assert_eq!(r.sharpe_ratio, None);
        assert_eq!((r.max_drawdown_pct, r.max_drawdown_abs), (0.0, 0.0));
        assert_eq!((r.gross_profit, r.gross_loss), (0.0, 0.0));
        assert!(approx(r.net_profit, 0.0));
        assert!(approx(r.ending_capital, 100_000.0));
    }

    #[test]
    fn grouped_reports_match_singular_reports() {
        // Grouped assembly == per-symbol assembly (probe asserts the same
        // equality on the Python side: trades, curve, metrics, bars_used).
        let trades = probe_trades();
        for symbol in ["AAA", "BBB", "ZZZ"] {
            let single_idx = indices_for_symbol(&trades, symbol);
            let grouped = grouped_indices(&trades, &[symbol]);
            assert_eq!(grouped[0], single_idx);
            let (e1, r1) = derive_curve_report(&trades, &single_idx, 100_000.0);
            let (e2, r2) = derive_curve_report(&trades, &grouped[0], 100_000.0);
            assert_eq!(e1, e2);
            assert_eq!(r1, r2);
        }
    }
}
