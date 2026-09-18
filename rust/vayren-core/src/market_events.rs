//! MARKET_DATA event contracts — Rust equivalents of 03_market/market/events/.
//!
//! Payload names, fields and command/fact roles mirror the Python frozen
//! dataclasses exactly (see `90_brain/event_catalog.md` §2). Events carry
//! data only — never connections, widgets or callables.
//!
//! The production Python loaders stay the authority; this module lets future
//! pure-Rust consumers speak the same bus language via
//! `crate::event_bus::EventBus`, without forcing any Python module to move.
//! Handler semantics mirror the Python loaders: fetch failure publishes
//! nothing (Python logs and returns).

use crate::event_bus::{Event, EventBus};
use crate::market::{Bar, SymbolQuote};

// ── commands (imperative requests) ──────────────────────────────────────

/// Request candles for a symbol. `None` limit = full history.
#[derive(Debug, Clone, PartialEq)]
pub struct LoadSymbol {
    pub symbol: String,
    pub limit: Option<usize>,
}
impl Event for LoadSymbol {}

/// Request candles at another timeframe. `None` limit = full history.
#[derive(Debug, Clone, PartialEq)]
pub struct TimeframeChanged {
    pub symbol: String,
    pub timeframe: String,
    pub limit: Option<usize>,
}
impl Event for TimeframeChanged {}

/// Request the symbol universe.
#[derive(Debug, Clone, PartialEq)]
pub struct ListSymbols;
impl Event for ListSymbols {}

/// Request available timeframes for a symbol.
#[derive(Debug, Clone, PartialEq)]
pub struct ListTimeframes {
    pub symbol: String,
}
impl Event for ListTimeframes {}

// ── facts (past-tense results) ──────────────────────────────────────────

/// Discovered symbol universe, sorted.
#[derive(Debug, Clone, PartialEq)]
pub struct SymbolsListed {
    pub symbols: Vec<String>,
}
impl Event for SymbolsListed {}

/// Detected timeframes for one symbol.
#[derive(Debug, Clone, PartialEq)]
pub struct TimeframesListed {
    pub symbol: String,
    pub timeframes: Vec<String>,
}
impl Event for TimeframesListed {}

/// Latest-quote snapshot for the universe.
#[derive(Debug, Clone, PartialEq)]
pub struct QuotesLoaded {
    pub quotes: Vec<SymbolQuote>,
}
impl Event for QuotesLoaded {}

/// Loaded candles for one symbol, ascending by timestamp.
#[derive(Debug, Clone, PartialEq)]
pub struct DataLoaded {
    pub symbol: String,
    pub bars: Vec<Bar>,
}
impl Event for DataLoaded {}

// ── loader semantics (mirror 03_market/market/loader/) ──────────────────

/// Mirror of `MarketDataLoader.on_load_symbol`: fetch, then publish
/// `DataLoaded`. Fetch failure publishes nothing and returns `false`
/// (Python logs the exception and returns).
pub fn handle_load_symbol(
    bus: &EventBus,
    event: &LoadSymbol,
    fetch: impl FnOnce() -> Result<Vec<Bar>, String>,
) -> bool {
    match fetch() {
        Ok(bars) => {
            bus.publish(DataLoaded {
                symbol: event.symbol.clone(),
                bars,
            });
            true
        }
        Err(_) => false,
    }
}

/// Mirror of `MarketDataLoader.on_timeframe_changed`: same fetch-then-publish
/// contract at the requested timeframe.
pub fn handle_timeframe_changed(
    bus: &EventBus,
    event: &TimeframeChanged,
    fetch: impl FnOnce() -> Result<Vec<Bar>, String>,
) -> bool {
    match fetch() {
        Ok(bars) => {
            bus.publish(DataLoaded {
                symbol: event.symbol.clone(),
                bars,
            });
            true
        }
        Err(_) => false,
    }
}

/// Mirror of `SymbolListLoader.on_list_symbols`.
pub fn handle_list_symbols(
    bus: &EventBus,
    fetch: impl FnOnce() -> Result<Vec<String>, String>,
) -> bool {
    match fetch() {
        Ok(symbols) => {
            bus.publish(SymbolsListed { symbols });
            true
        }
        Err(_) => false,
    }
}

/// Mirror of `TimeframeListLoader.on_list_timeframes`.
pub fn handle_list_timeframes(
    bus: &EventBus,
    event: &ListTimeframes,
    fetch: impl FnOnce() -> Result<Vec<String>, String>,
) -> bool {
    match fetch() {
        Ok(timeframes) => {
            bus.publish(TimeframesListed {
                symbol: event.symbol.clone(),
                timeframes,
            });
            true
        }
        Err(_) => false,
    }
}

/// Mirror of `QuoteLoader._last_symbols`: identical re-listings are a no-op
/// so the one-time quote batch is never re-run for a chart switch.
///
/// `mark` is called only after a successful fetch — Python sets
/// `_last_symbols` after fetching, before publishing — so a failed fetch
/// leaves the guard unset and the next identical listing retries.
#[derive(Debug, Default)]
pub struct QuoteDeduper {
    last: Option<Vec<String>>,
}

impl QuoteDeduper {
    pub fn new() -> Self {
        Self { last: None }
    }

    /// True when this universe differs from the last fetched one.
    pub fn changed(&self, symbols: &[String]) -> bool {
        self.last.as_deref() != Some(symbols)
    }

    /// Record a successfully fetched universe.
    pub fn mark(&mut self, symbols: Vec<String>) {
        self.last = Some(symbols);
    }
}

