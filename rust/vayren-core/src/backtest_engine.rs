//! Backtest run loop — the `execute_bars` core without the strategy callback.
//!
//! Rust port of the deterministic skeleton of
//! `06_backtest/backtest/runner.py::execute_bars` (canonical VM path):
//!
//! ```text
//! bar → (warmup: feed only) → SL/TP close? → signal → entry/exit → journal
//! leftover position → close at final bar ("END")
//! ```
//!
//! The strategy itself stays Python per constitution §2 (Python owns
//! Strategy/AI), so the per-bar signal comes from an injected
//! [`SignalSource`] — scripted in tests, strategy-driven in future Rust
//! consumers. The Python `execute_bars` remains the production path; this
//! module is its behavior-identical twin for the loop mechanics.
//!
//! Differences vs Python, all documented at the item:
//! - `warmup` is `usize` (Python clamps `max(0, ...)`; negative warmups do
//!   not exist in the type).
//! - Progress-report failures cannot happen (`FnMut` returns nothing); the
//!   Python `try/except: pass` around `on_progress` stays caller-side.
//! - `try_close` takes no `bar_open` (unused in Python, marked `noqa`).
//! - The final `END` close does not touch the realized accumulator —
//!   verbatim Python (harmless there too, the loop ends).
//!
//! Report assembly ([`assemble_curve`], [`assemble_report`]) mirrors
//! `engine/metrics.py::compute_equity_curve` / `compute_metrics` and reuses
//! the [`crate::metrics`] kernels — no duplicate math. Timestamps stay
//! caller-side (the kernels never see them).

use crate::backtest::{ExecutionSimulator, PositionManager, Side, TradeJournal, TradeRecord};
use crate::market::Bar;
use crate::metrics;

// ── run configuration ────────────────────────────────────────────────────

/// Immutable inputs to one isolated run (mirrors `BacktestConfig` fields the
/// loop reads; date/window selection stays caller-side via `slice_indices`).
#[derive(Debug, Clone)]
pub struct BacktestConfig {
    pub symbol: String,
    pub initial_capital: f64,
    pub slippage_pct: f64,
    pub commission_pct: f64,
}

impl Default for BacktestConfig {
    fn default() -> Self {
        Self {
            symbol: String::new(),
            initial_capital: 1_000_000.0,
            slippage_pct: 0.02,
            commission_pct: 0.03,
        }
    }
}

/// Strategy signal direction (mirrors `SignalKind`: only BUY/SELL exist).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SignalKind {
    Buy,
    Sell,
}

/// One bar signal with optional SL/TP (mirrors the `Signal` fields read).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Signal {
    pub kind: SignalKind,
    pub stop_loss: Option<f64>,
    pub take_profit: Option<f64>,
}

/// Per-bar signal provider. Called on every bar including warmup bars (whose
/// results are ignored, exactly like the Python warmup feed). `position` is
/// `Some((side, entry_price, entry_index))` when a leg is open — the same
/// triple the runner exposes as strategy state.
pub trait SignalSource {
    fn warmup(&self) -> usize;
    fn signal(&mut self, index: usize, position: Option<(Side, f64, usize)>) -> Option<Signal>;
}

