//! EXECUTION core — engine, planner, modes, lifecycle, readiness, ledger.
//!
//! Rust port of the pure-computation slices of `08_execution`:
//!
//! | Python (`08_execution/execution/...`) | Rust (here) |
//! |---|---|
//! | `engine.py` (`ExecutionEngine`) | `ExecutionEngine` (same guards, same messages) |
//! | `planner.py` (+ `(0,1]` prefs) | `plan_order`, `ExecutionPreferences::new` |
//! | `modes.py` (arm table, gates, resolve) | `LiveArm`, `ModeGates`, `resolve_mode`, … |
//! | `runtime/lifecycle.py` | `LifecycleState`, `StrategyLifecycle` |
//! | `runtime/session.py::check_live_readiness` | `check_live_readiness` (reason strings) |
//! | `broker/gates.py::risk_configuration_valid` | `risk_configuration_mask` (bitmask helper; `live_readiness::risk_reasons` owns the gate and its wording) |
//! | `portfolio/ledger.py` (fold economics) | `Ledger::apply_fill` (+ snapshot/state) |
//! | `models/position.py` (flat, mark math) | `execution::position_state` / `position_unrealized` |
//! | `portfolio/reconcile.py` (pure fns) | `reconcile_*`, `verdict_of` |
//! | `journal.py` (`_pct`, segments) | `percentile`, `segments` |
//!
//! Reused untouched: `order_state` (lifecycle table), `execution` (order/
//! fill/position models, position verdicts), `risk_engine::py_float` (reason
//! formatting).
//! Stays Python: `LiveSession` pipeline, broker adapters/SDKs, market-data
//! providers, strategy runtime/inspector, adaptive/regime/ML, journal + replay
//! file IO, UI. Session-added reason prefixes (`"{key}: …"`, live-block extra)
//! are caller-side — documented, not duplicated.

use std::collections::{BTreeSet, HashMap};

use crate::execution::{
    position_state, AccountSnapshot, BrokerOrder, Fill, OrderType, Position, Side, POSITION_SIDES,
};
use crate::order_state;
use crate::risk_engine::py_float;

// ── order-state names (mirror OrderState values) ──────────────────────────

/// Parse a lifecycle state name (mirror `OrderState(broker_state)`).
pub fn parse_state(name: &str) -> Option<i32> {
    match name {
        "CREATED" => Some(order_state::CREATED),
        "VALIDATED" => Some(order_state::VALIDATED),
        "SUBMITTED" => Some(order_state::SUBMITTED),
        "ACKNOWLEDGED" => Some(order_state::ACKNOWLEDGED),
        "PARTIALLY_FILLED" => Some(order_state::PARTIALLY_FILLED),
        "FILLED" => Some(order_state::FILLED),
        "REJECTED" => Some(order_state::REJECTED),
        "CANCEL_PENDING" => Some(order_state::CANCEL_PENDING),
        "CANCELLED" => Some(order_state::CANCELLED),
        "MODIFY_PENDING" => Some(order_state::MODIFY_PENDING),
        "MODIFIED" => Some(order_state::MODIFIED),
        "EXPIRED" => Some(order_state::EXPIRED),
        "UNKNOWN" => Some(order_state::UNKNOWN),
        _ => None,
    }
}

/// Lifecycle state name (mirror `OrderState.value`).
pub fn state_name(code: i32) -> &'static str {
    match code {
        x if x == order_state::CREATED => "CREATED",
        x if x == order_state::VALIDATED => "VALIDATED",
        x if x == order_state::SUBMITTED => "SUBMITTED",
        x if x == order_state::ACKNOWLEDGED => "ACKNOWLEDGED",
        x if x == order_state::PARTIALLY_FILLED => "PARTIALLY_FILLED",
        x if x == order_state::FILLED => "FILLED",
        x if x == order_state::REJECTED => "REJECTED",
        x if x == order_state::CANCEL_PENDING => "CANCEL_PENDING",
        x if x == order_state::CANCELLED => "CANCELLED",
        x if x == order_state::MODIFY_PENDING => "MODIFY_PENDING",
        x if x == order_state::MODIFIED => "MODIFIED",
        x if x == order_state::EXPIRED => "EXPIRED",
        x if x == order_state::UNKNOWN => "UNKNOWN",
        _ => "?",
    }
}

// ── engine.py ─────────────────────────────────────────────────────────────

/// Broker-independent order lifecycle tracker (mirror `ExecutionEngine`).
///
/// Duplicate maps survive in-memory only; checkpoint persistence is the
/// caller's `snapshot`/`restore` round-trip (file IO stays Python).
pub struct ExecutionEngine {
    orders: HashMap<String, BrokerOrder>,
    by_intent: HashMap<String, String>,
    now: String,
}

impl ExecutionEngine {
    pub fn new() -> Self {
        Self {
            orders: HashMap::new(),
            by_intent: HashMap::new(),
            // Quirky default preserved verbatim from `_now()` (no clock).
            now: "1970-01-01T00:00:00+00:00".to_string(),
        }
    }

    /// Fixed clock for tests/sessions (mirror the injected `clock.now_iso`).
    pub fn with_now(now: impl Into<String>) -> Self {
        let mut engine = Self::new();
        engine.now = now.into();
        engine
    }

    /// Register a CREATED order; duplicate ids/intents are rejected.
    pub fn create(&mut self, order: BrokerOrder) -> Result<BrokerOrder, String> {
        if self.orders.contains_key(&order.client_order_id) {
            return Err(format!(
                "duplicate client order id: {}",
                order.client_order_id
            ));
        }
        if self.by_intent.contains_key(&order.intent_id) {
            return Err(format!(
                "duplicate intent already has order: {}",
                order.intent_id
            ));
        }
        if order.state != order_state::CREATED {
            return Err("only CREATED orders may enter the engine".to_string());
        }
        // Stamp, not a transition: Python's `with_state` appends history
        // without consulting the table (CREATED -> CREATED is not an edge).
        let mut stamped = order.clone();
        stamped
            .history
            .push((order_state::CREATED, self.now.clone()));
        self.by_intent
            .insert(stamped.intent_id.clone(), stamped.client_order_id.clone());
        self.orders
            .insert(stamped.client_order_id.clone(), stamped.clone());
        Ok(stamped)
    }

    pub fn get(&self, client_order_id: &str) -> Option<&BrokerOrder> {
        self.orders.get(client_order_id)
    }

    pub fn by_intent(&self, intent_id: &str) -> Option<&BrokerOrder> {
        self.by_intent
            .get(intent_id)
            .and_then(|id| self.orders.get(id))
    }

    /// Advance along a legal edge (UNKNOWN exits need `reconcile`).
    pub fn transition(
        &mut self,
        client_order_id: &str,
        target: i32,
        reason: &str,
    ) -> Result<BrokerOrder, String> {
        let order = self
            .orders
            .get(client_order_id)
            .ok_or_else(|| format!("unknown order: {client_order_id}"))?;
        if order.state == order_state::UNKNOWN {
            return Err("UNKNOWN exits only via reconcile()".to_string());
        }
        if !order_state::is_legal_transition(order.state, target) {
            return Err(format!(
                "illegal {} -> {}",
                state_name(order.state),
                state_name(target)
            ));
        }
        let why = if target == order_state::UNKNOWN && reason.is_empty() {
            "state unknown"
        } else {
            reason
        };
        let advanced = order
            .advance(target, self.now.clone(), why)
            .expect("legality checked above");
        self.orders
            .insert(client_order_id.to_string(), advanced.clone());
        Ok(advanced)
    }

    /// Fold a fill report (partial-aware average price). Terminal orders
    /// reject fills. An untracked id falls back to the passed order — then
    /// stored, exactly like Python.
    pub fn apply_fill(&mut self, order: &BrokerOrder, fill: &Fill) -> Result<BrokerOrder, String> {
        let stored = self
            .orders
            .get(&order.client_order_id)
            .unwrap_or(order)
            .clone();
        if order_state::is_terminal(stored.state) {
            return Err(format!(
                "fill for terminal order {}",
                stored.client_order_id
            ));
        }
        let prev_qty = stored.filled_qty;
        let prev_avg = stored.avg_fill_price.unwrap_or(0.0);
        let new_qty = prev_qty + fill.fill_qty;
        let new_avg = if prev_qty <= 0.0 {
            fill.fill_price
        } else {
            (prev_avg * prev_qty + fill.fill_price * fill.fill_qty) / new_qty
        };
        let updated = BrokerOrder {
            filled_qty: new_qty,
            avg_fill_price: Some(new_avg),
            broker_order_id: fill.broker_order_id.clone().or(stored.broker_order_id),
            ..stored
        };
        self.orders
            .insert(updated.client_order_id.clone(), updated.clone());
        Ok(updated)
    }

