//! CHART event contracts — Rust equivalents of `04_chart/chart/events/`.
//!
//! Payload names and roles mirror the Python frozen dataclasses exactly
//! (`ChartReady` result carrying the model, `WindowRendered` result; see
//! `90_brain/event_catalog.md` §2). Events carry data only — never
//! connections, widgets or callables.
//!
//! The production Python `ChartEngine`/`ChartWindow` stay the authority;
//! this module lets future pure-Rust consumers speak the same bus language
//! via `crate::event_bus::EventBus`, without forcing any Python module to
//! move. Handler semantics mirror `ChartEngine.on_data_loaded`: empty input
//! publishes nothing (Python logs a warning and returns).

use crate::chart_model::{build_model, ChartModel};
use crate::event_bus::{Event, EventBus};
use crate::market::Bar;

/// The chart model is ready to be displayed (mirrors `ChartReady`).
#[derive(Debug, Clone, PartialEq)]
pub struct ChartReady {
    pub model: ChartModel,
}
impl Event for ChartReady {}

/// The chart window has been shown (mirrors `WindowRendered`).
#[derive(Debug, Clone, PartialEq)]
pub struct WindowRendered;
impl Event for WindowRendered {}

/// Mirror of `ChartEngine.on_data_loaded`: assemble the model, then publish
/// `ChartReady`. Empty bars publish nothing and return `false` (Python logs
/// the warning and returns).
pub fn handle_data_loaded(bus: &EventBus, symbol: &str, bars: Vec<Bar>) -> bool {
    match build_model(symbol, bars) {
        Some(model) => {
            bus.publish(ChartReady { model });
            true
        }
        None => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;

    fn bar(symbol: &str, ts: &str) -> Bar {
        Bar::new(symbol, ts, 100.0, 105.0, 95.0, 102.0, 1000)
    }

    #[test]
    fn data_loaded_publishes_sorted_chart_ready() {
        // Mirrors `test_on_data_loaded_publishes_sorted_chart_ready`.
        let bus = EventBus::new();
        let received = Arc::new(AtomicUsize::new(0));
        let counter = Arc::clone(&received);
        bus.subscribe(move |event: &ChartReady| {
            let stamps: Vec<&str> = event
                .model
                .bars
                .iter()
                .map(|bar| bar.timestamp.as_str())
                .collect();
            assert_eq!(stamps, vec!["2026-01-01", "2026-01-02", "2026-01-03"]);
            assert_eq!(event.model.symbol, "SPY");
            assert_eq!(event.model.exchange, "NSE");
            counter.fetch_add(1, Ordering::SeqCst);
        });

        let ok = handle_data_loaded(
            &bus,
            "SPY",
            vec![
                bar("SPY", "2026-01-03"),
                bar("SPY", "2026-01-01"),
                bar("SPY", "2026-01-02"),
            ],
        );
        assert!(ok);
        assert_eq!(received.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn empty_data_publishes_nothing() {
        // Mirrors `test_empty_data_publishes_nothing`.
        let bus = EventBus::new();
        let received = Arc::new(AtomicUsize::new(0));
        let counter = Arc::clone(&received);
        bus.subscribe(move |_: &ChartReady| {
            counter.fetch_add(1, Ordering::SeqCst);
        });

        assert!(!handle_data_loaded(&bus, "SPY", Vec::new()));
        assert_eq!(received.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn window_rendered_rides_the_bus() {
        let bus = EventBus::new();
        let received = Arc::new(AtomicUsize::new(0));
        let counter = Arc::clone(&received);
        bus.subscribe(move |_: &WindowRendered| {
            counter.fetch_add(1, Ordering::SeqCst);
        });
        bus.publish(WindowRendered);
        assert_eq!(received.load(Ordering::SeqCst), 1);
    }
}
