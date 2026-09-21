//! Execution models — intent chain, order vocabulary, position ledger.
//!
//! Rust port of 08_execution/execution/models/*.py. The order lifecycle table
//! itself lives in `order_state` (already Rust-owned); this module holds the
//! surrounding value objects and the position/account math.

use crate::order_state;

/// Order side.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Side {
    Buy,
    Sell,
}

impl Side {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Buy => "BUY",
            Self::Sell => "SELL",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "BUY" => Some(Self::Buy),
            "SELL" => Some(Self::Sell),
            _ => None,
        }
    }

    /// Signed direction applied to a position: +1 buy, -1 sell.
    pub fn direction(&self) -> f64 {
        match self {
            Self::Buy => 1.0,
            Self::Sell => -1.0,
        }
    }
}

/// Order type.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OrderType {
    Market,
    Limit,
}

impl OrderType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Market => "MARKET",
            Self::Limit => "LIMIT",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "MARKET" => Some(Self::Market),
            "LIMIT" => Some(Self::Limit),
            _ => None,
        }
    }
}

/// A strategy decision normalized for the live pipeline.
#[derive(Debug, Clone, PartialEq)]
pub struct StrategySignal {
    pub signal_id: String,
    pub strategy_id: String,
    pub strategy_version: String,
    pub timestamp: String,
    pub event_seq: i64,
    pub symbol: String,
    pub side: Side,
    pub price: f64,
    pub stop_loss: Option<f64>,
    pub take_profit: Option<f64>,
    pub reason: String,
    pub confidence: f64,
}

/// Deterministic idempotency key: same input events always yield same ids.
pub fn make_intent_id(
    strategy_id: &str,
    strategy_version: &str,
    event_seq: i64,
    intent_seq: i64,
) -> String {
    format!("{strategy_id}:{strategy_version}:{event_seq}:{intent_seq}")
}

/// What the strategy wants, before risk has spoken. Never an order yet.
#[derive(Debug, Clone, PartialEq)]
pub struct ExecutionIntent {
    pub intent_id: String,
    pub strategy_id: String,
    pub strategy_version: String,
    pub signal_id: String,
    pub timestamp: String,
    pub event_seq: i64,
    pub symbol: String,
    pub side: Side,
    /// Signed desired end state.
    pub target_position_qty: f64,
    /// Absolute policed quantity to trade now.
    pub quantity: f64,
    pub preferred_order_type: OrderType,
    pub reason: String,
    pub confidence: f64,
}

/// Deterministic planner output: one executable order spec.
#[derive(Debug, Clone, PartialEq)]
pub struct OrderPlan {
    pub intent_id: String,
    pub symbol: String,
    pub side: Side,
    pub quantity: f64,
    pub order_type: OrderType,
    pub limit_price: Option<f64>,
    pub time_in_force: String,
}

/// An order as tracked by the execution engine.
#[derive(Debug, Clone, PartialEq)]
pub struct BrokerOrder {
    pub client_order_id: String,
    pub intent_id: String,
    pub symbol: String,
    pub side: Side,
    pub quantity: f64,
    pub order_type: OrderType,
    pub limit_price: Option<f64>,
    /// Lifecycle state code from `order_state` (the Rust authority).
    pub state: i32,
    pub filled_qty: f64,
    pub avg_fill_price: Option<f64>,
    pub broker_order_id: Option<String>,
    pub reason: String,
    /// `(state, timestamp)` transitions in order.
    pub history: Vec<(i32, String)>,
}

impl BrokerOrder {
    pub fn new(
        client_order_id: impl Into<String>,
        intent_id: impl Into<String>,
        symbol: impl Into<String>,
        side: Side,
        quantity: f64,
    ) -> Self {
        Self {
            client_order_id: client_order_id.into(),
            intent_id: intent_id.into(),
            symbol: symbol.into(),
            side,
            quantity,
            order_type: OrderType::Market,
            limit_price: None,
            state: order_state::CREATED,
            filled_qty: 0.0,
            avg_fill_price: None,
            broker_order_id: None,
            reason: String::new(),
            history: Vec::new(),
        }
    }

    /// Advance to `state`, appending the transition. Illegal transitions are
    /// rejected (fail closed) and leave the order untouched.
    pub fn advance(&self, state: i32, timestamp: impl Into<String>, reason: &str) -> Option<Self> {
        if !order_state::is_legal_transition(self.state, state) {
            return None;
        }
        let mut next = self.clone();
        next.state = state;
        if !reason.is_empty() {
            next.reason = reason.to_string();
        }
        next.history.push((state, timestamp.into()));
        Some(next)
    }

    pub fn is_terminal(&self) -> bool {
        order_state::is_terminal(self.state)
    }
}

/// One fill report from the broker (or paper) adapter.
#[derive(Debug, Clone, PartialEq)]
pub struct Fill {
    pub client_order_id: String,
    pub broker_order_id: Option<String>,
    pub symbol: String,
    pub side: Side,
    pub fill_qty: f64,
    pub fill_price: f64,
    pub commission: f64,
    pub timestamp: String,
    pub partial: bool,
}