    /// Resolve any order (including UNKNOWN) against a broker snapshot.
    /// Never creates fills — only aligns state.
    pub fn reconcile(
        &mut self,
        client_order_id: &str,
        broker_state: &str,
        broker_filled_qty: f64,
        reason: &str,
    ) -> Result<BrokerOrder, String> {
        if !self.orders.contains_key(client_order_id) {
            return Err(format!("unknown order: {client_order_id}"));
        }
        let target = parse_state(broker_state)
            .ok_or_else(|| format!("broker reported unknown state: {broker_state}"))?;
        if target == order_state::UNKNOWN {
            return Err("reconcile cannot resolve UNKNOWN to UNKNOWN".to_string());
        }
        let order = self
            .orders
            .get(client_order_id)
            .expect("checked above")
            .clone();
        let why = if reason.is_empty() {
            "reconciled"
        } else {
            reason
        };
        // Direct stamp: Python's `with_state` bypasses the table here, so any
        // non-UNKNOWN target resolves (UNKNOWN sources included).
        let mut updated = BrokerOrder {
            state: target,
            reason: if why.is_empty() {
                order.reason.clone()
            } else {
                why.to_string()
            },
            history: {
                let mut h = order.history.clone();
                h.push((target, self.now.clone()));
                h
            },
            ..order
        };
        if broker_filled_qty != 0.0 {
            updated.filled_qty = broker_filled_qty;
        }
        self.orders
            .insert(client_order_id.to_string(), updated.clone());
        Ok(updated)
    }

    /// Orders not in a terminal state.
    pub fn open_orders(&self) -> Vec<&BrokerOrder> {
        self.orders.values().filter(|o| !o.is_terminal()).collect()
    }

    pub fn order_count(&self) -> usize {
        self.orders.len()
    }

    /// Export idempotency state (JSON-safe shapes; file IO stays Python).
    pub fn snapshot(&self) -> Vec<OrderSnapshot> {
        self.orders
            .values()
            .map(|o| OrderSnapshot {
                client_order_id: o.client_order_id.clone(),
                intent_id: o.intent_id.clone(),
                symbol: o.symbol.clone(),
                side: o.side.as_str().to_string(),
                quantity: o.quantity,
                order_type: o.order_type.as_str().to_string(),
                limit_price: o.limit_price,
                state: state_name(o.state).to_string(),
                filled_qty: o.filled_qty,
                avg_fill_price: o.avg_fill_price,
                broker_order_id: o.broker_order_id.clone(),
                reason: o.reason.clone(),
                history: o
                    .history
                    .iter()
                    .map(|(s, t)| (state_name(*s).to_string(), t.clone()))
                    .collect(),
            })
            .collect()
    }

    /// Rebuild maps from a snapshot. Corrupt payloads fail closed.
    pub fn restore(&mut self, orders: Vec<RestoredOrder>) -> Result<(), String> {
        let mut rebuilt: HashMap<String, BrokerOrder> = HashMap::new();
        let mut by_intent: HashMap<String, String> = HashMap::new();
        for item in orders {
            let state = parse_state(&item.state).ok_or_else(|| {
                format!(
                    "invalid engine snapshot order: unknown state '{}'",
                    item.state
                )
            })?;
            let side = Side::parse(&item.side).ok_or_else(|| {
                format!(
                    "invalid engine snapshot order: unknown side '{}'",
                    item.side
                )
            })?;
            let order_type =
                crate::execution::OrderType::parse(&item.order_type).ok_or_else(|| {
                    format!(
                        "invalid engine snapshot order: unknown order type '{}'",
                        item.order_type
                    )
                })?;
            let history = item
                .history
                .iter()
                .map(|(s, t)| {
                    parse_state(s).map(|code| (code, t.clone())).ok_or_else(|| {
                        format!("invalid engine snapshot order: unknown history state '{s}'")
                    })
                })
                .collect::<Result<Vec<_>, _>>()?;
            let order = BrokerOrder {
                client_order_id: item.client_order_id.clone(),
                intent_id: item.intent_id.clone(),
                symbol: item.symbol.clone(),
                side,
                quantity: item.quantity,
                order_type,
                limit_price: item.limit_price,
                state,
                filled_qty: item.filled_qty,
                avg_fill_price: item.avg_fill_price,
                broker_order_id: item.broker_order_id.clone(),
                reason: item.reason.clone(),
                history,
            };
            if rebuilt.contains_key(&order.client_order_id) {
                return Err(format!(
                    "duplicate client order id in snapshot: {}",
                    order.client_order_id
                ));
            }
            if by_intent.contains_key(&order.intent_id) {
                return Err(format!("duplicate intent in snapshot: {}", order.intent_id));
            }
            by_intent.insert(order.intent_id.clone(), order.client_order_id.clone());
            rebuilt.insert(order.client_order_id.clone(), order);
        }
        self.orders = rebuilt;
        self.by_intent = by_intent;
        Ok(())
    }
}

impl Default for ExecutionEngine {
    fn default() -> Self {
        Self::new()
    }
}

/// Snapshot shape (mirror `snapshot()` dicts; `state` spelled by name).
#[derive(Debug, Clone, PartialEq)]
pub struct OrderSnapshot {
    pub client_order_id: String,
    pub intent_id: String,
    pub symbol: String,
    pub side: String,
    pub quantity: f64,
    pub order_type: String,
    pub limit_price: Option<f64>,
    pub state: String,
    pub filled_qty: f64,
    pub avg_fill_price: Option<f64>,
    pub broker_order_id: Option<String>,
    pub reason: String,
    pub history: Vec<(String, String)>,
}

/// Restore input (mirror `restore()` item reads; missing keys are a
/// caller-side type error, corrupt values fail closed here).
#[derive(Debug, Clone)]
pub struct RestoredOrder {
    pub client_order_id: String,
    pub intent_id: String,
    pub symbol: String,
    pub side: String,
    pub quantity: f64,
    pub order_type: String,
    pub limit_price: Option<f64>,
    pub state: String,
    pub filled_qty: f64,
    pub avg_fill_price: Option<f64>,
    pub broker_order_id: Option<String>,
    pub reason: String,
    pub history: Vec<(String, String)>,
}

impl From<&OrderSnapshot> for RestoredOrder {
    fn from(s: &OrderSnapshot) -> Self {
        Self {
            client_order_id: s.client_order_id.clone(),
            intent_id: s.intent_id.clone(),
            symbol: s.symbol.clone(),
            side: s.side.clone(),
            quantity: s.quantity,
            order_type: s.order_type.clone(),
            limit_price: s.limit_price,
            state: s.state.clone(),
            filled_qty: s.filled_qty,
            avg_fill_price: s.avg_fill_price,
            broker_order_id: s.broker_order_id.clone(),
            reason: s.reason.clone(),
            history: s.history.clone(),
        }
    }
}

// ── planner.py ────────────────────────────────────────────────────────────

/// Advisory preferences (never risk overrides).
#[derive(Debug, Clone, PartialEq)]
pub struct ExecutionPreferences {
    pub prefer_limit: bool,
    pub size_multiplier: f64,
}

impl ExecutionPreferences {
    /// `size_multiplier` must be in `(0, 1]` — shrink-only, never enlarge.
    pub fn new(prefer_limit: bool, size_multiplier: f64) -> Result<Self, String> {
        match multiplier_problem(size_multiplier) {
            None => Ok(Self {
                prefer_limit,
                size_multiplier,
            }),
            Some(problem) => Err(problem.to_string()),
        }
    }
}

/// Why an advisory `size_multiplier` is unusable — `(0, 1]` shrinks, never
/// enlarges. `None` means the value is usable.
pub fn multiplier_problem(size_multiplier: f64) -> Option<&'static str> {
    if 0.0 < size_multiplier && size_multiplier <= 1.0 {
        None
    } else {
        Some("size_multiplier must be in (0, 1]")
    }
}

/// The advisory multiplier as the planner can use it: only `(0, 1]` is
/// honoured, and anything else — including a value the caller could not read
/// (`defined` false) — means "no shrink".
pub fn narrow_multiplier(defined: bool, raw: f64) -> f64 {
    if defined && multiplier_problem(raw).is_none() {
        raw
    } else {
        1.0
    }
}