/// Shared per-bar execution core: warmup feed, SL/TP exits, signal entries,
/// opposite-signal exits, final-bar `END` close. Returns closed trades in
/// close order.
pub fn execute_bars(
    bars: &[Bar],
    source: &mut dyn SignalSource,
    config: &BacktestConfig,
    progress: &mut dyn FnMut(usize, usize),
) -> Vec<TradeRecord> {
    let mut journal = TradeJournal::new();
    let mut positions = PositionManager::new(config.symbol.clone());
    let simulator = ExecutionSimulator::new(config.slippage_pct, config.commission_pct);
    let warmup = source.warmup();
    let mut realized = 0.0;

    for (index, bar) in bars.iter().enumerate() {
        if index % 500 == 0 {
            progress(index, bars.len());
        }

        // Warmup: feed the strategy for indicator history only, skip trading.
        if index < warmup {
            source.signal(index, None);
            continue;
        }

        // SL/TP first; a close consumes the whole bar (no signal this bar).
        if !positions.flat() {
            if let Some(trade) = positions.try_close(
                index,
                bar.timestamp.clone(),
                bar.high,
                bar.low,
                bar.close,
                config.commission_pct,
                false,
            ) {
                realized += trade.pnl;
                journal.record(trade);
                continue;
            }
        }

        let position = positions
            .open_position()
            .map(|v| (v.side, v.entry_price, v.entry_index));
        let Some(signal) = source.signal(index, position) else {
            continue;
        };

        if positions.flat() {
            let side = match signal.kind {
                SignalKind::Buy => Side::Long,
                SignalKind::Sell => Side::Short,
            };
            let equity = config.initial_capital + realized;
            let Some(fill) = simulator.fill(side, bar.close, equity) else {
                continue;
            };
            match side {
                Side::Long => {
                    positions.open_long(
                        config.symbol.clone(),
                        index,
                        bar.timestamp.clone(),
                        fill.fill_price,
                        fill.quantity,
                        fill.commission,
                        signal.stop_loss,
                        signal.take_profit,
                    );
                }
                Side::Short => {
                    positions.open_short(
                        config.symbol.clone(),
                        index,
                        bar.timestamp.clone(),
                        fill.fill_price,
                        fill.quantity,
                        fill.commission,
                        signal.stop_loss,
                        signal.take_profit,
                    );
                }
            }
        } else {
            let view = positions.open_position().expect("checked non-flat above");
            let is_long = view.side == Side::Long;
            let should_close = (is_long && signal.kind == SignalKind::Sell)
                || (!is_long && signal.kind == SignalKind::Buy);
            if should_close {
                let slip = bar.close * (config.slippage_pct / 100.0);
                let fill_price = if is_long {
                    bar.close - slip
                } else {
                    bar.close + slip
                };
                if let Some(trade) = positions.close_signal(
                    index,
                    bar.timestamp.clone(),
                    fill_price,
                    config.commission_pct,
                ) {
                    realized += trade.pnl;
                    journal.record(trade);
                }
            }
        }
    }

    if !positions.flat() && !bars.is_empty() {
        let last = &bars[bars.len() - 1];
        let view = positions.open_position().expect("checked non-flat above");
        let slip = last.close * (config.slippage_pct / 100.0);
        let last_price = if view.side == Side::Short {
            last.close + slip
        } else {
            last.close - slip
        };
        if let Some(trade) = positions.close_end(
            bars.len() - 1,
            last.timestamp.clone(),
            last_price,
            config.commission_pct,
        ) {
            journal.record(trade);
        }
    }

    journal.into_trades()
}

// ── replay.rs: window slicing ─────────────────────────────────────────────

/// Indices of bars whose `timestamp[..10]` date falls in `[start, end]`
/// (`None` bound = open-ended). Input order is preserved; empty input gives
/// empty output. Mirrors `slice_bars` (short timestamps compare by prefix,
/// exactly like Python's `[:10]` slice).
pub fn slice_indices(timestamps: &[&str], start: Option<&str>, end: Option<&str>) -> Vec<usize> {
    if timestamps.is_empty() {
        return Vec::new();
    }
    if start.is_none() && end.is_none() {
        return (0..timestamps.len()).collect();
    }
    timestamps
        .iter()
        .enumerate()
        .filter(|(_, ts)| {
            let day = ts.get(..10).unwrap_or(ts);
            if let Some(s) = start {
                if day < &s {
                    return false;
                }
            }
            if let Some(e) = end {
                if day > &e {
                    return false;
                }
            }
            true
        })
        .map(|(i, _)| i)
        .collect()
}

// ── engine/metrics.py: report assembly (kernels reused) ───────────────────

/// The display metrics (mirrors `PerformanceMetrics` fields).
#[derive(Debug, Clone, PartialEq)]
pub struct PerformanceReport {
    pub net_profit: f64,
    pub net_profit_pct: f64,
    pub total_trades: usize,
    pub win_rate: Option<f64>,
    pub profit_factor: Option<f64>,
    pub max_drawdown_pct: f64,
    pub max_drawdown_abs: f64,
    pub avg_trade: Option<f64>,
    pub expectancy: Option<f64>,
    pub sharpe_ratio: Option<f64>,
    pub gross_profit: f64,
    pub gross_loss: f64,
    pub starting_capital: f64,
    pub ending_capital: f64,
}

