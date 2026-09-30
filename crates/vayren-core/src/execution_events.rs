//! EXECUTION event contracts — Rust equivalents of
//! `08_execution/execution/events/__init__.py`.
//!
//! Names, fields and defaults mirror the Python frozen dataclasses. The
//! `LiveSession` stays the production publisher; these types let future
//! pure-Rust consumers speak the same bus language via
//! `crate::event_bus::EventBus`.

use crate::event_bus::Event;

// ── normalized market-data events (provider-agnostic) ────────────────────

/// Base for normalized market data. Sequence is per-stream monotonic.
#[derive(Debug, Clone, PartialEq)]
pub struct MarketEvent {
    pub symbol: String,
    pub timestamp: String,
    pub seq: i64,
    pub source: String,
}
impl Event for MarketEvent {}

/// Best bid/ask snapshot.
#[derive(Debug, Clone, PartialEq)]
pub struct QuoteEvent {
    pub base: MarketEvent,
    pub bid: f64,
    pub ask: f64,
    pub bid_qty: f64,
    pub ask_qty: f64,
}
impl Event for QuoteEvent {}

/// One tape print.
#[derive(Debug, Clone, PartialEq)]
pub struct TradeEvent {
    pub base: MarketEvent,
    pub price: f64,
    pub quantity: f64,
}
impl Event for TradeEvent {}

/// One closed (or updating) OHLCV candle.
#[derive(Debug, Clone, PartialEq)]
pub struct CandleEvent {
    pub base: MarketEvent,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: i64,
    pub timeframe: String,
    pub is_closed: bool,
}
impl Event for CandleEvent {}

/// Top-N depth snapshot (empty when the provider has no book).
#[derive(Debug, Clone, PartialEq)]
pub struct OrderBookEvent {
    pub base: MarketEvent,
    pub bids: Vec<(f64, f64)>,
    pub asks: Vec<(f64, f64)>,
}
impl Event for OrderBookEvent {}

/// Provider liveness pulse.
#[derive(Debug, Clone, PartialEq)]
pub struct HeartbeatEvent {
    pub base: MarketEvent,
    pub status: String,
}
impl Event for HeartbeatEvent {}

// ── execution journal facts (observable pipeline trace) ──────────────────

#[derive(Debug, Clone, PartialEq)]
pub struct SignalGenerated {
    pub request_id: String,
    pub strategy_id: String,
    pub signal_id: String,
    pub event_seq: i64,
}
impl Event for SignalGenerated {}

#[derive(Debug, Clone, PartialEq)]
pub struct RiskApproved {
    pub request_id: String,
    pub intent_id: String,
}
impl Event for RiskApproved {}

#[derive(Debug, Clone, PartialEq)]
pub struct RiskDenied {
    pub request_id: String,
    pub intent_id: String,
    pub reasons: Vec<String>,
}
impl Event for RiskDenied {}

#[derive(Debug, Clone, PartialEq)]
pub struct OrderPlanned {
    pub request_id: String,
    pub intent_id: String,
    pub client_order_id: String,
}
impl Event for OrderPlanned {}

#[derive(Debug, Clone, PartialEq)]
pub struct OrderSubmitted {
    pub request_id: String,
    pub client_order_id: String,
}
impl Event for OrderSubmitted {}

#[derive(Debug, Clone, PartialEq)]
pub struct OrderAcknowledged {
    pub request_id: String,
    pub client_order_id: String,
    pub broker_order_id: String,
}
impl Event for OrderAcknowledged {}

#[derive(Debug, Clone, PartialEq)]
pub struct OrderFill {
    pub request_id: String,
    pub client_order_id: String,
    pub fill_qty: f64,
    pub fill_price: f64,
    pub partial: bool,
}
impl Event for OrderFill {}

#[derive(Debug, Clone, PartialEq)]
pub struct OrderRejected {
    pub request_id: String,
    pub client_order_id: String,
    pub reason: String,
}
impl Event for OrderRejected {}

#[derive(Debug, Clone, PartialEq)]
pub struct PositionUpdated {
    pub request_id: String,
    pub symbol: String,
    pub quantity: f64,
}
impl Event for PositionUpdated {}