/// Default order quantity: what the account can afford at `price`, capped by
/// the policy's per-order ceiling. A non-positive price buys nothing.
pub fn default_quantity(available_capital: f64, price: f64, max_order_qty: f64) -> f64 {
    if price <= 0.0 {
        return 0.0;
    }
    (available_capital / price).min(max_order_qty).max(0.0)
}

impl Default for ExecutionPreferences {
    fn default() -> Self {
        Self {
            prefer_limit: false,
            size_multiplier: 1.0,
        }
    }
}

/// Planner input (mirror the `ExecutionIntent` fields read + reference price).
pub struct PlanIntent {
    pub intent_id: String,
    pub symbol: String,
    pub side: Side,
    pub quantity: f64,
    pub preferred_order_type: OrderType,
}

/// Deterministic intent → order spec. Bracket legs stay empty data.
pub fn plan_order(
    intent: &PlanIntent,
    reference_price: f64,
    prefs: &ExecutionPreferences,
) -> crate::execution::OrderPlan {
    let quantity = intent.quantity * prefs.size_multiplier;
    let mut order_type = intent.preferred_order_type;
    let mut limit_price = None;
    if prefs.prefer_limit || order_type == OrderType::Limit {
        order_type = OrderType::Limit;
        limit_price = Some(reference_price);
    }
    crate::execution::OrderPlan {
        intent_id: intent.intent_id.clone(),
        symbol: intent.symbol.clone(),
        side: intent.side,
        quantity,
        order_type,
        limit_price,
        time_in_force: "DAY".to_string(),
    }
}

// ── modes.py ──────────────────────────────────────────────────────────────

/// Execution venue mode. Default is PAPER, always.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExecutionMode {
    Paper,
    Sandbox,
    Live,
}

impl ExecutionMode {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Paper => "PAPER",
            Self::Sandbox => "SANDBOX",
            Self::Live => "LIVE",
        }
    }

    /// Mode for its wire label (`as_str` round trip); `None` if unknown.
    pub fn from_label(name: &str) -> Option<Self> {
        match name {
            "PAPER" => Some(Self::Paper),
            "SANDBOX" => Some(Self::Sandbox),
            "LIVE" => Some(Self::Live),
            _ => None,
        }
    }
}

/// Explicit live arming state. Credentials alone never arm anything.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum LiveArm {
    Disarmed,
    Arming,
    Armed,
    Running,
    Halted,
}

impl LiveArm {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Disarmed => "DISARMED",
            Self::Arming => "ARMING",
            Self::Armed => "ARMED",
            Self::Running => "RUNNING",
            Self::Halted => "HALTED",
        }
    }

    fn allowed(&self) -> &'static [LiveArm] {
        match self {
            Self::Disarmed => &[LiveArm::Arming],
            Self::Arming => &[LiveArm::Armed, LiveArm::Disarmed],
            Self::Armed => &[LiveArm::Running, LiveArm::Halted, LiveArm::Disarmed],
            Self::Running => &[LiveArm::Halted, LiveArm::Disarmed],
            Self::Halted => &[LiveArm::Disarmed],
        }
    }
}

/// Validate one arming step; returns target or the exact error.
pub fn arm_transition(current: LiveArm, target: LiveArm) -> Result<LiveArm, String> {
    if current.allowed().contains(&target) {
        Ok(target)
    } else {
        Err(format!(
            "illegal arming transition {} -> {}",
            current.as_str(),
            target.as_str()
        ))
    }
}

/// The five mandatory live gates.
#[derive(Debug, Clone, PartialEq)]
pub struct ModeGates {
    pub live_trading_enabled: bool,
    pub broker_live_enabled: bool,
    pub account_confirmed: bool,
    pub risk_limits_valid: bool,
    pub kill_switch_off: bool,
}

impl Default for ModeGates {
    fn default() -> Self {
        Self {
            live_trading_enabled: false,
            broker_live_enabled: false,
            account_confirmed: false,
            risk_limits_valid: false,
            kill_switch_off: false,
        }
    }
}

impl ModeGates {
    /// The gates in declaration order — the order [`bool_flags_to_mask`] packs.
    pub fn as_flags(&self) -> [bool; GATE_COUNT] {
        [
            self.live_trading_enabled,
            self.broker_live_enabled,
            self.account_confirmed,
            self.risk_limits_valid,
            self.kill_switch_off,
        ]
    }

    pub fn all_satisfied(&self) -> bool {
        self.live_trading_enabled
            && self.broker_live_enabled
            && self.account_confirmed
            && self.risk_limits_valid
            && self.kill_switch_off
    }

    pub fn missing(&self) -> Vec<&'static str> {
        let mut out = Vec::new();
        if !self.live_trading_enabled {
            out.push("LIVE_TRADING_ENABLED");
        }
        if !self.broker_live_enabled {
            out.push("BROKER_LIVE_ENABLED");
        }
        if !self.account_confirmed {
            out.push("ACCOUNT_CONFIRMED");
        }
        if !self.risk_limits_valid {
            out.push("RISK_LIMITS_VALID");
        }
        if !self.kill_switch_off {
            out.push("KILL_SWITCH_OFF");
        }
        out
    }
}

/// Read gates from configuration: only the exact string `"true"`
/// (case-insensitive, trimmed) counts as ON — `"1"`/`"yes"` are OFF.
pub fn gates_from_env(get: &dyn Fn(&str) -> String) -> ModeGates {
    let flag = |name: &str| flag_on(&get(name));
    ModeGates {
        live_trading_enabled: flag("LIVE_TRADING_ENABLED"),
        broker_live_enabled: flag("BROKER_LIVE_ENABLED"),
        account_confirmed: flag("ACCOUNT_CONFIRMED"),
        risk_limits_valid: flag("RISK_LIMITS_VALID"),
        kill_switch_off: flag("KILL_SWITCH_OFF"),
    }
}

/// Effective mode: LIVE without all gates degrades to PAPER (never silent).
pub fn resolve_mode(requested: ExecutionMode, gates: &ModeGates) -> (ExecutionMode, Vec<String>) {
    if requested == ExecutionMode::Live && gates.all_satisfied() {
        return (ExecutionMode::Live, Vec::new());
    }
    if requested == ExecutionMode::Live {
        return (
            ExecutionMode::Paper,
            gates
                .missing()
                .iter()
                .map(|n| format!("live gate off: {n}"))
                .collect(),
        );
    }
    if requested == ExecutionMode::Sandbox {
        return (ExecutionMode::Sandbox, Vec::new());
    }
    (ExecutionMode::Paper, Vec::new())
}

/// One gate value counts as ON only when it reads exactly `"true"`
/// (trimmed, case-insensitive): `"1"`/`"yes"` stay OFF.
fn flag_on(raw: &str) -> bool {
    raw.trim().to_lowercase() == "true"
}

/// Number of mandatory live gates.
pub const GATE_COUNT: usize = 5;

/// Gate flags in declaration order → mask (bit 0 = live trading switch).
/// `None` when the value list is not exactly [`GATE_COUNT`] flags.
pub fn flags_to_mask(values: &[&str]) -> Option<u32> {
    if values.len() != GATE_COUNT {
        return None;
    }
    let mut mask = 0u32;
    for (index, raw) in values.iter().enumerate() {
        if flag_on(raw) {
            mask |= 1 << index;
        }
    }
    Some(mask)
}

/// Gate booleans in declaration order → mask, for a holder that already read
/// the values. Same bit order as [`flags_to_mask`]; `None` when the list is not
/// exactly [`GATE_COUNT`] flags.
pub fn bool_flags_to_mask(values: &[bool]) -> Option<u32> {
    if values.len() != GATE_COUNT {
        return None;
    }
    let mut mask = 0u32;
    for (index, on) in values.iter().enumerate() {
        if *on {
            mask |= 1 << index;
        }
    }
    Some(mask)
}

/// Gates as the mask encodes them.
pub fn gates_from_mask(mask: u32) -> ModeGates {
    ModeGates {
        live_trading_enabled: mask & (1 << 0) != 0,
        broker_live_enabled: mask & (1 << 1) != 0,
        account_confirmed: mask & (1 << 2) != 0,
        risk_limits_valid: mask & (1 << 3) != 0,
        kill_switch_off: mask & (1 << 4) != 0,
    }
}

/// Names of the unsatisfied gates, in gate order.
pub fn missing_gates(mask: u32) -> Vec<&'static str> {
    gates_from_mask(mask).missing()
}