/// Equity series with the starting point prepended: `(equities, drawdowns)`
/// where index 0 is `(initial, 0.0)`. Mirrors `compute_equity_curve` minus
/// timestamps (caller zips exit times, exactly like the Python `zip`).
pub fn assemble_curve(initial: f64, pnls: &[f64]) -> (Vec<f64>, Vec<f64>) {
    let (equities, drawdowns) = metrics::equity_curve(initial, pnls);
    let mut full_eq = Vec::with_capacity(equities.len() + 1);
    let mut full_dd = Vec::with_capacity(drawdowns.len() + 1);
    full_eq.push(initial);
    full_dd.push(0.0);
    full_eq.extend(equities);
    full_dd.extend(drawdowns);
    (full_eq, full_dd)
}

/// Derive display metrics from per-trade PnLs. `final_equity` is the last
/// point of [`assemble_curve`] (or `initial` when there are no trades) —
/// the same value `compute_metrics` reads off the curve. Kernels reused:
/// [`metrics::max_drawdown`], [`metrics::sharpe`].
pub fn assemble_report(
    pnls: &[f64],
    bars_held: &[f64],
    initial: f64,
    final_equity: f64,
) -> PerformanceReport {
    let total = pnls.len();
    let net = final_equity - initial;
    let net_pct = if initial != 0.0 {
        net / initial * 100.0
    } else {
        0.0
    };
    if total == 0 {
        return PerformanceReport {
            net_profit: net,
            net_profit_pct: net_pct,
            total_trades: 0,
            win_rate: None,
            profit_factor: None,
            max_drawdown_pct: 0.0,
            max_drawdown_abs: 0.0,
            avg_trade: None,
            expectancy: None,
            sharpe_ratio: None,
            gross_profit: 0.0,
            gross_loss: 0.0,
            starting_capital: initial,
            ending_capital: final_equity,
        };
    }

    let gross_profit: f64 = pnls.iter().filter(|p| **p > 0.0).sum();
    let gross_loss: f64 = pnls.iter().filter(|p| **p < 0.0).sum();
    let wins = pnls.iter().filter(|p| **p > 0.0).count();
    let win_rate = wins as f64 / total as f64;
    let profit_factor = if gross_loss != 0.0 {
        Some(gross_profit / gross_loss.abs())
    } else {
        None
    };
    let avg = pnls.iter().sum::<f64>() / total as f64;
    let (equities, _) = assemble_curve(initial, pnls);
    let (dd_pct, dd_abs) = metrics::max_drawdown(&equities);
    PerformanceReport {
        net_profit: net,
        net_profit_pct: net_pct,
        total_trades: total,
        win_rate: Some(win_rate),
        profit_factor,
        max_drawdown_pct: dd_pct,
        max_drawdown_abs: dd_abs,
        avg_trade: Some(avg),
        expectancy: Some(avg),
        sharpe_ratio: metrics::sharpe(pnls, bars_held, initial),
        gross_profit,
        gross_loss,
        starting_capital: initial,
        ending_capital: final_equity,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bar(ts: &str, close: f64) -> Bar {
        Bar::new("T", ts, close - 0.5, close + 1.0, close - 1.5, close, 1000)
    }

    /// Scripted source replaying the parity probe
    /// (`Temp/opencode/bt_probe.py`): BUY@2 (SL 99), SELL@7, BUY@9, BUY@10.
    struct ProbeScript {
        warmup: usize,
    }

    impl SignalSource for ProbeScript {
        fn warmup(&self) -> usize {
            self.warmup
        }

        fn signal(
            &mut self,
            index: usize,
            _position: Option<(Side, f64, usize)>,
        ) -> Option<Signal> {
            match index {
                2 => Some(Signal {
                    kind: SignalKind::Buy,
                    stop_loss: Some(99.0),
                    take_profit: Some(110.0),
                }),
                7 => Some(Signal {
                    kind: SignalKind::Sell,
                    stop_loss: None,
                    take_profit: None,
                }),
                9 | 10 => Some(Signal {
                    kind: SignalKind::Buy,
                    stop_loss: None,
                    take_profit: None,
                }),
                _ => None,
            }
        }
    }

    fn probe_bars() -> Vec<Bar> {
        [
            100.0, 101.0, 102.0, 103.0, 104.0, 98.0, 97.0, 96.0, 95.0, 97.0, 98.0, 99.0,
        ]
        .iter()
        .enumerate()
        .map(|(i, c)| bar(&format!("2026-01-05 09:{:02}:00", 15 + 15 * i), *c))
        .collect()
    }

    fn approx(a: f64, b: f64) -> bool {
        (a - b).abs() < 1e-9
    }

    // ── trade-level parity vs live `execute_bars` ────────────────────────

    #[test]
    fn loop_matches_python_trades() {
        let bars = probe_bars();
        let config = BacktestConfig {
            symbol: "T".to_string(),
            initial_capital: 100_000.0,
            slippage_pct: 0.02,
            commission_pct: 0.03,
        };
        let mut progress_calls = 0;
        let mut progress = |_: usize, _: usize| progress_calls += 1;
        let trades = execute_bars(
            &bars,
            &mut ProbeScript { warmup: 2 },
            &config,
            &mut progress,
        );
        assert_eq!(progress_calls, 1); // index 0 hits the %500 gate
        assert_eq!(trades.len(), 3);

        // T1: LONG 2→5, SL @99.0 (probe: entry 102.0204, qty 980.196..., pnl -3019.696...).
        let t1 = &trades[0];
        assert_eq!((t1.entry_index, t1.exit_index), (2, 5));
        assert_eq!(t1.exit_reason, crate::backtest::ExitReason::StopLoss);
        assert!(approx(t1.entry_price, 102.0204));
        assert!(approx(t1.exit_price, 99.0));
        assert!(approx(t1.quantity, 980.1961176392173));
        assert!(approx(t1.pnl, -3019.696178411372));
        assert!(approx(t1.pnl_pct, -3.019696178411372));
        assert!(approx(t1.commission, 59.11182469388474));
        assert_eq!(t1.bars_held, 3);
        assert!(approx(t1.r_multiple.unwrap(), -1.0199662693682956));

        // T2: SHORT 7→9, opposite-signal close (probe: 95.9808 → 97.0194).
        let t2 = &trades[1];
        assert_eq!((t2.entry_index, t2.exit_index), (7, 9));
        assert_eq!(t2.side, Side::Short);
        assert_eq!(t2.exit_reason, crate::backtest::ExitReason::Signal);
        assert!(approx(t2.entry_price, 95.9808));
        assert!(approx(t2.exit_price, 97.0194));
        assert!(approx(t2.quantity, 1010.4135808577198));
        assert!(approx(t2.pnl, -1107.9185520353071));
        assert_eq!(t2.r_multiple, None);

        // T3: LONG 10→11, END close (probe: 98.0196 → 98.9802, pnl 881.75...).
        let t3 = &trades[2];
        assert_eq!((t3.entry_index, t3.exit_index), (10, 11));
        assert_eq!(t3.exit_reason, crate::backtest::ExitReason::End);
        assert!(approx(t3.entry_price, 98.0196));
        assert!(approx(t3.exit_price, 98.9802));
        assert!(approx(t3.quantity, 978.0940268023264));
        assert!(approx(t3.pnl, 881.7518238479383));
    }

    #[test]
    fn warmup_feed_ignores_signals_and_empty_stays_empty() {
        // Signals during warmup never trade, even a BUY on every bar.
        struct AlwaysBuy;
        impl SignalSource for AlwaysBuy {
            fn warmup(&self) -> usize {
                100
            }
            fn signal(&mut self, _i: usize, _p: Option<(Side, f64, usize)>) -> Option<Signal> {
                Some(Signal {
                    kind: SignalKind::Buy,
                    stop_loss: None,
                    take_profit: None,
                })
            }
        }
        let bars = probe_bars();
        let config = BacktestConfig::default();
        let mut nop = |_: usize, _: usize| {};
        assert!(execute_bars(&bars, &mut AlwaysBuy, &config, &mut nop).is_empty());
        assert!(execute_bars(&[], &mut ProbeScript { warmup: 0 }, &config, &mut nop).is_empty());
    }

    #[test]
    fn sl_close_consumes_the_bar() {
        // A bar that triggers SL closes the position AND skips the signal —
        // the scripted BUY on the SL bar must not open a second leg.
        struct BuyEveryBar;
        impl SignalSource for BuyEveryBar {
            fn warmup(&self) -> usize {
                0
            }
            fn signal(&mut self, _i: usize, _p: Option<(Side, f64, usize)>) -> Option<Signal> {
                Some(Signal {
                    kind: SignalKind::Buy,
                    stop_loss: Some(50.0),
                    take_profit: None,
                })
            }
        }
        let bars = vec![
            bar("2026-01-05 09:15:00", 100.0),
            bar("2026-01-05 09:30:00", 40.0),
        ];
        let config = BacktestConfig {
            symbol: "T".to_string(),
            ..BacktestConfig::default()
        };
        let mut nop = |_: usize, _: usize| {};
        let trades = execute_bars(&bars, &mut BuyEveryBar, &config, &mut nop);
        // Bar 0 opens LONG; bar 1 hits SL 50 → close, signal skipped → flat.
        // Bar 1 low (38.5) <= 50 → SL. Exactly one trade.
        assert_eq!(trades.len(), 1);
        assert_eq!(trades[0].exit_reason, crate::backtest::ExitReason::StopLoss);
    }

    // ── slice parity ────────────────────────────────────────────────────

    #[test]
    fn slice_indices_match_python() {
        let days = vec![
            "2026-01-05 09:15:00",
            "2026-01-05 09:30:00",
            "2026-01-06 09:15:00",
        ];
        assert_eq!(slice_indices(&days, None, None), vec![0, 1, 2]);
        assert!(slice_indices(&[], Some("2026-01-05"), Some("2026-01-06")).is_empty());
        assert_eq!(
            slice_indices(&days, Some("2026-01-06"), Some("2026-01-06")),
            vec![2]
        );
        assert!(slice_indices(&days, Some("2026-01-07"), Some("2026-01-06")).is_empty());
    }

    // ── report parity (values recomputed from probe trades) ─────────────

    #[test]
    fn report_matches_python_metrics() {
        // Probe trade PnLs through the shared kernels.
        let pnls = [-3019.696178411372, -1107.9185520353071, 881.7518238479383];
        let held = [3.0, 2.0, 1.0];
        let (equities, _) = assemble_curve(100_000.0, &pnls);
        assert_eq!(equities.len(), 4);
        assert!(approx(equities[0], 100_000.0));
        let final_eq = *equities.last().unwrap();
        assert!(approx(
            final_eq,
            100_000.0 - 3019.696178411372 - 1107.9185520353071 + 881.7518238479383
        ));

        let r = assemble_report(&pnls, &held, 100_000.0, final_eq);
        assert_eq!(r.total_trades, 3);
        assert!(approx(r.gross_profit, 881.7518238479383));
        assert!(approx(r.gross_loss, -4127.614730446679));
        assert!(approx(r.win_rate.unwrap(), 1.0 / 3.0));
        assert!(approx(
            r.profit_factor.unwrap(),
            881.7518238479383 / 4127.614730446679
        ));
        assert!(approx(r.net_profit, final_eq - 100_000.0));
        assert!(approx(r.avg_trade.unwrap(), r.net_profit / 3.0));
        assert_eq!(r.expectancy, r.avg_trade);
        assert_eq!(r.starting_capital, 100_000.0);
        assert_eq!(r.ending_capital, final_eq);
        assert!(r.max_drawdown_pct > 0.0 && r.max_drawdown_abs > 0.0);
        assert!(r.sharpe_ratio.is_some());
    }

    #[test]
    fn report_zero_trades_matches_python() {
        let r = assemble_report(&[], &[], 100_000.0, 100_000.0);
        assert_eq!(r.total_trades, 0);
        assert_eq!(r.win_rate, None);
        assert_eq!(r.profit_factor, None);
        assert_eq!(r.avg_trade, None);
        assert_eq!(r.sharpe_ratio, None);
        assert_eq!((r.max_drawdown_pct, r.max_drawdown_abs), (0.0, 0.0));
        assert_eq!((r.gross_profit, r.gross_loss), (0.0, 0.0));
        assert_eq!((r.net_profit, r.net_profit_pct), (0.0, 0.0));
    }
}
