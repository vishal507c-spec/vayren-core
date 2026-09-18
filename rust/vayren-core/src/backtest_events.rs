//! BACKTEST event contracts — Rust equivalents of
//! `06_backtest/backtest/events/backtest_events.py`.
//!
//! Names, fields and defaults mirror the Python frozen dataclasses. The Qt
//! `BacktestWorker` stays the production bridge; these types let future
//! pure-Rust consumers speak the same bus language via
//! `crate::event_bus::EventBus`.
//!
//! One deliberate narrowing: Python's `BacktestCompleted.result` carries the
//! full `BacktestResult` object graph (kept as `object` to dodge an import
//! cycle). Rust carries [`RunSummary`] — trade count + net profit — instead
//! of an object graph across the boundary.

use crate::event_bus::Event;

/// Request: run a backtest for one or more strategies.
#[derive(Debug, Clone, PartialEq)]
pub struct RunBacktest {
    pub request_id: String,
    pub strategy_ids: Vec<String>,
    pub symbol: String,
    pub timeframe: String,
    pub start_date: String,
    pub end_date: String,
    pub initial_capital: f64,
    pub slippage_pct: f64,
    pub commission_pct: f64,
}
impl Event for RunBacktest {}

impl RunBacktest {
    /// Python dataclass defaults: capital 1M, slippage 0.02, commission 0.03.
    pub fn new(
        request_id: impl Into<String>,
        strategy_ids: Vec<String>,
        symbol: impl Into<String>,
        timeframe: impl Into<String>,
        start_date: impl Into<String>,
        end_date: impl Into<String>,
    ) -> Self {
        Self {
            request_id: request_id.into(),
            strategy_ids,
            symbol: symbol.into(),
            timeframe: timeframe.into(),
            start_date: start_date.into(),
            end_date: end_date.into(),
            initial_capital: 1_000_000.0,
            slippage_pct: 0.02,
            commission_pct: 0.03,
        }
    }
}

/// A backtest run started.
#[derive(Debug, Clone, PartialEq)]
pub struct BacktestStarted {
    pub request_id: String,
    pub strategy_ids: Vec<String>,
    pub symbol: String,
    pub timeframe: String,
}
impl Event for BacktestStarted {}

/// Periodic progress during a run.
#[derive(Debug, Clone, PartialEq)]
pub struct BacktestProgress {
    pub request_id: String,
    pub processed: usize,
    pub total: usize,
}
impl Event for BacktestProgress {}

/// Compact run outcome (see module docs: summary, not the object graph).
#[derive(Debug, Clone, PartialEq)]
pub struct RunSummary {
    pub trade_count: usize,
    pub net_profit: f64,
}

/// A backtest finished successfully.
#[derive(Debug, Clone, PartialEq)]
pub struct BacktestCompleted {
    pub request_id: String,
    pub summary: RunSummary,
}
impl Event for BacktestCompleted {}

/// A backtest could not be produced.
#[derive(Debug, Clone, PartialEq)]
pub struct BacktestFailed {
    pub request_id: String,
    pub reason: String,
}
impl Event for BacktestFailed {}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::event_bus::EventBus;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;

    #[test]
    fn request_defaults_match_python() {
        let req = RunBacktest::new(
            "r1",
            vec!["s1".to_string()],
            "TCS",
            "15m",
            "2026-01-01",
            "2026-06-30",
        );
        assert_eq!(req.initial_capital, 1_000_000.0);
        assert_eq!(req.slippage_pct, 0.02);
        assert_eq!(req.commission_pct, 0.03);
    }

    #[test]
    fn run_lifecycle_rides_the_bus() {
        // Mirrors worker → bootstrap → bus.publish ordering: Started,
        // Progress*, Completed (or Failed).
        let bus = EventBus::new();
        let order = Arc::new(std::sync::Mutex::new(Vec::new()));

        let o1 = Arc::clone(&order);
        bus.subscribe(move |_: &BacktestStarted| o1.lock().unwrap().push("started"));
        let o2 = Arc::clone(&order);
        bus.subscribe(move |_: &BacktestProgress| o2.lock().unwrap().push("progress"));
        let o3 = Arc::clone(&order);
        bus.subscribe(move |event: &BacktestCompleted| {
            assert_eq!(event.summary.trade_count, 3);
            o3.lock().unwrap().push("completed");
        });
        let failures = Arc::new(AtomicUsize::new(0));
        let f = Arc::clone(&failures);
        bus.subscribe(move |_: &BacktestFailed| {
            f.fetch_add(1, Ordering::SeqCst);
        });

        let req = RunBacktest::new(
            "r1",
            vec!["s1".to_string()],
            "TCS",
            "15m",
            "2026-01-01",
            "2026-06-30",
        );
        bus.publish(BacktestStarted {
            request_id: req.request_id.clone(),
            strategy_ids: req.strategy_ids.clone(),
            symbol: req.symbol.clone(),
            timeframe: req.timeframe.clone(),
        });
        bus.publish(BacktestProgress {
            request_id: req.request_id.clone(),
            processed: 500,
            total: 1000,
        });
        bus.publish(BacktestCompleted {
            request_id: req.request_id.clone(),
            summary: RunSummary {
                trade_count: 3,
                net_profit: 881.75,
            },
        });
        assert_eq!(
            *order.lock().unwrap(),
            vec!["started", "progress", "completed"]
        );
        assert_eq!(failures.load(Ordering::SeqCst), 0);

        bus.publish(BacktestFailed {
            request_id: "r2".to_string(),
            reason: "no bars".to_string(),
        });
        assert_eq!(failures.load(Ordering::SeqCst), 1);
    }
}