/// Effective mode for a requested-mode label plus the gate mask.
/// `None` when the label is not a mode this module knows.
pub fn resolve_mode_mask(requested: &str, mask: u32) -> Option<(ExecutionMode, Vec<String>)> {
    ExecutionMode::from_label(requested).map(|mode| resolve_mode(mode, &gates_from_mask(mask)))
}

// ── runtime/lifecycle.py ──────────────────────────────────────────────────

/// Strategy lifecycle states.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum LifecycleState {
    Created,
    Validating,
    WarmingUp,
    Ready,
    Running,
    Paused,
    Stopping,
    Stopped,
    Error,
    Recovering,
    Reconciling,
}

impl LifecycleState {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Created => "CREATED",
            Self::Validating => "VALIDATING",
            Self::WarmingUp => "WARMING_UP",
            Self::Ready => "READY",
            Self::Running => "RUNNING",
            Self::Paused => "PAUSED",
            Self::Stopping => "STOPPING",
            Self::Stopped => "STOPPED",
            Self::Error => "ERROR",
            Self::Recovering => "RECOVERING",
            Self::Reconciling => "RECONCILING",
        }
    }

    fn allowed(&self) -> &'static [LifecycleState] {
        use LifecycleState::*;
        match self {
            Created => &[Validating, Recovering, Stopped],
            Validating => &[WarmingUp, Error, Stopped],
            WarmingUp => &[Ready, Error, Stopped],
            Ready => &[Running, Stopped],
            Running => &[Paused, Stopping, Error],
            Paused => &[Running, Stopping],
            Stopping => &[Stopped, Error],
            Stopped => &[Recovering],
            Error => &[Recovering, Stopped],
            Recovering => &[Validating, Reconciling, Error, Stopped],
            Reconciling => &[Validating, Error, Stopped],
        }
    }
}

/// One strategy instance's lifecycle.
#[derive(Debug, Clone)]
pub struct StrategyLifecycle {
    pub state: LifecycleState,
    pub reason: String,
}

impl StrategyLifecycle {
    pub fn new() -> Self {
        Self {
            state: LifecycleState::Created,
            reason: String::new(),
        }
    }

    pub fn transition(&mut self, target: LifecycleState, reason: &str) -> Result<(), String> {
        if !self.state.allowed().contains(&target) {
            return Err(format!(
                "illegal transition {} -> {}",
                self.state.as_str(),
                target.as_str()
            ));
        }
        self.state = target;
        self.reason = reason.to_string();
        Ok(())
    }

    /// Whether the instance may currently generate orders.
    pub fn live(&self) -> bool {
        self.state == LifecycleState::Running
    }
}

impl Default for StrategyLifecycle {
    fn default() -> Self {
        Self::new()
    }
}

// ── check_live_readiness ──────────────────────────────────────────────────

/// Strategy contract surface the 11 gates read (mirror fields used).
#[derive(Debug, Clone, PartialEq)]
pub struct ContractView {
    pub missing: Vec<String>,
    pub supports_live: bool,
    pub data_requirements: Vec<String>,
    pub warmup_bars: usize,
    pub required_broker_capabilities: Vec<String>,
}

/// The 11 pre-live gates. Any failure → not ready with reasons (exact
/// strings). Per-context `"{key}: …"` prefixes and the extra session-level
/// live-block reason are caller-side (see `LiveSession._readiness`).
#[allow(clippy::too_many_arguments)]
pub fn check_live_readiness(
    contract: &ContractView,
    data_capabilities: &[String],
    broker_capabilities: &[String],
    warmup_bars_available: usize,
    risk_policy_ok: bool,
    account_ok: bool,
    clock_ok: bool,
    reconcile_ok: bool,
    persistence_ok: bool,
    kill_ok: bool,
    observability_ok: bool,
    for_live: bool,
) -> (bool, Vec<String>) {
    let mut reasons: Vec<String> = Vec::new();
    if !contract.missing.is_empty() {
        reasons.push(format!(
            "contract incomplete: {}",
            contract.missing.join(", ")
        ));
    }
    if for_live && !contract.supports_live {
        reasons.push("strategy does not declare live support (SUPPORTS_LIVE)".to_string());
    }
    for required in &contract.data_requirements {
        if !data_capabilities.iter().any(|c| c == required) {
            reasons.push(format!("data capability missing: {required}"));
        }
    }
    for required in &contract.required_broker_capabilities {
        if !broker_capabilities.iter().any(|c| c == required) {
            reasons.push(format!("broker capability missing: {required}"));
        }
    }
    if warmup_bars_available < contract.warmup_bars {
        reasons.push(format!(
            "warmup shortfall: have {warmup_bars_available}, need {}",
            contract.warmup_bars
        ));
    }
    if !risk_policy_ok {
        reasons.push("risk policy invalid".to_string());
    }
    if !account_ok {
        reasons.push("account check failed".to_string());
    }
    if !clock_ok {
        reasons.push("clock check failed".to_string());
    }
    if !reconcile_ok {
        reasons.push("reconciliation mismatch unresolved".to_string());
    }
    if !persistence_ok {
        reasons.push("persistence unavailable".to_string());
    }
    if !kill_ok {
        reasons.push("kill switch engaged or unavailable".to_string());
    }
    if !observability_ok {
        reasons.push("observability (journal) unavailable".to_string());
    }
    let ready = reasons.is_empty();
    (ready, reasons)
}

// ── broker/gates.py::risk_configuration_valid ─────────────────────────────

/// Presence bits for the optional policy limits. A clear bit means the Python
/// model holds `None` there (check disabled) and the kernel must ignore that
/// value slot — `0.0` is NOT the absent sentinel, it is an invalid limit.
pub const PRESENT_MAX_NOTIONAL: u32 = 1 << 0;
pub const PRESENT_MAX_EXPOSURE_PCT: u32 = 1 << 1;
pub const PRESENT_DAILY_LOSS_LIMIT: u32 = 1 << 2;
pub const PRESENT_STRATEGY_LOSS_LIMIT: u32 = 1 << 3;
pub const PRESENT_MAX_ORDERS_PER_DAY: u32 = 1 << 4;
pub const PRESENT_REQUIRE_FRESH_DATA_SECONDS: u32 = 1 << 5;

/// Every policy check failed (fail-closed value for panics/wrong shapes).
pub const RISK_CONFIG_ALL_BITS: u32 = 0x3FF;

/// Active risk policy sanity check — failure bitmask, `0` = valid.
///
/// Bit i is Python's i-th reason, in `risk_configuration_valid` order:
/// 0 `max_position_qty <= 0`, 1 `max_order_qty <= 0`,
/// 2 `max_order_qty > max_position_qty`, 3-6 the four optional limits
/// (`max_notional`, `max_exposure_pct`, `daily_loss_limit`,
/// `strategy_loss_limit`) when set and `<= 0`, 7 `cooldown_seconds < 0`,
/// 8 `max_orders_per_day <= 0` when set, 9 `require_fresh_data_seconds <= 0`
/// when set. Comparisons mirror Python's operators exactly, so NaN fails no
/// check there and none here. The message table lives in the bridge: strings
/// never cross the FFI.
#[allow(clippy::too_many_arguments)]
pub fn risk_configuration_mask(
    present: u32,
    max_position_qty: f64,
    max_order_qty: f64,
    max_notional: f64,
    max_exposure_pct: f64,
    daily_loss_limit: f64,
    strategy_loss_limit: f64,
    cooldown_seconds: f64,
    max_orders_per_day: i64,
    require_fresh_data_seconds: f64,
) -> u32 {
    let mut bits = 0u32;
    if max_position_qty <= 0.0 {
        bits |= 1 << 0;
    }
    if max_order_qty <= 0.0 {
        bits |= 1 << 1;
    }
    if max_order_qty > max_position_qty {
        bits |= 1 << 2;
    }
    if present & PRESENT_MAX_NOTIONAL != 0 && max_notional <= 0.0 {
        bits |= 1 << 3;
    }
    if present & PRESENT_MAX_EXPOSURE_PCT != 0 && max_exposure_pct <= 0.0 {
        bits |= 1 << 4;
    }
    if present & PRESENT_DAILY_LOSS_LIMIT != 0 && daily_loss_limit <= 0.0 {
        bits |= 1 << 5;
    }
    if present & PRESENT_STRATEGY_LOSS_LIMIT != 0 && strategy_loss_limit <= 0.0 {
        bits |= 1 << 6;
    }
    if cooldown_seconds < 0.0 {
        bits |= 1 << 7;
    }
    if present & PRESENT_MAX_ORDERS_PER_DAY != 0 && max_orders_per_day <= 0 {
        bits |= 1 << 8;
    }
    if present & PRESENT_REQUIRE_FRESH_DATA_SECONDS != 0 && require_fresh_data_seconds <= 0.0 {
        bits |= 1 << 9;
    }
    bits
}