/// Mirror of `QuoteLoader.on_symbols_listed`: skip identical universes,
/// otherwise fetch latest quotes and publish `QuotesLoaded`.
pub fn handle_symbols_listed_for_quotes(
    bus: &EventBus,
    deduper: &mut QuoteDeduper,
    event: &SymbolsListed,
    fetch: impl FnOnce() -> Result<Vec<SymbolQuote>, String>,
) -> bool {
    if !deduper.changed(&event.symbols) {
        return false;
    }
    match fetch() {
        Ok(quotes) => {
            deduper.mark(event.symbols.clone());
            bus.publish(QuotesLoaded { quotes });
            true
        }
        Err(_) => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::event_bus::EventBus;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;

    fn bar(symbol: &str, ts: &str, close: f64) -> Bar {
        Bar::new(
            symbol,
            ts,
            close - 1.0,
            close + 1.0,
            close - 2.0,
            close,
            100,
        )
    }

    #[test]
    fn load_symbol_publishes_data_loaded() {
        let bus = EventBus::new();
        let received = Arc::new(AtomicUsize::new(0));
        let counter = Arc::clone(&received);
        bus.subscribe(move |event: &DataLoaded| {
            assert_eq!(event.symbol, "RELIANCE");
            assert_eq!(event.bars.len(), 2);
            counter.fetch_add(1, Ordering::SeqCst);
        });

        let ok = handle_load_symbol(
            &bus,
            &LoadSymbol {
                symbol: "RELIANCE".to_string(),
                limit: None,
            },
            || {
                Ok(vec![
                    bar("RELIANCE", "2026-01-01 09:15:00", 100.0),
                    bar("RELIANCE", "2026-01-02 09:15:00", 101.0),
                ])
            },
        );
        assert!(ok);
        assert_eq!(received.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn load_symbol_failure_publishes_nothing() {
        // Mirrors the loader's log-and-return: no event, false return.
        let bus = EventBus::new();
        let received = Arc::new(AtomicUsize::new(0));
        let counter = Arc::clone(&received);
        bus.subscribe(move |_: &DataLoaded| {
            counter.fetch_add(1, Ordering::SeqCst);
        });

        let ok = handle_load_symbol(
            &bus,
            &LoadSymbol {
                symbol: "MISSING".to_string(),
                limit: None,
            },
            || Err("database not found".to_string()),
        );
        assert!(!ok);
        assert_eq!(received.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn timeframe_changed_publishes_data_loaded() {
        let bus = EventBus::new();
        let received = Arc::new(AtomicUsize::new(0));
        let counter = Arc::clone(&received);
        bus.subscribe(move |event: &DataLoaded| {
            assert_eq!(event.bars.len(), 1);
            counter.fetch_add(1, Ordering::SeqCst);
        });

        let ok = handle_timeframe_changed(
            &bus,
            &TimeframeChanged {
                symbol: "TCS".to_string(),
                timeframe: "30m".to_string(),
                limit: Some(500),
            },
            || Ok(vec![bar("TCS", "2026-01-01 09:30:00", 200.0)]),
        );
        assert!(ok);
        assert_eq!(received.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn list_symbols_and_timeframes_round_trip() {
        let bus = EventBus::new();
        bus.subscribe(|_: &SymbolsListed| {});
        bus.subscribe(|_: &TimeframesListed| {});
        assert_eq!(bus.handler_count::<SymbolsListed>(), 1);

        assert!(handle_list_symbols(&bus, || Ok(vec![
            "A".to_string(),
            "B".to_string()
        ])));
        assert!(!handle_list_symbols(&bus, || Err("io".to_string())));
        assert!(handle_list_timeframes(
            &bus,
            &ListTimeframes {
                symbol: "A".to_string(),
            },
            || Ok(vec!["15m".to_string()]),
        ));
    }

    #[test]
    fn quote_deduper_skips_identical_universe() {
        // Mirrors QuoteLoader: once per universe, retry after failure.
        let bus = EventBus::new();
        let published = Arc::new(AtomicUsize::new(0));
        let counter = Arc::clone(&published);
        bus.subscribe(move |_: &QuotesLoaded| {
            counter.fetch_add(1, Ordering::SeqCst);
        });

        let mut deduper = QuoteDeduper::new();
        let universe = SymbolsListed {
            symbols: vec!["A".to_string()],
        };
        let fetch_ok = || {
            Ok(vec![SymbolQuote::from_bar(&bar(
                "A",
                "2026-01-01 09:15:00",
                50.0,
            ))])
        };

        assert!(handle_symbols_listed_for_quotes(
            &bus,
            &mut deduper,
            &universe,
            fetch_ok
        ));
        assert_eq!(published.load(Ordering::SeqCst), 1);
        // Identical re-listing: no-op, no publish.
        assert!(!handle_symbols_listed_for_quotes(
            &bus,
            &mut deduper,
            &universe,
            || panic!("must not refetch")
        ));
        assert_eq!(published.load(Ordering::SeqCst), 1);
        // Changed universe: fetches again.
        let bigger = SymbolsListed {
            symbols: vec!["A".to_string(), "B".to_string()],
        };
        assert!(handle_symbols_listed_for_quotes(
            &bus,
            &mut deduper,
            &bigger,
            || Ok(vec![])
        ));
        assert_eq!(published.load(Ordering::SeqCst), 2);
    }

    #[test]
    fn quote_fetch_failure_leaves_guard_unset() {
        let bus = EventBus::new();
        let mut deduper = QuoteDeduper::new();
        let universe = SymbolsListed {
            symbols: vec!["A".to_string()],
        };
        assert!(!handle_symbols_listed_for_quotes(
            &bus,
            &mut deduper,
            &universe,
            || Err("db locked".to_string())
        ));
        // Guard unset → next identical listing retries instead of skipping.
        assert!(deduper.changed(&universe.symbols));
    }
}