/// Signed-quantity verdict: ``0`` flat, ``1`` long, ``2`` short.
///
/// The one place that reads a position's sign, so the flat test, the
/// ``LONG``/``SHORT`` label and the Python bridge can never drift apart.
pub fn position_state(quantity: f64) -> i32 {
    if quantity == 0.0 {
        0
    } else if quantity > 0.0 {
        1
    } else {
        2
    }
}

/// Side labels indexed by [`position_state`]; index 0 is the flat sentinel.
pub const POSITION_SIDES: [Option<&'static str>; 3] = [None, Some("LONG"), Some("SHORT")];

/// Mark-to-market P&L of a signed quantity; flat legs have no basis.
pub fn position_unrealized(quantity: f64, avg_price: f64, mark_price: f64) -> f64 {
    if quantity == 0.0 {
        0.0
    } else {
        (mark_price - avg_price) * quantity
    }
}

/// Net position in one symbol.
#[derive(Debug, Clone, PartialEq)]
pub struct Position {
    pub symbol: String,
    /// Signed: +long / -short / 0 flat.
    pub quantity: f64,
    pub avg_price: f64,
    pub realized_pnl: f64,
}

impl Position {
    pub fn flat_at(symbol: impl Into<String>) -> Self {
        Self {
            symbol: symbol.into(),
            quantity: 0.0,
            avg_price: 0.0,
            realized_pnl: 0.0,
        }
    }

    pub fn is_flat(&self) -> bool {
        position_state(self.quantity) == 0
    }

    pub fn unrealized(&self, mark_price: f64) -> f64 {
        position_unrealized(self.quantity, self.avg_price, mark_price)
    }

    /// Apply a fill: weighted-average entry when adding, realized P&L when
    /// reducing, and a clean flip when the fill crosses through flat.
    pub fn apply_fill(&mut self, side: Side, qty: f64, price: f64) {
        if qty <= 0.0 {
            return;
        }
        let signed = side.direction() * qty;
        let same_direction = self.is_flat() || (self.quantity > 0.0) == (signed > 0.0);

        if same_direction {
            let total = self.quantity + signed;
            if total == 0.0 {
                self.quantity = 0.0;
                self.avg_price = 0.0;
                return;
            }
            let notional = self.quantity.abs() * self.avg_price + qty * price;
            self.quantity = total;
            self.avg_price = notional / total.abs();
            return;
        }

        let closing = qty.min(self.quantity.abs());
        let direction = if self.quantity > 0.0 { 1.0 } else { -1.0 };
        self.realized_pnl += (price - self.avg_price) * closing * direction;
        let remaining = self.quantity + signed;

        if remaining == 0.0 {
            self.quantity = 0.0;
            self.avg_price = 0.0;
        } else if (remaining > 0.0) == (self.quantity > 0.0) {
            // Partial close: entry basis unchanged.
            self.quantity = remaining;
        } else {
            // Flipped through flat: the excess opens at the fill price.
            self.quantity = remaining;
            self.avg_price = price;
        }
    }
}

/// Capital view handed to risk and reconciliation.
#[derive(Debug, Clone, PartialEq)]
pub struct AccountSnapshot {
    pub equity: f64,
    pub available_capital: f64,
    pub day_pnl: f64,
    pub currency: String,
    pub account_id: String,
    pub environment: String,
}

impl AccountSnapshot {
    pub fn new(equity: f64, available_capital: f64) -> Self {
        Self {
            equity,
            available_capital,
            day_pnl: 0.0,
            currency: "INR".to_string(),
            account_id: String::new(),
            environment: String::new(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn side_round_trip_and_direction() {
        assert_eq!(Side::parse("BUY"), Some(Side::Buy));
        assert_eq!(Side::parse("SELL"), Some(Side::Sell));
        assert_eq!(Side::parse("HOLD"), None);
        assert_eq!(Side::Buy.direction(), 1.0);
        assert_eq!(Side::Sell.direction(), -1.0);
    }

    #[test]
    fn intent_id_is_deterministic() {
        let a = make_intent_id("sma", "1.0.0", 42, 0);
        let b = make_intent_id("sma", "1.0.0", 42, 0);
        assert_eq!(a, b);
        assert_eq!(a, "sma:1.0.0:42:0");
        assert_ne!(a, make_intent_id("sma", "1.0.0", 42, 1));
    }

    #[test]
    fn broker_order_starts_created() {
        let order = BrokerOrder::new("c-1", "i-1", "AAPL", Side::Buy, 10.0);
        assert_eq!(order.state, order_state::CREATED);
        assert!(order.history.is_empty());
        assert!(!order.is_terminal());
    }

    #[test]
    fn broker_order_rejects_illegal_transition() {
        let order = BrokerOrder::new("c-1", "i-1", "AAPL", Side::Buy, 10.0);
        // CREATED cannot jump straight to FILLED.
        assert!(order.advance(order_state::FILLED, "t0", "").is_none());
        assert_eq!(order.state, order_state::CREATED);
    }

    #[test]
    fn broker_order_records_legal_transition() {
        let order = BrokerOrder::new("c-1", "i-1", "AAPL", Side::Buy, 10.0);
        let legal = (0..order_state::STATE_COUNT)
            .find(|&to| order_state::is_legal_transition(order.state, to))
            .expect("CREATED must have at least one legal exit");
        let next = order.advance(legal, "t1", "submitted").unwrap();
        assert_eq!(next.state, legal);
        assert_eq!(next.reason, "submitted");
        assert_eq!(next.history, vec![(legal, "t1".to_string())]);
    }

    #[test]
    fn position_starts_flat() {
        let position = Position::flat_at("AAPL");
        assert!(position.is_flat());
        assert_eq!(position.unrealized(150.0), 0.0);
    }

    #[test]
    fn position_state_reads_the_sign_once() {
        assert_eq!(position_state(0.0), 0);
        assert_eq!(position_state(0.1), 1);
        assert_eq!(position_state(-0.1), 2);
        assert_eq!(position_state(-0.0), 0);
        assert_eq!(position_state(f64::NAN), 2);
        assert_eq!(POSITION_SIDES[0], None);
        assert_eq!(POSITION_SIDES[1], Some("LONG"));
        assert_eq!(POSITION_SIDES[2], Some("SHORT"));
        let short = Position {
            symbol: "AAPL".to_string(),
            quantity: -10.0,
            avg_price: 100.0,
            realized_pnl: 0.0,
        };
        assert_eq!(short.unrealized(90.0), 100.0);
        assert_eq!(
            short.unrealized(90.0),
            position_unrealized(short.quantity, short.avg_price, 90.0)
        );
    }

    #[test]
    fn position_opens_and_marks() {
        let mut position = Position::flat_at("AAPL");
        position.apply_fill(Side::Buy, 10.0, 100.0);
        assert_eq!(position.quantity, 10.0);
        assert_eq!(position.avg_price, 100.0);
        assert_eq!(position.unrealized(110.0), 100.0);
    }

    #[test]
    fn position_averages_when_adding() {
        let mut position = Position::flat_at("AAPL");
        position.apply_fill(Side::Buy, 10.0, 100.0);
        position.apply_fill(Side::Buy, 10.0, 120.0);
        assert_eq!(position.quantity, 20.0);
        assert_eq!(position.avg_price, 110.0);
        assert_eq!(position.realized_pnl, 0.0);
    }

    #[test]
    fn position_realizes_on_close() {
        let mut position = Position::flat_at("AAPL");
        position.apply_fill(Side::Buy, 10.0, 100.0);
        position.apply_fill(Side::Sell, 10.0, 110.0);
        assert!(position.is_flat());
        assert_eq!(position.realized_pnl, 100.0);
        assert_eq!(position.avg_price, 0.0);
    }

    #[test]
    fn position_partial_close_keeps_basis() {
        let mut position = Position::flat_at("AAPL");
        position.apply_fill(Side::Buy, 10.0, 100.0);
        position.apply_fill(Side::Sell, 4.0, 110.0);
        assert_eq!(position.quantity, 6.0);
        assert_eq!(position.avg_price, 100.0);
        assert_eq!(position.realized_pnl, 40.0);
    }

    #[test]
    fn position_flip_reopens_at_fill_price() {
        let mut position = Position::flat_at("AAPL");
        position.apply_fill(Side::Buy, 10.0, 100.0);
        position.apply_fill(Side::Sell, 15.0, 110.0);
        assert_eq!(position.quantity, -5.0);
        assert_eq!(position.avg_price, 110.0);
        assert_eq!(position.realized_pnl, 100.0);
    }

    #[test]
    fn position_short_realizes_correctly() {
        let mut position = Position::flat_at("AAPL");
        position.apply_fill(Side::Sell, 10.0, 100.0);
        assert_eq!(position.quantity, -10.0);
        // Short profits when price falls.
        assert_eq!(position.unrealized(90.0), 100.0);
        position.apply_fill(Side::Buy, 10.0, 90.0);
        assert!(position.is_flat());
        assert_eq!(position.realized_pnl, 100.0);
    }

    #[test]
    fn position_ignores_non_positive_fill() {
        let mut position = Position::flat_at("AAPL");
        position.apply_fill(Side::Buy, 0.0, 100.0);
        position.apply_fill(Side::Buy, -5.0, 100.0);
        assert!(position.is_flat());
    }

    #[test]
    fn account_snapshot_defaults() {
        let account = AccountSnapshot::new(100_000.0, 50_000.0);
        assert_eq!(account.currency, "INR");
        assert_eq!(account.day_pnl, 0.0);
    }
}