// ── portfolio/ledger.py ───────────────────────────────────────────────────

/// Execution-owned book. One writer; no shared state. Mirrors
/// `PositionLedger` fill economics EXACTLY — including the reduce leg
/// resetting `avg_price` to the fill price and pro-rata commission on the
/// closing leg. (This differs deliberately from the backtest `Position`
/// math, which keeps basis and prices commission per trade.)
#[derive(Debug, Clone)]
pub struct Ledger {
    starting_capital: f64,
    positions: HashMap<String, Position>,
    realized: HashMap<String, f64>,
    day_pnl: f64,
}

impl Ledger {
    pub fn new(starting_capital: f64) -> Result<Self, String> {
        if starting_capital <= 0.0 {
            return Err("starting capital must be positive".to_string());
        }
        Ok(Self {
            starting_capital,
            positions: HashMap::new(),
            realized: HashMap::new(),
            day_pnl: 0.0,
        })
    }

    /// Fold a fill; returns the updated position (flat legs read back zeroed
    /// but are dropped from the map, like Python's pop-then-get).
    pub fn apply_fill(
        &mut self,
        symbol: &str,
        side: Side,
        fill_qty: f64,
        fill_price: f64,
        commission: f64,
    ) -> Position {
        let pos = self
            .positions
            .get(symbol)
            .cloned()
            .unwrap_or_else(|| Position::flat_at(symbol));
        let signed = side.direction() * fill_qty;
        let new_qty = pos.quantity + signed;
        let updated = if pos.is_flat() || (pos.quantity > 0.0) == (signed > 0.0) {
            let total_cost = pos.avg_price * pos.quantity.abs() + fill_price * fill_qty;
            let denom = new_qty.abs();
            Position {
                symbol: symbol.to_string(),
                quantity: new_qty,
                avg_price: if denom > 0.0 { total_cost / denom } else { 0.0 },
                realized_pnl: pos.realized_pnl,
            }
        } else {
            let closing = pos.quantity.abs().min(fill_qty);
            let mut pnl = (fill_price - pos.avg_price)
                * closing
                * if pos.quantity > 0.0 { 1.0 } else { -1.0 };
            if fill_qty != 0.0 {
                pnl -= commission * (closing / fill_qty);
            }
            let realized = pos.realized_pnl + pnl;
            *self.realized.entry(symbol.to_string()).or_insert(0.0) += pnl;
            self.day_pnl += pnl;
            if new_qty.abs() > 0.0 {
                Position {
                    symbol: symbol.to_string(),
                    quantity: new_qty,
                    avg_price: fill_price,
                    realized_pnl: realized,
                }
            } else {
                Position {
                    symbol: symbol.to_string(),
                    quantity: 0.0,
                    avg_price: 0.0,
                    realized_pnl: realized,
                }
            }
        };
        if updated.is_flat() {
            self.positions.remove(symbol);
        } else {
            self.positions.insert(symbol.to_string(), updated.clone());
        }
        updated
    }

    pub fn position(&self, symbol: &str) -> Position {
        self.positions
            .get(symbol)
            .cloned()
            .unwrap_or_else(|| Position::flat_at(symbol))
    }

    pub fn all_positions(&self) -> Vec<Position> {
        self.positions.values().cloned().collect()
    }

    /// Account view: starting capital + realized + unrealized at marks.
    pub fn snapshot(&self, marks: &HashMap<String, f64>) -> AccountSnapshot {
        let unrealized: f64 = self
            .positions
            .values()
            .map(|p| p.unrealized(*marks.get(&p.symbol).unwrap_or(&p.avg_price)))
            .sum();
        let realized: f64 = self.realized.values().sum();
        let equity = self.starting_capital + realized + unrealized;
        AccountSnapshot {
            equity,
            available_capital: equity,
            day_pnl: self.day_pnl + unrealized,
            currency: "INR".to_string(),
            account_id: String::new(),
            environment: String::new(),
        }
    }

    /// `(signed qty, side, avg entry)` for position state (flat → Nones).
    pub fn strategy_state_for(&self, symbol: &str) -> (f64, Option<&'static str>, Option<f64>) {
        let pos = self.position(symbol);
        let state = position_state(pos.quantity);
        if state == 0 {
            (0.0, None, None)
        } else {
            (
                pos.quantity,
                POSITION_SIDES[state as usize],
                Some(pos.avg_price),
            )
        }
    }
}

// ── portfolio/reconcile.py (pure fns) ─────────────────────────────────────

/// One local-vs-broker mismatch.
#[derive(Debug, Clone, PartialEq)]
pub struct Mismatch {
    pub kind: &'static str,
    pub id: String,
    pub local: String,
    pub broker: String,
}

/// Comparison report. Unresolved mismatch blocks live.
#[derive(Debug, Clone, PartialEq)]
pub struct ReconReport {
    pub matched: bool,
    pub mismatches: Vec<Mismatch>,
    pub checked_at: String,
}

impl ReconReport {
    pub fn blocks_live(&self) -> bool {
        report_blocks_live(self.matched)
    }
}

/// Unresolved mismatch blocks live execution; paper only reports.
///
/// `matched` is already the kernel's verdict, so this is the whole rule —
/// which is exactly why the bridge asks instead of carrying a copy.
pub fn report_blocks_live(matched: bool) -> bool {
    !matched
}

/// Compare local ledger against broker snapshot (tolerance on quantities;
/// a broker quantity that is not a number counts as zero).
pub fn reconcile_positions(
    local: &[(String, f64)],
    broker: &[(String, String)],
    tolerance: f64,
    checked_at: &str,
) -> ReconReport {
    let mut broker_qty: HashMap<&str, f64> = HashMap::new();
    for (symbol, raw) in broker {
        broker_qty.insert(symbol.as_str(), raw.parse::<f64>().unwrap_or(0.0));
    }
    let mut local_qty: HashMap<&str, f64> = HashMap::new();
    for (symbol, qty) in local {
        local_qty.insert(symbol.as_str(), *qty);
    }
    let mut symbols: BTreeSet<&str> = BTreeSet::new();
    symbols.extend(local_qty.keys());
    symbols.extend(broker_qty.keys());
    let mut mismatches = Vec::new();
    for symbol in symbols {
        let mine = local_qty.get(symbol).copied().unwrap_or(0.0);
        let theirs = broker_qty.get(symbol).copied().unwrap_or(0.0);
        if (mine - theirs).abs() > tolerance {
            mismatches.push(Mismatch {
                kind: "position",
                id: symbol.to_string(),
                local: py_float(mine),
                broker: py_float(theirs),
            });
        }
    }
    ReconReport {
        matched: mismatches.is_empty(),
        mismatches,
        checked_at: checked_at.to_string(),
    }
}

/// Compare open-order id sets (symmetric difference, sorted).
pub fn reconcile_orders(local: &[String], broker: &[String], checked_at: &str) -> ReconReport {
    let set_local: BTreeSet<&str> = local.iter().map(|s| s.as_str()).collect();
    let set_broker: BTreeSet<&str> = broker.iter().map(|s| s.as_str()).collect();
    let mut mismatches = Vec::new();
    for cid in set_local.symmetric_difference(&set_broker) {
        let side = if set_local.contains(cid) {
            "local-only"
        } else {
            "broker-only"
        };
        mismatches.push(Mismatch {
            kind: "order",
            id: cid.to_string(),
            local: side.to_string(),
            broker: side.to_string(),
        });
    }
    ReconReport {
        matched: mismatches.is_empty(),
        mismatches,
        checked_at: checked_at.to_string(),
    }
}