#[derive(Debug, Clone, PartialEq)]
pub struct KillSwitchEngaged {
    pub request_id: String,
    pub level: String,
    pub reason: String,
}
impl Event for KillSwitchEngaged {}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::event_bus::EventBus;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;

    fn market(symbol: &str, seq: i64) -> MarketEvent {
        MarketEvent {
            symbol: symbol.to_string(),
            timestamp: "2026-01-05T09:30:00+00:00".to_string(),
            seq,
            source: String::new(),
        }
    }

    #[test]
    fn pipeline_facts_ride_the_bus_in_order() {
        // Mirrors the session pipeline emission order: signal → risk →
        // planned → submitted → ack → fill → position.
        let bus = EventBus::new();
        let order = Arc::new(std::sync::Mutex::new(Vec::new()));
        macro_rules! watch {
            ($t:ty, $name:expr) => {{
                let o = Arc::clone(&order);
                bus.subscribe(move |_: &$t| o.lock().unwrap().push($name));
            }};
        }
        watch!(SignalGenerated, "signal");
        watch!(RiskApproved, "risk");
        watch!(RiskDenied, "denied");
        watch!(OrderPlanned, "planned");
        watch!(OrderSubmitted, "submitted");
        watch!(OrderAcknowledged, "ack");
        watch!(OrderFill, "fill");
        watch!(OrderRejected, "rejected");
        watch!(PositionUpdated, "position");
        watch!(KillSwitchEngaged, "kill");

        bus.publish(SignalGenerated {
            request_id: "live-1".to_string(),
            strategy_id: "sma".to_string(),
            signal_id: "g1".to_string(),
            event_seq: 7,
        });
        bus.publish(RiskApproved {
            request_id: "live-1".to_string(),
            intent_id: "sma:1.0:7:1".to_string(),
        });
        bus.publish(OrderPlanned {
            request_id: "live-1".to_string(),
            intent_id: "sma:1.0:7:1".to_string(),
            client_order_id: "sma:1.0:7:1:o1".to_string(),
        });
        bus.publish(OrderSubmitted {
            request_id: "live-1".to_string(),
            client_order_id: "c1".to_string(),
        });
        bus.publish(OrderAcknowledged {
            request_id: "live-1".to_string(),
            client_order_id: "c1".to_string(),
            broker_order_id: "PAPER-1".to_string(),
        });
        bus.publish(OrderFill {
            request_id: "live-1".to_string(),
            client_order_id: "c1".to_string(),
            fill_qty: 10.0,
            fill_price: 100.02,
            partial: false,
        });
        bus.publish(PositionUpdated {
            request_id: "live-1".to_string(),
            symbol: "T".to_string(),
            quantity: 10.0,
        });
        assert_eq!(
            *order.lock().unwrap(),
            vec![
                "signal",
                "risk",
                "planned",
                "submitted",
                "ack",
                "fill",
                "position"
            ]
        );
    }

    #[test]
    fn market_events_carry_defaults() {
        let bus = EventBus::new();
        let count = Arc::new(AtomicUsize::new(0));
        let c = Arc::clone(&count);
        bus.subscribe(move |event: &CandleEvent| {
            assert_eq!(event.timeframe, "15m");
            assert!(event.is_closed);
            c.fetch_add(1, Ordering::SeqCst);
        });
        let c2 = Arc::clone(&count);
        bus.subscribe(move |event: &HeartbeatEvent| {
            assert_eq!(event.status, "ok");
            c2.fetch_add(1, Ordering::SeqCst);
        });
        bus.publish(CandleEvent {
            base: market("T", 1),
            open: 99.5,
            high: 101.0,
            low: 98.5,
            close: 100.0,
            volume: 1000,
            timeframe: "15m".to_string(),
            is_closed: true,
        });
        bus.publish(HeartbeatEvent {
            base: market("T", 2),
            status: "ok".to_string(),
        });
        bus.publish(QuoteEvent {
            base: market("T", 3),
            bid: 99.9,
            ask: 100.1,
            bid_qty: 5.0,
            ask_qty: 6.0,
        });
        bus.publish(TradeEvent {
            base: market("T", 4),
            price: 100.0,
            quantity: 2.0,
        });
        bus.publish(OrderBookEvent {
            base: market("T", 5),
            bids: vec![],
            asks: vec![],
        });
        assert_eq!(count.load(Ordering::SeqCst), 2);
    }

    #[test]
    fn denial_and_rejection_shapes() {
        let denied = RiskDenied {
            request_id: "live-1".to_string(),
            intent_id: "i1".to_string(),
            reasons: vec!["in cooldown".to_string()],
        };
        assert_eq!(denied.reasons, vec!["in cooldown".to_string()]);
        let rejected = OrderRejected {
            request_id: "live-1".to_string(),
            client_order_id: "c1".to_string(),
            reason: String::new(),
        };
        assert_eq!(rejected.reason, "");
    }
}
