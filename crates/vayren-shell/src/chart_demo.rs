//! Chart view demo — proof of concept for legacy → Slint chart migration.
//!
//! Full 6,578 LOC legacy renderer/widget/window port is separate multi-week effort.
//! This demonstrates Rust+Slint architecture for the target state.

/// Candle data matching Slint CandleData struct.
#[derive(Debug, Clone)]
pub struct CandleData {
    pub timestamp: String,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: i64,
}

impl From<&vayren_core::market::Bar> for CandleData {
    fn from(bar: &vayren_core::market::Bar) -> Self {
        Self {
            timestamp: bar.timestamp.clone(),
            open: bar.open,
            high: bar.high,
            low: bar.low,
            close: bar.close,
            volume: bar.volume,
        }
    }
}

/// Generate sample candles for demo.
pub fn sample_candles(_symbol: &str, count: usize) -> Vec<CandleData> {
    (0..count)
        .map(|i| CandleData {
            timestamp: format!("2024-01-{:02} 09:30:00", i % 30 + 1),
            open: 100.0 + i as f64,
            high: 105.0 + i as f64,
            low: 98.0 + i as f64,
            close: 102.0 + i as f64,
            volume: 1_000_000 + (i * 10_000) as i64,
        })
        .collect()
}

/// Architecture demo: EventBus → Rust state → Slint bindings.
/// Full legacy→Slint chart wiring happens when renderer port completes.
pub fn chart_architecture_note() -> &'static str {
    "Chart migration path: legacy Python renderer → Rust core + Slint UI\n\
     - EventBus: already migrated (vayren-core::event_bus)\n\
     - Bar model: migrated (vayren-core::market::Bar)\n\
     - Renderer: 6,578 LOC legacy painter → Slint canvas (in progress)\n\
     - Integration: LoadSymbol event → market loader → DataLoaded → chart update"
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn candle_from_bar() {
        let bar = vayren_core::market::Bar::new(
            "AAPL",
            "2024-01-01 09:30:00",
            150.0,
            152.0,
            149.0,
            151.5,
            1_000_000,
        );
        let candle = CandleData::from(&bar);
        assert_eq!(candle.timestamp, "2024-01-01 09:30:00");
        assert_eq!(candle.open, 150.0);
        assert_eq!(candle.close, 151.5);
    }

    #[test]
    fn sample_generation() {
        let candles = sample_candles("TEST", 10);
        assert_eq!(candles.len(), 10);
        assert_eq!(candles[0].open, 100.0);
        assert_eq!(candles[9].open, 109.0);
    }

    #[test]
    fn architecture_note() {
        let note = chart_architecture_note();
        assert!(note.contains("EventBus"));
        assert!(note.contains("Bar model"));
    }
}