/// Compare ledger equity against the broker funds snapshot. `None` broker
/// equity is a mismatch (`"unknown"`, never treated as matching).
pub fn reconcile_funds(
    local_equity: f64,
    broker_equity: Option<f64>,
    tolerance: f64,
    checked_at: &str,
) -> ReconReport {
    match broker_equity {
        None => ReconReport {
            matched: false,
            mismatches: vec![Mismatch {
                kind: "funds",
                id: "equity".to_string(),
                local: py_float(local_equity),
                broker: "unknown".to_string(),
            }],
            checked_at: checked_at.to_string(),
        },
        Some(equity) => {
            if (local_equity - equity).abs() > tolerance {
                ReconReport {
                    matched: false,
                    mismatches: vec![Mismatch {
                        kind: "funds",
                        id: "equity".to_string(),
                        local: py_float(local_equity),
                        broker: py_float(equity),
                    }],
                    checked_at: checked_at.to_string(),
                }
            } else {
                ReconReport {
                    matched: true,
                    mismatches: Vec::new(),
                    checked_at: checked_at.to_string(),
                }
            }
        }
    }
}

/// Combined verdict status. WARNING and BLOCKED both block LIVE.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Verdict {
    Safe,
    Warning,
    Blocked,
}

impl Verdict {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Safe => "SAFE",
            Self::Warning => "WARNING",
            Self::Blocked => "BLOCKED",
        }
    }

    pub fn blocks_live(&self) -> bool {
        *self != Verdict::Safe
    }
}

/// Combine reports: unevaluated → WARNING; any mismatch → BLOCKED with
/// `"{kind} {id}: local={local} broker={broker}"` reasons; else SAFE.
pub fn verdict_of(
    reports: &[&ReconReport],
    evaluated: bool,
    reasons_out: &mut Vec<String>,
) -> Verdict {
    if !evaluated {
        reasons_out.push("reconciliation not evaluated".to_string());
        return Verdict::Warning;
    }
    for report in reports {
        for m in &report.mismatches {
            reasons_out.push(format!(
                "{} {}: local={} broker={}",
                m.kind, m.id, m.local, m.broker
            ));
        }
    }
    if reasons_out.is_empty() {
        Verdict::Safe
    } else {
        Verdict::Blocked
    }
}

/// Status code for the combined verdict: 0 SAFE, 1 WARNING, 2 BLOCKED.
///
/// The counts arrive already totalled by the caller; which report contributed
/// a mismatch never changes the answer, so no ordering rule lives here.
pub fn verdict_status(evaluated: bool, mismatch_total: i64) -> u32 {
    if !evaluated {
        return 1;
    }
    if mismatch_total > 0 {
        2
    } else {
        0
    }
}

/// One report as the bridge document `"<count>\n"` then, per mismatch, four
/// NUL-terminated fields (`kind`, `id`, `local`, `broker`).
///
/// Four fields per record plus an explicit count is why no field has to
/// escape the separator: the boundaries are arithmetic, not syntactic.
pub fn report_doc(report: &ReconReport) -> String {
    let mut doc = format!("{}\n", report.mismatches.len());
    for m in &report.mismatches {
        doc.push_str(m.kind);
        doc.push('\0');
        doc.push_str(&m.id);
        doc.push('\0');
        doc.push_str(&m.local);
        doc.push('\0');
        doc.push_str(&m.broker);
        doc.push('\0');
    }
    doc
}

// ── journal.py (pure math) ────────────────────────────────────────────────

/// Percentile over ascending samples: `rank = min(n-1, max(0, int(pct/100*n)))`.
pub fn percentile(ordered: &[f64], pct: f64) -> f64 {
    if ordered.is_empty() {
        return 0.0;
    }
    let rank = (ordered.len() - 1).min(((pct / 100.0 * ordered.len() as f64) as usize).max(0));
    ordered[rank]
}

/// Consecutive-mark segments (`"a->b"`, floored at zero).
pub fn segments(marks: &[(&str, f64)]) -> Vec<(String, f64)> {
    marks
        .windows(2)
        .map(|w| {
            (
                format!("{}->{}", w[0].0, w[1].0),
                (w[1].1 - w[0].1).max(0.0),
            )
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::execution::OrderType;

    const T: &str = "2026-01-05T09:30:00+00:00";

    fn mk_order(client: &str, intent: &str) -> BrokerOrder {
        BrokerOrder::new(client, intent, "T", Side::Buy, 10.0)
    }

    fn fill(qty: f64, price: f64) -> Fill {
        Fill {
            client_order_id: "p1".to_string(),
            broker_order_id: Some("b1".to_string()),
            symbol: "T".to_string(),
            side: Side::Buy,
            fill_qty: qty,
            fill_price: price,
            commission: 1.0,
            timestamp: T.to_string(),
            partial: false,
        }
    }

    // ── parity vs live probe (exec_probe.py) ────────────────────────────

    #[test]
    fn engine_create_advance_and_guards() {
        let mut eng = ExecutionEngine::with_now(T);
        let o = eng.create(mk_order("c1", "i1")).unwrap();
        assert_eq!(o.state, order_state::CREATED);
        assert_eq!(o.history, vec![(order_state::CREATED, T.to_string())]);
        let mut o = o;
        for (name, code) in [
            ("VALIDATED", order_state::VALIDATED),
            ("SUBMITTED", order_state::SUBMITTED),
            ("ACKNOWLEDGED", order_state::ACKNOWLEDGED),
        ] {
            o = eng.transition("c1", code, "r").unwrap();
            assert_eq!(state_name(o.state), name);
        }
        assert_eq!(o.history.len(), 4);
        let _o = eng
            .transition("c1", order_state::PARTIALLY_FILLED, "partial")
            .unwrap();
        let o = eng.transition("c1", order_state::FILLED, "fill").unwrap();
        assert_eq!(o.history.len(), 6);
        assert!(eng.open_orders().is_empty());

        assert_eq!(
            eng.create(mk_order("c1", "i9")).unwrap_err(),
            "duplicate client order id: c1"
        );
        assert_eq!(
            eng.create(mk_order("c9", "i1")).unwrap_err(),
            "duplicate intent already has order: i1"
        );
        let mut bad = mk_order("c9", "i9");
        bad.state = order_state::FILLED;
        assert_eq!(
            eng.create(bad).unwrap_err(),
            "only CREATED orders may enter the engine"
        );
        assert_eq!(
            eng.transition("zz", order_state::FILLED, "").unwrap_err(),
            "unknown order: zz"
        );
        assert_eq!(
            eng.transition("c1", order_state::CANCELLED, "")
                .unwrap_err(),
            "illegal FILLED -> CANCELLED"
        );
    }

    #[test]
    fn engine_unknown_and_reconcile() {
        let mut eng = ExecutionEngine::with_now(T);
        eng.create(mk_order("u1", "ui1")).unwrap();
        let u = eng.transition("u1", order_state::UNKNOWN, "").unwrap();
        assert_eq!(u.reason, "state unknown");
        assert_eq!(
            eng.transition("u1", order_state::FILLED, "").unwrap_err(),
            "UNKNOWN exits only via reconcile()"
        );
        let r = eng.reconcile("u1", "FILLED", 5.0, "").unwrap();
        assert_eq!(r.state, order_state::FILLED);
        assert_eq!(r.filled_qty, 5.0);
        assert_eq!(r.avg_fill_price, None);
        assert_eq!(r.reason, "reconciled");
        assert_eq!(
            eng.reconcile("u1", "NOPE", 0.0, "").unwrap_err(),
            "broker reported unknown state: NOPE"
        );
        assert_eq!(
            eng.reconcile("u1", "UNKNOWN", 0.0, "").unwrap_err(),
            "reconcile cannot resolve UNKNOWN to UNKNOWN"
        );
    }

    #[test]
    fn engine_fill_fold_and_snapshot() {
        let mut eng = ExecutionEngine::new();
        eng.create(mk_order("p1", "pi1")).unwrap();
        let a = eng
            .apply_fill(&mk_order("p1", "pi1"), &fill(4.0, 100.0))
            .unwrap();
        assert_eq!((a.filled_qty, a.avg_fill_price), (4.0, Some(100.0)));
        assert_eq!(a.broker_order_id.as_deref(), Some("b1"));
        let a = eng
            .apply_fill(
                &mk_order("p1", "pi1"),
                &Fill {
                    fill_qty: 6.0,
                    fill_price: 110.0,
                    ..fill(0.0, 0.0)
                },
            )
            .unwrap();
        assert_eq!((a.filled_qty, a.avg_fill_price), (10.0, Some(106.0)));
        // Terminal orders reject fills (stored CREATED order takes another
        // fill first — mirroring the probe's exact call sequence).
        let a = eng
            .apply_fill(&mk_order("p1", "pi1"), &fill(4.0, 100.0))
            .unwrap();
        assert_eq!(a.filled_qty, 14.0);
        let mut term = mk_order("p1", "pi1");
        term.state = order_state::FILLED;
        // Stored order is CREATED (non-terminal) so the fold proceeds; a
        // genuinely terminal stored order rejects:
        eng.transition("p1", order_state::VALIDATED, "").unwrap();
        eng.transition("p1", order_state::REJECTED, "").unwrap();
        assert_eq!(
            eng.apply_fill(&term, &fill(1.0, 1.0)).unwrap_err(),
            "fill for terminal order p1"
        );
        let snap = eng.snapshot();
        assert_eq!(snap.len(), 1);
        assert_eq!(
            (snap[0].state.as_str(), snap[0].filled_qty),
            ("REJECTED", 14.0)
        );

        let mut eng2 = ExecutionEngine::new();
        eng2.restore(snap.iter().map(RestoredOrder::from).collect())
            .unwrap();
        assert_eq!(eng2.get("p1").unwrap().state, order_state::REJECTED);
        assert_eq!(eng2.by_intent("pi1").unwrap().client_order_id, "p1");
        assert_eq!(
            eng2.restore(vec![RestoredOrder {
                client_order_id: "x".to_string(),
                intent_id: "y".to_string(),
                symbol: "T".to_string(),
                side: "BUY".to_string(),
                quantity: 1.0,
                order_type: "MARKET".to_string(),
                limit_price: None,
                state: "NOPE".to_string(),
                filled_qty: 0.0,
                avg_fill_price: None,
                broker_order_id: None,
                reason: String::new(),
                history: Vec::new(),
            }])
            .unwrap_err(),
            "invalid engine snapshot order: unknown state 'NOPE'"
        );
    }

    #[test]
    fn planner_exact_shapes() {
        let intent = PlanIntent {
            intent_id: "i1".to_string(),
            symbol: "T".to_string(),
            side: Side::Buy,
            quantity: 10.0,
            preferred_order_type: OrderType::Market,
        };
        let plan = plan_order(&intent, 100.0, &ExecutionPreferences::default());
        assert_eq!(plan.order_type, OrderType::Market);
        assert_eq!(plan.limit_price, None);
        assert_eq!(plan.quantity, 10.0);
        assert_eq!(plan.time_in_force, "DAY");
        let prefs = ExecutionPreferences::new(true, 0.5).unwrap();
        let plan2 = plan_order(&intent, 100.0, &prefs);
        assert_eq!(plan2.order_type, OrderType::Limit);
        assert_eq!(plan2.limit_price, Some(100.0));
        assert_eq!(plan2.quantity, 5.0);
        assert_eq!(
            ExecutionPreferences::new(false, 0.0).unwrap_err(),
            "size_multiplier must be in (0, 1]"
        );
        assert_eq!(
            ExecutionPreferences::new(false, 1.5).unwrap_err(),
            "size_multiplier must be in (0, 1]"
        );
    }

    #[test]
    fn modes_and_arms_match() {
        assert_eq!(
            arm_transition(LiveArm::Disarmed, LiveArm::Arming).unwrap(),
            LiveArm::Arming
        );
        assert_eq!(
            arm_transition(LiveArm::Disarmed, LiveArm::Running).unwrap_err(),
            "illegal arming transition DISARMED -> RUNNING"
        );
        assert!(
            ModeGates::default().missing()
                == vec![
                    "LIVE_TRADING_ENABLED",
                    "BROKER_LIVE_ENABLED",
                    "ACCOUNT_CONFIRMED",
                    "RISK_LIMITS_VALID",
                    "KILL_SWITCH_OFF",
                ]
        );
        let env = |pairs: &[(&str, &str)]| {
            let owned: HashMap<String, String> = pairs
                .iter()
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .collect();
            gates_from_env(&|k: &str| owned.get(k).cloned().unwrap_or_default())
        };
        let g = env(&[
            ("LIVE_TRADING_ENABLED", "True"),
            ("BROKER_LIVE_ENABLED", "1"),
            ("ACCOUNT_CONFIRMED", "yes"),
            ("RISK_LIMITS_VALID", "TRUE"),
            ("KILL_SWITCH_OFF", " true "),
        ]);
        assert!(g.live_trading_enabled && !g.broker_live_enabled && !g.account_confirmed);
        assert!(g.risk_limits_valid && g.kill_switch_off);
        let full = ModeGates {
            live_trading_enabled: true,
            broker_live_enabled: true,
            account_confirmed: true,
            risk_limits_valid: true,
            kill_switch_off: true,
        };
        assert_eq!(
            resolve_mode(ExecutionMode::Live, &full),
            (ExecutionMode::Live, vec![])
        );
        let (mode, reasons) = resolve_mode(ExecutionMode::Live, &ModeGates::default());
        assert_eq!(mode, ExecutionMode::Paper);
        assert_eq!(reasons.len(), 5);
        assert_eq!(reasons[0], "live gate off: LIVE_TRADING_ENABLED");
        assert_eq!(
            resolve_mode(ExecutionMode::Sandbox, &ModeGates::default()).0,
            ExecutionMode::Sandbox
        );
    }

    #[test]
    fn gate_masks_agree_with_the_struct_rules() {
        assert_eq!(
            flags_to_mask(&["true", "TRUE ", "1", "yes", " true "]),
            Some(0b0_0011 | 0b1_0000)
        );
        assert_eq!(flags_to_mask(&["true"]), None);
        assert_eq!(
            missing_gates(0b0_0011),
            vec!["ACCOUNT_CONFIRMED", "RISK_LIMITS_VALID", "KILL_SWITCH_OFF"]
        );
        assert!(missing_gates(0b1_1111).is_empty());
        let (mode, reasons) = resolve_mode_mask("LIVE", 0b0_0011).unwrap();
        assert_eq!(mode, ExecutionMode::Paper);
        assert_eq!(
            reasons,
            vec![
                "live gate off: ACCOUNT_CONFIRMED",
                "live gate off: RISK_LIMITS_VALID",
                "live gate off: KILL_SWITCH_OFF"
            ]
        );
        assert_eq!(
            resolve_mode_mask("LIVE", 0b1_1111).unwrap(),
            (ExecutionMode::Live, vec![])
        );
        assert_eq!(
            resolve_mode_mask("PAPER", 0).unwrap().0,
            ExecutionMode::Paper
        );
        assert_eq!(
            resolve_mode_mask("SANDBOX", 0).unwrap().0,
            ExecutionMode::Sandbox
        );
        assert!(resolve_mode_mask("DESKTOP", 0).is_none());
    }

    #[test]
    fn lifecycle_matches_python() {
        let mut lc = StrategyLifecycle::new();
        assert!(!lc.live());
        for target in [
            LifecycleState::Validating,
            LifecycleState::WarmingUp,
            LifecycleState::Ready,
            LifecycleState::Running,
        ] {
            lc.transition(target, "").unwrap();
        }
        assert!(lc.live());
        assert_eq!(
            lc.transition(LifecycleState::Ready, "").unwrap_err(),
            "illegal transition RUNNING -> READY"
        );
    }

    #[test]
    fn ledger_fold_matches_probe() {
        let mut led = Ledger::new(100_000.0).unwrap();
        led.apply_fill("R", Side::Buy, 10.0, 100.0, 1.0);
        let q = led.apply_fill("R", Side::Buy, 10.0, 120.0, 1.0);
        assert_eq!((q.quantity, q.avg_price), (20.0, 110.0));
        // Partial reduce resets avg to the fill price (verbatim ledger.py).
        let q = led.apply_fill("R", Side::Sell, 5.0, 130.0, 1.0);
        assert_eq!(
            (q.quantity, q.avg_price, q.realized_pnl),
            (15.0, 130.0, 99.0)
        );
        let mut marks = HashMap::new();
        marks.insert("R".to_string(), 140.0);
        let snap = led.snapshot(&marks);
        assert_eq!(
            (snap.equity, snap.available_capital, snap.day_pnl),
            (100249.0, 100249.0, 249.0)
        );
        assert_eq!(
            led.strategy_state_for("R"),
            (15.0, Some("LONG"), Some(130.0))
        );
        assert_eq!(led.strategy_state_for("ZZZ"), (0.0, None, None));
        assert_eq!(
            Ledger::new(0.0).unwrap_err(),
            "starting capital must be positive"
        );
    }

    #[test]
    fn reconcile_strings_match() {
        let local = vec![("R".to_string(), 15.0)];
        let r = reconcile_positions(&local, &[("R".to_string(), "15.0".to_string())], 1e-9, "");
        assert!(r.matched && !r.blocks_live());
        let r2 = reconcile_positions(&local, &[("R".to_string(), "bad".to_string())], 1e-9, "");
        assert_eq!(
            r2.mismatches,
            vec![Mismatch {
                kind: "position",
                id: "R".to_string(),
                local: "15.0".to_string(),
                broker: "0.0".to_string()
            }]
        );
        let r3 = reconcile_funds(100.0, None, 1e-9, "");
        assert_eq!(
            r3.mismatches,
            vec![Mismatch {
                kind: "funds",
                id: "equity".to_string(),
                local: "100.0".to_string(),
                broker: "unknown".to_string()
            }]
        );
        let ro = reconcile_orders(&["a".to_string()], &["b".to_string()], "");
        assert!(ro.blocks_live());
        let mut reasons = Vec::new();
        assert_eq!(verdict_of(&[&r2], true, &mut reasons), Verdict::Blocked);
        assert_eq!(
            reasons,
            vec!["position R: local=15.0 broker=0.0".to_string()]
        );
        let mut reasons2 = Vec::new();
        assert_eq!(verdict_of(&[], false, &mut reasons2), Verdict::Warning);
        assert_eq!(reasons2, vec!["reconciliation not evaluated".to_string()]);
    }

    #[test]
    fn report_doc_and_status_frame_the_bridge() {
        let report = reconcile_positions(
            &[("A".to_string(), 1.0), ("B".to_string(), 2.0)],
            &[("A".to_string(), "9".to_string())],
            1e-9,
            "",
        );
        let doc = report_doc(&report);
        let (head, body) = doc.split_once('\n').unwrap();
        assert_eq!(head, "2");
        let fields: Vec<&str> = body.split('\0').collect();
        assert_eq!(
            fields,
            vec!["position", "A", "1.0", "9.0", "position", "B", "2.0", "0.0", "",]
        );
        let matched = ReconReport {
            matched: true,
            mismatches: Vec::new(),
            checked_at: String::new(),
        };
        assert_eq!(report_doc(&matched), "0\n");
        assert_eq!(verdict_status(true, 0), 0);
        assert_eq!(verdict_status(true, 1), 2);
        assert_eq!(verdict_status(false, 0), 1);
        assert_eq!(verdict_status(false, 5), 1);
    }

    #[test]
    fn latency_math_matches() {
        assert_eq!(percentile(&[1.0, 2.0, 3.0, 4.0], 50.0), 3.0);
        assert_eq!(percentile(&[1.0, 2.0, 3.0, 4.0], 95.0), 4.0);
        assert_eq!(percentile(&[], 50.0), 0.0);
        assert_eq!(
            segments(&[("a", 1.0), ("b", 2.5)]),
            vec![("a->b".to_string(), 1.5)]
        );
    }

    #[test]
    fn readiness_reasons_match_source() {
        let contract = ContractView {
            missing: vec![],
            supports_live: true,
            data_requirements: vec!["candle-close".to_string()],
            warmup_bars: 20,
            required_broker_capabilities: vec![],
        };
        let ok_caps = vec!["candle-close".to_string()];
        let (ready, reasons) = check_live_readiness(
            &contract,
            &ok_caps,
            &[],
            20,
            true,
            true,
            true,
            true,
            true,
            true,
            true,
            true,
        );
        assert!(ready && reasons.is_empty());
        let (ready2, reasons2) = check_live_readiness(
            &contract,
            &[],
            &[],
            5,
            false,
            true,
            true,
            true,
            true,
            false,
            true,
            true,
        );
        assert!(!ready2);
        assert!(reasons2.contains(&"data capability missing: candle-close".to_string()));
        assert!(reasons2.contains(&"warmup shortfall: have 5, need 20".to_string()));
        assert!(reasons2.contains(&"risk policy invalid".to_string()));
        assert!(reasons2.contains(&"kill switch engaged or unavailable".to_string()));
    }

    // ── broker/gates.py::risk_configuration_valid ───────────────────────

    const ALL_OPTIONAL: u32 = PRESENT_MAX_NOTIONAL
        | PRESENT_MAX_EXPOSURE_PCT
        | PRESENT_DAILY_LOSS_LIMIT
        | PRESENT_STRATEGY_LOSS_LIMIT
        | PRESENT_MAX_ORDERS_PER_DAY
        | PRESENT_REQUIRE_FRESH_DATA_SECONDS;

    /// Kernel call with the five optional `f64` slots filled explicitly.
    fn mask(present: u32, slots: [f64; 5], max_orders_per_day: i64) -> u32 {
        risk_configuration_mask(
            present,
            1000.0,
            500.0,
            slots[0],
            slots[1],
            slots[2],
            slots[3],
            0.0,
            max_orders_per_day,
            slots[4],
        )
    }

    fn one_bit(bit: u32) -> u32 {
        1 << bit
    }

    #[test]
    fn a_sane_policy_sets_no_bits() {
        assert_eq!(mask(0, [0.0; 5], 0), 0);
        assert_eq!(
            mask(ALL_OPTIONAL, [100_000.0, 25.0, 5_000.0, 2_500.0, 60.0], 2),
            0
        );
    }

    #[test]
    fn optional_limits_are_checked_only_when_present() {
        for slot in 0..4 {
            let mut slots = [0.0; 5];
            slots[slot] = -5.0;
            // Presence bits 0..3 map onto result bits 3..6.
            assert_eq!(
                mask(1 << slot, slots, 0),
                one_bit(3 + slot as u32),
                "slot {slot}"
            );
            // Same garbage in a disabled slot is `None` in Python: no verdict.
            assert_eq!(mask(0, slots, 0), 0);
        }
        assert_eq!(
            mask(
                PRESENT_REQUIRE_FRESH_DATA_SECONDS,
                [1.0, 1.0, 1.0, 1.0, 0.0],
                0
            ),
            one_bit(9)
        );
        assert_eq!(mask(0, [1.0, 1.0, 1.0, 1.0, 0.0], 0), 0);
    }

    #[test]
    fn quantity_cooldown_and_daily_order_rules() {
        assert_eq!(
            risk_configuration_mask(0, 0.0, 500.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0),
            one_bit(0) | one_bit(2),
            "a zero position ceiling is exceeded by any order ceiling"
        );
        assert_eq!(
            risk_configuration_mask(0, 1000.0, -1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0),
            one_bit(1)
        );
        assert_eq!(
            risk_configuration_mask(0, 10.0, 9999.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0),
            one_bit(2)
        );
        // An equal order/position ceiling is legal (`>`, not `>=`).
        assert_eq!(
            risk_configuration_mask(0, 500.0, 500.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0),
            0
        );
        assert_eq!(
            risk_configuration_mask(0, 1000.0, 500.0, 0.0, 0.0, 0.0, 0.0, -0.5, 0, 0.0),
            one_bit(7)
        );
        assert_eq!(mask(PRESENT_MAX_ORDERS_PER_DAY, [0.0; 5], 0), one_bit(8));
        assert_eq!(mask(PRESENT_MAX_ORDERS_PER_DAY, [0.0; 5], -3), one_bit(8));
        assert_eq!(mask(PRESENT_MAX_ORDERS_PER_DAY, [0.0; 5], 3), 0);
    }

    #[test]
    fn every_failure_is_reachable_and_ordered() {
        let all = mask(ALL_OPTIONAL, [-1.0, -1.0, -1.0, -1.0, -1.0], 0);
        let worst = risk_configuration_mask(
            ALL_OPTIONAL,
            -10.0,
            -5.0,
            -1.0,
            -1.0,
            -1.0,
            -1.0,
            -1.0,
            0,
            -1.0,
        );
        assert_eq!(
            all,
            one_bit(3) | one_bit(4) | one_bit(5) | one_bit(6) | one_bit(8) | one_bit(9)
        );
        assert_eq!(worst, RISK_CONFIG_ALL_BITS);
        assert_eq!(RISK_CONFIG_ALL_BITS, (1 << 10) - 1);
    }

    #[test]
    fn nan_limits_fail_no_check_like_python() {
        // Python's `nan <= 0` and `nan > x` are both False; same here.
        assert_eq!(
            risk_configuration_mask(
                ALL_OPTIONAL,
                f64::NAN,
                f64::NAN,
                f64::NAN,
                f64::NAN,
                f64::NAN,
                f64::NAN,
                f64::NAN,
                1,
                f64::NAN,
            ),
            0
        );
    }
}
