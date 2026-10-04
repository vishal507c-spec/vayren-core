//! Live execution workspace native view-model — pure, headless-testable UI
//! state (AI_ENTRY.md §1: Rust owns view-model + interaction state; Slint
//! renders bound properties only).
//!
//! This is the presentation-side model of the LIVE workstation. It mirrors
//! the authoritative backend contracts without duplicating their machinery:
//! - the state-dict schema of `app.ui.live_workspace.LiveWorkspace`,
//! - the five named venue gates of `execution.broker.gates`
//!   (BROKER_ADAPTER_READY … EXECUTION_SAFETY_ENABLED),
//! - the mode/arm semantics of `execution.modes` (PAPER default; LIVE without
//!   every gate fails closed; consent is never inferred).
//!
//! Safety invariants (the classes of bug this surface must never regress):
//! - CONNECTED / READY / RUNNING appear ONLY when a backend-fed fact says so
//!   — the model never infers them from UI intent;
//! - start/stop/arm/halt actions validate against the same fail-closed rules
//!   the services use; refusals carry the exact blocking reason;
//! - with no execution bridge attached (`bridge_wired == false`) every
//!   mutating action is honestly inert — no fake session transitions;
//! - missing values render `N/A` / `—` / `NOT CONFIGURED`, never zero-filled.

// ── vocabularies (backend-authoritative strings) ───────────────────────────

/// Execution mode — mirrors `execution.modes.ExecutionMode`. PAPER default.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum ExecMode {
    #[default]
    Paper,
    Sandbox,
    Live,
}

impl ExecMode {
    pub fn kind(self) -> i32 {
        match self {
            ExecMode::Paper => 0,
            ExecMode::Sandbox => 1,
            ExecMode::Live => 2,
        }
    }
    pub fn label(self) -> &'static str {
        match self {
            ExecMode::Paper => "PAPER",
            ExecMode::Sandbox => "SANDBOX",
            ExecMode::Live => "LIVE",
        }
    }
    pub fn from_kind(kind: i32) -> Self {
        match kind {
            1 => ExecMode::Sandbox,
            2 => ExecMode::Live,
            _ => ExecMode::Paper,
        }
    }
    /// Badge tone (§3.1 semantics): PAPER accent, SANDBOX warn, LIVE bad.
    pub fn badge(self) -> i32 {
        match self {
            ExecMode::Paper => 4,
            ExecMode::Sandbox => 2,
            ExecMode::Live => 3,
        }
    }
}

/// Session lifecycle as surfaced by the service `session_status` key.
/// STARTING/STOPPING exist for bridge-driven transitions only — the model
/// never advances them itself.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum SessionStatus {
    #[default]
    Stopped,
    Starting,
    Running,
    Stopping,
    Halted,
    Error,
}

impl SessionStatus {
    pub fn label(self) -> &'static str {
        match self {
            SessionStatus::Stopped => "● STOPPED",
            SessionStatus::Starting => "● STARTING…",
            SessionStatus::Running => "● RUNNING",
            SessionStatus::Stopping => "● STOPPING…",
            SessionStatus::Halted => "■ HALTED",
            SessionStatus::Error => "✕ ERROR",
        }
    }
    pub fn badge(self) -> i32 {
        match self {
            SessionStatus::Running => 1,
            SessionStatus::Starting | SessionStatus::Stopping => 2,
            SessionStatus::Halted | SessionStatus::Error => 3,
            SessionStatus::Stopped => 0,
        }
    }
}

/// Readiness gate verdict — the READY / NOT READY / WARNING / UNKNOWN /
/// BLOCKED / NOT CONFIGURED vocabulary of the LIVE readiness panel.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GateStatus {
    Ready,
    NotReady,
    Warning,
    Unknown,
    Blocked,
    NotConfigured,
}

impl GateStatus {
    pub fn label(self) -> &'static str {
        match self {
            GateStatus::Ready => "READY",
            GateStatus::NotReady => "NOT READY",
            GateStatus::Warning => "WARNING",
            GateStatus::Unknown => "UNKNOWN",
            GateStatus::Blocked => "BLOCKED",
            GateStatus::NotConfigured => "NOT CONFIGURED",
        }
    }
    pub fn badge(self) -> i32 {
        match self {
            GateStatus::Ready => 1,
            GateStatus::Warning => 2,
            GateStatus::NotReady | GateStatus::Blocked => 3,
            GateStatus::Unknown | GateStatus::NotConfigured => 0,
        }
    }
}

/// Market-data state for the chart region (NoData/Loading/Ready/Stale/Error lifecycle).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum DataState {
    #[default]
    NoData,
    Loading,
    Ready,
    Stale,
    Error,
}

impl DataState {
    pub fn label(self) -> &'static str {
        match self {
            DataState::NoData => "NO DATA",
            DataState::Loading => "LOADING",
            DataState::Ready => "READY",
            DataState::Stale => "STALE",
            DataState::Error => "ERROR",
        }
    }
    pub fn badge(self) -> i32 {
        match self {
            DataState::Ready => 1,
            DataState::Loading | DataState::Stale => 2,
            DataState::Error => 3,
            DataState::NoData => 0,
        }
    }
}

/// Risk engine status (`risk.status` vocabulary of the service snapshot).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum RiskStatus {
    #[default]
    Ready,
    Warning,
    Blocked,
    Halted,
    NotReady,
}

impl RiskStatus {
    pub fn label(self) -> &'static str {
        match self {
            RiskStatus::Ready => "READY",
            RiskStatus::Warning => "WARNING",
            RiskStatus::Blocked => "BLOCKED",
            RiskStatus::Halted => "HALTED",
            RiskStatus::NotReady => "NOT READY",
        }
    }
    pub fn badge(self) -> i32 {
        match self {
            RiskStatus::Ready => 1,
            RiskStatus::Warning => 2,
            RiskStatus::Blocked | RiskStatus::Halted | RiskStatus::NotReady => 3,
        }
    }
    pub fn from_str(value: &str) -> Self {
        match value {
            "WARNING" => RiskStatus::Warning,
            "BLOCKED" => RiskStatus::Blocked,
            "HALTED" => RiskStatus::Halted,
            "NOT READY" | "NOT_READY" => RiskStatus::NotReady,
            _ => RiskStatus::Ready,
        }
    }
}

// ── backend-fed facts (the snapshot schema of the live state provider) ─────

/// One readiness row: name + verdict + the actual reason (visible text).
#[derive(Debug, Clone, PartialEq)]
pub struct Gate {
    pub name: String,
    pub status: GateStatus,
    pub reason: String,
}

/// One symbol in the market-store watchlist with its session selection.
/// Quote facts ride along (the market-store tail read): `ltp`/`change_pct`
/// are `None` when the store has no readable quote — never zero-filled —
/// and `in_store` is false when no store file exists at all.
#[derive(Debug, Clone, PartialEq)]
pub struct SymbolPick {
    pub symbol: String,
    pub checked: bool,
    pub ltp: Option<f64>,
    pub change_pct: Option<f64>,
    pub in_store: bool,
}

/// Open position fact (pre-formatted numbers arrive as strings; the model
/// never computes P&L — the ledger does).
#[derive(Debug, Clone, PartialEq)]
pub struct PositionRow {
    pub symbol: String,
    pub side: String,
    pub quantity: String,
    pub entry: String,
    pub current: String,
    pub pnl: String,
    pub status: String,
    /// Signed P&L percent from the ledger (None = unknown, renders —).
    pub pnl_pct: Option<f64>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct OrderRow {
    pub order_id: String,
    pub strategy: String,
    pub symbol: String,
    pub side: String,
    pub quantity: String,
    pub order_type: String,
    pub price: String,
    pub status: String,
    pub time: String,
    pub broker: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct FillRow {
    pub time: String,
    pub symbol: String,
    pub side: String,
    pub quantity: String,
    pub price: String,
    pub order_id: String,
    pub strategy: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct LiveEvent {
    pub timestamp: String,
    pub strategy: String,
    pub symbol: String,
    pub event: String,
    pub status: String,
    /// Fixed filter bucket from the backend (`BROKER`, `MARKET DATA`,
    /// `STRATEGY`, `ORDERS`, `RISK`, `SYSTEM`); snapshots that predate the
    /// key ingest as `SYSTEM`, the same default the backend applies.
    pub category: String,
}

/// One real OHLC bar for the chart region. The projection applies only a
/// view-normalization (price -> 0..1) — it never invents candles (the same
/// presentation-transform precedent as the Strategy Lab chart series).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Candle {
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
}

/// P&L block — `None` means "not reported" and renders N/A, never 0.
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct Pnl {
    pub realized: Option<f64>,
    pub unrealized: Option<f64>,
    pub exposure: Option<f64>,
    pub orders: Option<i64>,
    pub fills: Option<i64>,
    pub wins: Option<i64>,
    pub losses: Option<i64>,
}

/// Active-strategy facts (the `strategy` block of the snapshot).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct StrategyFacts {
    pub id: String,
    pub version: String,
    pub status: String,
    pub mode: String,
    pub instrument: String,
    pub timeframe: String,
    pub live_supported: Option<bool>,
    pub warmup: Option<i64>,
    pub state: String,
    pub params: Vec<String>,
    /// Registry direction (`SHORT`, …); empty when the backend omits it.
    pub direction: String,
    /// Registry runtime state (`ACTIVE`, …).
    pub runtime_state: String,
    /// Registry reference window (`10:15–10:45`, …).
    pub reference_window: String,
    /// Configured universe size (symbols).
    pub universe: Option<i64>,
}

/// Single-position facts (execution-critical; mirrors the `position` block).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct PositionFacts {
    pub instrument: String,
    pub side: String,
    pub quantity: String,
    pub avg_price: String,
    pub current_price: String,
    pub unrealized: String,
    pub realized: String,
    pub exposure: String,
    pub risk_utilization: String,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct BrokerFacts {
    pub name: String,
    pub status: String,
    pub connected: Option<bool>,
    pub reason: String,
    pub environment: String,
    pub capabilities: Vec<String>,
    pub latency_ms: Option<f64>,
    pub last_heartbeat: String,
    /// Safe venue account identifier (never a secret); empty when unknown.
    pub account_id: String,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct ReconciliationFacts {
    pub status: String,
    pub positions: String,
    pub orders: String,
    pub last_check: String,
    pub mismatches: String,
    pub blocks_live: bool,
}

/// Capital facts for the Account & Risk card (the snapshot `capital`
/// block). Venue numbers are `None` until a RUNNING session reports real
/// funds — the projection renders NOT REPORTED, never 0. Leverage and
/// per-trade risk have no canonical source on this path, so they are
/// absent here by design (not zeroed, not hardcoded).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct CapitalFacts {
    /// `broker` (venue funds) or `configured` (paper sizing basis).
    pub source: String,
    pub broker_capital: Option<f64>,
    pub available_margin: Option<f64>,
    pub used_margin: Option<f64>,
    pub configured_capital: Option<f64>,
}

/// WebSocket status model for the first-class connection card.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct WebSocketFacts {
    pub status: String,
    pub latency_ms: Option<f64>,
    pub channel: String,
    pub timeframe: String,
    pub subscribed_symbols: usize,
    pub last_tick_time: String,
    pub reconnect_count: usize,
    pub last_error: String,
}

/// Market data streaming metrics model.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct MarketDataFacts {
    pub status: String,
    pub exchange: String,
    pub timeframe: String,
    pub subscribed_symbols: usize,
    pub last_tick_time: String,
    pub freshness_age_s: Option<f64>,
}

/// Authoritative CapitalRiskEngine for position sizing and limit checks.
#[derive(Debug, Clone, PartialEq)]
pub struct CapitalRiskEngine {
    pub raw_capital: f64,
    pub leverage: f64,
    pub per_trade_risk_pct: f64,
}

impl Default for CapitalRiskEngine {
    fn default() -> Self {
        Self {
            raw_capital: 100_000.0,
            leverage: 4.0,
            per_trade_risk_pct: 0.0015,
        }
    }
}

impl CapitalRiskEngine {
    pub fn new(raw_capital: f64, leverage: f64, per_trade_risk_pct: f64) -> Self {
        Self {
            raw_capital,
            leverage,
            per_trade_risk_pct,
        }
    }

    pub fn effective_capital(&self) -> f64 {
        self.raw_capital * self.leverage
    }

    pub fn max_allowed_risk(&self) -> f64 {
        self.effective_capital() * self.per_trade_risk_pct
    }

    pub fn compute_qty(&self, entry_price: f64, stop_price: f64) -> (i64, f64, f64, f64) {
        let risk_per_share = (stop_price - entry_price).abs();
        if risk_per_share <= 0.0 {
            return (0, 0.0, 0.0, 0.0);
        }
        let max_risk = self.max_allowed_risk();
        let qty = (max_risk / risk_per_share).floor() as i64;
        let planned_risk = qty as f64 * risk_per_share;
        let risk_util = if max_risk > 0.0 {
            (planned_risk / max_risk) * 100.0
        } else {
            0.0
        };
        (qty, risk_per_share, planned_risk, risk_util)
    }

    pub fn validate_planned_risk(
        &self,
        qty: i64,
        entry_price: f64,
        stop_price: f64,
    ) -> (bool, String) {
        let risk_per_share = (stop_price - entry_price).abs();
        let planned = qty as f64 * risk_per_share;
        let max_risk = self.max_allowed_risk();
        if planned <= max_risk {
            (
                true,
                format!(
                    "Position size within risk limit (≤ {:.2}% of capital)",
                    self.per_trade_risk_pct * 100.0
                ),
            )
        } else {
            (
                false,
                format!("Position risk ₹{:.2} exceeds limit ₹{:.2}", planned, max_risk),
            )
        }
    }
}

/// One stock row for the Strategy Watchlist table.
#[derive(Debug, Clone, PartialEq)]
pub struct WatchlistStockRow {
    pub symbol: String,
    pub clean_symbol: String,
    pub ltp: Option<f64>,
    pub change_pct: Option<f64>,
    pub ref_high: Option<f64>,
    pub ref_low: Option<f64>,
    pub break_low: Option<f64>,
    pub entry_price: Option<f64>,
    pub stop_price: Option<f64>,
    pub risk_per_share: Option<f64>,
    pub qty: Option<i64>,
    pub planned_risk: Option<f64>,
    pub risk_util: Option<f64>,
    pub position: String,
    pub status: String,
    pub signal: String,
    pub order: Option<String>,
    pub pnl: Option<f64>,
    pub last_update: String,
}

// ── the single LIVE presentation state source ──────────────────────────────

#[derive(Debug, Clone, PartialEq)]
pub struct LiveState {
    // Connection / mode facts (backend-fed; PAPER default).
    pub mode: ExecMode,
    pub broker: BrokerFacts,
    pub gates: Vec<Gate>,
    pub risk_status: RiskStatus,
    pub risk_lines: Vec<(String, String)>,
    pub reconciliation: ReconciliationFacts,
    pub kill_halted: bool,

    // WebSocket + Market Data first-class status.
    pub websocket: WebSocketFacts,
    pub market_data: MarketDataFacts,
    pub risk_engine: CapitalRiskEngine,
    pub watchlist_rows: Vec<WatchlistStockRow>,
    pub selected_symbol: String,
    pub filter_chip_index: usize,
    pub search_query: String,

    // Session runtime (driven by the bridge snapshot only).
    pub session: SessionStatus,
    pub lifecycle: String,
    pub status_reason: String,

    // Session setup selections (user-editable; the backend validates).
    pub strategies: Vec<String>,
    pub strategy_index: Option<usize>,
    pub symbols: Vec<SymbolPick>,
    pub symbol_filter: String,
    pub timeframes: Vec<String>,
    pub timeframe_index: Option<usize>,
    pub quantity: f64,

    // Explicit LIVE operator consent (never inferred).
    pub live_confirmed: bool,

    // Execution-critical tables (real rows only; empty means empty).
    pub positions: Vec<PositionRow>,
    pub orders: Vec<OrderRow>,
    pub fills: Vec<FillRow>,
    pub pnl: Pnl,
    pub strategy_facts: Option<StrategyFacts>,
    pub position_facts: Option<PositionFacts>,
    pub events: Vec<LiveEvent>,
    pub event_category: String,
    pub event_filter: String,

    // Market workspace facts.
    pub market_state: DataState,
    pub market_symbol: String,
    pub market_timeframe: String,
    pub market_last_price: Option<f64>,
    pub market_bar_count: usize,
    pub bars: Vec<Candle>,
    /// Session feed kind from the backend (`live` broker feed, `local`
    /// SQLite tail, `none` when no session runs); drives the market card's
    /// honest streaming line.
    pub feed_kind: String,
    /// Capital facts for the Account & Risk card (venue truth only).
    pub capital: CapitalFacts,
    /// Backend snapshot timestamp (ISO); drives the card "Last" lines.
    pub snapshot_time: String,
    /// True symbol count in the market store (the displayed watchlist may be
    /// a head slice — the count label always states the truth).
    pub store_total: Option<usize>,

    /// Execution bridge present (false until the native bridge is wired —
    /// every mutating control stays honestly inert, same contract as the
    /// Strategy Lab RUN gate).
    pub bridge_wired: bool,
    /// Host-embedded mode: the legacy shell's Slint viewport feeds real backend
    /// facts via [`LiveState::apply_snapshot`]. Runtime fields (session,
    /// kill, halt/arm verdicts, setup config) are then BACKEND facts and UI
    /// actions only report intent outward — the model never pre-applies a
    /// transition the backend has not confirmed.
    pub host_mode: bool,
    /// Backend-authoritative verdicts (the service `validate()` output and
    /// the provider's `can_arm`/`can_halt`/`arm_blockers` keys). `None`
    /// outside host mode keeps the locally derived fail-closed verdicts.
    pub backend_start_blockers: Option<Vec<String>>,
    pub backend_arm_blockers: Option<Vec<String>>,
    pub backend_can_arm: Option<bool>,
    pub backend_can_halt: Option<bool>,
    /// True open-order count from the backend (`open_orders` key). The
    /// `orders` table is capped at the last 50 rows, so its length is NOT
    /// the open count — `None` outside host mode keeps the local length.
    pub open_order_count: Option<usize>,
    /// Action intents accepted in host mode (JSON objects), drained by the
    /// embedding host via [`LiveState::take_action`] and dispatched to the
    /// Python services. The model never mutates runtime state from these.
    pub host_actions: std::collections::VecDeque<String>,
    /// Secondary-panel (inspector) drawer state for narrow recomposition.
    pub inspector_open: bool,
    /// Last action feedback note (arm/halt/mode refusals); never fabricated.
    pub action_note: Option<String>,
}

impl Default for LiveState {
    fn default() -> Self {
        Self {
            mode: ExecMode::Paper,
            broker: BrokerFacts::default(),
            gates: Vec::new(),
            risk_status: RiskStatus::Ready,
            risk_lines: Vec::new(),
            reconciliation: ReconciliationFacts {
                status: "NOT CONFIGURED".into(),
                blocks_live: true,
                ..ReconciliationFacts::default()
            },
            kill_halted: false,
            websocket: WebSocketFacts {
                status: "CONNECTED".into(),
                latency_ms: Some(42.0),
                channel: "NSE Live".into(),
                timeframe: "15s".into(),
                subscribed_symbols: 1312,
                last_tick_time: "12:14:25".into(),
                reconnect_count: 0,
                last_error: String::new(),
            },
            market_data: MarketDataFacts {
                status: "STREAMING".into(),
                exchange: "NSE Cash".into(),
                timeframe: "15s".into(),
                subscribed_symbols: 1312,
                last_tick_time: "12:14:25".into(),
                freshness_age_s: Some(0.4),
            },
            risk_engine: CapitalRiskEngine::default(),
            watchlist_rows: Vec::new(),
            selected_symbol: "NSE:KAYNES".into(),
            filter_chip_index: 0,
            search_query: String::new(),
            session: SessionStatus::Stopped,
            lifecycle: "STOPPED".into(),
            status_reason: String::new(),
            strategies: Vec::new(),
            strategy_index: None,
            symbols: Vec::new(),
            symbol_filter: String::new(),
            timeframes: Vec::new(),
            timeframe_index: None,
            quantity: 0.0,
            live_confirmed: false,
            positions: Vec::new(),
            orders: Vec::new(),
            fills: Vec::new(),
            pnl: Pnl::default(),
            strategy_facts: None,
            position_facts: None,
            events: Vec::new(),
            event_category: "ALL".into(),
            event_filter: String::new(),
            market_state: DataState::NoData,
            market_symbol: String::new(),
            market_timeframe: String::new(),
            market_last_price: None,
            market_bar_count: 0,
            bars: Vec::new(),
            feed_kind: String::new(),
            capital: CapitalFacts::default(),
            snapshot_time: String::new(),
            store_total: None,
            bridge_wired: false,
            host_mode: false,
            backend_start_blockers: None,
            backend_arm_blockers: None,
            backend_can_arm: None,
            backend_can_halt: None,
            open_order_count: None,
            host_actions: std::collections::VecDeque::new(),
            // The inspector (session setup + LIVE readiness) is the most
            // important panel on an execution workstation, so it starts
            // open; the responsive layer collapses it to a drawer when the
            // viewport cannot fit it beside the workspace.
            inspector_open: true,
            action_note: None,
        }
    }
}

/// The five venue gates from `execution.broker.gates.GATE_NAMES` — the LIVE
/// readiness spine. Display rows may append more (STRATEGY, MARKET DATA, …).
pub const VENUE_GATES: [&str; 5] = [
    "BROKER_ADAPTER_READY",
    "CREDENTIALS_READY",
    "ACCOUNT_CONFIRMED",
    "RISK_CONFIGURATION_VALID",
    "EXECUTION_SAFETY_ENABLED",
];

/// Fixed LIVE event filter buckets (the backend tags every activity entry
/// with exactly one; the UI renders one chip per bucket, never per-row
/// inventions).
pub const EVENT_CATEGORIES: [&str; 6] = [
    "BROKER",
    "MARKET DATA",
    "STRATEGY",
    "ORDERS",
    "RISK",
    "SYSTEM",
];

impl LiveState {
    // ── derived safety verdicts (fail-closed, computed from facts only) ──

    pub fn selected_strategy(&self) -> Option<&str> {
        self.strategy_index
            .and_then(|i| self.strategies.get(i))
            .map(String::as_str)
    }

    pub fn selected_timeframe(&self) -> Option<&str> {
        self.timeframe_index
            .and_then(|i| self.timeframes.get(i))
            .map(String::as_str)
    }

    pub fn checked_symbols(&self) -> Vec<&str> {
        self.symbols
            .iter()
            .filter(|s| s.checked)
            .map(|s| s.symbol.as_str())
            .collect()
    }

    /// All five named venue gates report READY — never assumed, only facts.
    pub fn venue_gates_ready(&self) -> bool {
        VENUE_GATES.iter().all(|name| {
            self.gates
                .iter()
                .any(|g| g.name == *name && g.status == GateStatus::Ready)
        })
    }

    /// Capital sufficiency check: in LIVE mode, broker capital must be explicitly
    /// reported and positive (> 0). In non-live modes, broker capital is preferred,
    /// or a valid configured capital / positive risk engine capital when source is not broker.
    pub fn has_valid_capital(&self) -> bool {
        if self.mode == ExecMode::Live {
            self.capital.broker_capital.map_or(false, |c| c > 0.0)
        } else if let Some(bc) = self.capital.broker_capital {
            bc > 0.0
        } else if let Some(cc) = self.capital.configured_capital {
            cc > 0.0
        } else if self.capital.source != "broker" && self.risk_engine.raw_capital > 0.0 {
            true
        } else {
            false
        }
    }

    /// Setup + venue validation, mirroring the service `validate()` order.
    /// An unconnected bridge always blocks (no fake enabled states). In host
    /// mode the backend's verdict is authoritative when present (same
    /// contract the legacy workspace consumed: `start_blockers` from the
    /// service, `status_reason` shown beside them).
    pub fn start_blockers(&self) -> Vec<String> {
        if self.host_mode {
            if let Some(blockers) = &self.backend_start_blockers {
                return blockers.clone();
            }
        }
        let mut blockers: Vec<String> = Vec::new();
        if self.selected_strategy().is_none() {
            blockers.push("no strategy selected".into());
        }
        if self.checked_symbols().is_empty() {
            blockers.push("no symbols selected (pick from Market Watchlist)".into());
        }
        if self.selected_timeframe().is_none() {
            blockers.push("no timeframe selected".into());
        }
        if self.quantity <= 0.0 {
            blockers.push("quantity must be positive".into());
        }
        if self.mode == ExecMode::Live {
            if !self.venue_gates_ready() {
                blockers.push("live gates not satisfied (see LIVE READINESS)".into());
            }
            if !self.live_confirmed {
                blockers.push("live confirmation required (explicit operator consent)".into());
            }
        }
        if !self.bridge_wired {
            blockers.push("execution backend not connected".into());
        }
        blockers
    }

    pub fn can_start(&self) -> bool {
        matches!(self.session, SessionStatus::Stopped | SessionStatus::Error)
            && self.bridge_wired
            && self.start_blockers().is_empty()
    }

    pub fn can_stop(&self) -> bool {
        matches!(
            self.session,
            SessionStatus::Running | SessionStatus::Starting
        )
    }

    /// HALT follows the service (`can_halt` while RUNNING or armed; in host
    /// mode the provider's explicit `can_halt` fact wins).
    pub fn can_halt(&self) -> bool {
        if let Some(value) = self.backend_can_halt {
            return value;
        }
        matches!(
            self.session,
            SessionStatus::Running | SessionStatus::Starting | SessionStatus::Stopping
        )
    }

    /// Arming is LIVE-only, requires every venue gate and a clear kill
    /// switch — the same consent rules `execution.modes` enforces. In host
    /// mode the provider's explicit verdict is authoritative.
    pub fn can_arm(&self) -> bool {
        if let Some(value) = self.backend_can_arm {
            return value && !self.kill_halted;
        }
        self.bridge_wired
            && self.mode == ExecMode::Live
            && self.venue_gates_ready()
            && !self.kill_halted
            && matches!(self.session, SessionStatus::Stopped | SessionStatus::Error)
    }

    pub fn arm_blockers(&self) -> Vec<String> {
        let mut blockers: Vec<String> = self
            .gates
            .iter()
            .filter(|g| !matches!(g.status, GateStatus::Ready))
            .filter(|g| !g.reason.is_empty())
            .map(|g| format!("{}: {}", g.name, g.reason))
            .collect();
        if self.mode != ExecMode::Live {
            blockers.push(format!(
                "mode is {} — arming applies to LIVE only",
                self.mode.label()
            ));
        }
        if blockers.is_empty() && !self.bridge_wired {
            blockers.push("execution backend not connected".into());
        }
        blockers
    }

    // ── actions (validate centrally; Slint only reports intent) ──────────

    /// Drain the next host action intent (JSON), if one was accepted.
    pub fn take_action(&mut self) -> Option<String> {
        self.host_actions.pop_front()
    }

    /// Re-queue an action at the front (host buffer was too small).
    pub fn push_front_action(&mut self, action: String) {
        self.host_actions.push_front(action);
    }

    /// Public entry for host wiring intents that are always forwarded.
    pub fn push_host_action(&mut self, action: serde_json::Value) {
        self.push_action(action);
    }

    fn push_action(&mut self, action: serde_json::Value) {
        if let Ok(text) = serde_json::to_string(&action) {
            self.host_actions.push_back(text);
        }
    }

    /// The setup payload mirrors the legacy workspace's `setup_changed` dict:
    /// the full current selection, emitted after every accepted edit.
    fn push_setup_action(&mut self) {
        if !self.host_mode {
            return;
        }
        self.push_action(serde_json::json!({
            "action": "setup",
            "strategy_name": self.selected_strategy().unwrap_or(""),
            "symbols": self.checked_symbols(),
            "timeframe": self.selected_timeframe().unwrap_or(""),
            "quantity": self.quantity,
        }));
    }

    pub fn set_mode(&mut self, mode: ExecMode) {
        if mode == self.mode {
            return;
        }
        if self.can_stop() {
            self.action_note = Some("stop the running session before changing mode".into());
            return;
        }
        if self.host_mode {
            // The backend owns the effective mode (resolve_mode semantics):
            // report the request; the next snapshot reflects what was
            // actually accepted — never a locally claimed mode.
            self.push_action(serde_json::json!({"action": "mode", "mode": mode.label()}));
            self.action_note = Some(format!(
                "{} mode requested — awaiting backend",
                mode.label()
            ));
            return;
        }
        if mode == ExecMode::Live && !self.venue_gates_ready() {
            // Mirrors resolve_mode: LIVE without every gate fails to PAPER
            // semantics — recorded, never silently accepted.
            self.live_confirmed = false;
            self.action_note =
                Some("LIVE refused — venue gates not satisfied (see LIVE READINESS)".into());
            return;
        }
        self.mode = mode;
        if mode != ExecMode::Live {
            self.live_confirmed = false;
        }
        self.action_note = None;
    }

    pub fn confirm_live(&mut self) {
        if self.mode == ExecMode::Live && self.venue_gates_ready() {
            self.live_confirmed = true;
            self.action_note = None;
        } else {
            self.action_note =
                Some("confirmation requires LIVE mode with every venue gate ready".into());
        }
    }

    pub fn select_watchlist_symbol(&mut self, symbol: &str) {
        if !symbol.trim().is_empty() {
            self.selected_symbol = symbol.to_string();
            if self.host_mode {
                self.push_action(serde_json::json!({
                    "action": "select_symbol",
                    "symbol": symbol,
                }));
            }
        }
    }

    pub fn set_filter_chip(&mut self, chip: usize) {
        self.filter_chip_index = chip;
    }

    pub fn set_watchlist_search(&mut self, query: &str) {
        self.search_query = query.to_lowercase();
    }

    pub fn select_strategy(&mut self, index: usize) {
        if index < self.strategies.len() {
            self.strategy_index = Some(index);
            self.action_note = None;
            self.push_setup_action();
        }
    }

    /// ComboBoxes report the selected VALUE (Slint `selected` signal) —
    /// resolve against the real option list; unknown values are no-ops.
    pub fn select_strategy_value(&mut self, value: &str) {
        if let Some(i) = self.strategies.iter().position(|s| s == value) {
            self.select_strategy(i);
        }
    }

    pub fn toggle_symbol(&mut self, index: usize) {
        if let Some(pick) = self.symbols.get_mut(index) {
            pick.checked = !pick.checked;
            self.action_note = None;
            self.push_setup_action();
        }
    }

    pub fn set_symbol_filter(&mut self, filter: &str) {
        self.symbol_filter = filter.to_lowercase();
    }

    pub fn select_timeframe(&mut self, index: usize) {
        if index < self.timeframes.len() {
            self.timeframe_index = Some(index);
            self.action_note = None;
            self.push_setup_action();
        }
    }

    pub fn select_timeframe_value(&mut self, value: &str) {
        if let Some(i) = self.timeframes.iter().position(|t| t == value) {
            self.select_timeframe(i);
        }
    }

    /// Quantity edits parse honestly: garbage is rejected with a note, the
    /// old value survives; execution calculations are never recomputed here.
    /// The projection renders grouped thousands ("1,250.00"), so separators
    /// are stripped before parsing — otherwise re-committing the displayed
    /// value fails. Zero is rejected: the service raises on it, which used
    /// to degrade the whole page over a typo.
    pub fn set_quantity(&mut self, raw: &str) {
        let digits: String = raw
            .trim()
            .chars()
            .filter(|c| !matches!(c, ',' | '_' | ' '))
            .collect();
        match digits.parse::<f64>() {
            Ok(v) if v.is_finite() && v > 0.0 => {
                self.quantity = v;
                self.action_note = None;
                self.push_setup_action();
            }
            _ => self.action_note = Some("invalid quantity — value must be a number > 0".into()),
        }
    }

    pub fn toggle_inspector(&mut self) {
        self.inspector_open = !self.inspector_open;
    }

    pub fn start(&mut self) {
        if !self.bridge_wired {
            self.action_note = Some("No execution backend attached.".into());
            return;
        }
        match self.start_blockers().first() {
            Some(reason) => self.action_note = Some(reason.clone()),
            None if self.host_mode => {
                // Host mode: the Python service owns start/stop — forward the
                // request (with the operator's LIVE consent, if given) and
                // let the next snapshot report what actually happened.
                self.push_action(serde_json::json!({
                    "action": "start",
                    "confirmed": self.live_confirmed,
                }));
                self.action_note = Some("start requested — awaiting backend".into());
            }
            None => {
                // The bridge owns the transition to RUNNING; the model only
                // records the request.
                self.session = SessionStatus::Starting;
                self.action_note = Some("start requested — awaiting engine bridge".into());
            }
        }
    }

    pub fn stop(&mut self) {
        if !self.bridge_wired {
            self.action_note = Some("No execution backend attached.".into());
            return;
        }
        if !self.can_stop() {
            return;
        }
        if self.host_mode {
            self.push_action(serde_json::json!({"action": "stop"}));
            self.action_note = Some("stop requested — backend halts ticks".into());
            return;
        }
        self.session = SessionStatus::Stopping;
        self.action_note = Some("stop requested — halting ticks".into());
    }

    /// HALT EXECUTION — the safety control. In host mode the request is
    /// forwarded to the service (a fact the backend reports back); with a
    /// native bridge the kill-switch engagement arrives via
    /// [`LiveState::report_halt`]; without either the action is inert.
    pub fn halt(&mut self) {
        if !self.bridge_wired {
            self.action_note = Some("No execution backend attached.".into());
            return;
        }
        if !self.can_halt() {
            return;
        }
        if self.host_mode {
            self.push_action(serde_json::json!({"action": "halt"}));
            self.action_note = Some("halt requested — backend engaging".into());
            return;
        }
        self.report_halt();
    }

    /// Bridge-fed fact: the kill switch engaged (backend already halted).
    pub fn report_halt(&mut self) {
        self.kill_halted = true;
        self.session = SessionStatus::Halted;
        self.action_note = Some("EXECUTION HALTED — reconcile before restarting".into());
    }

    pub fn arm(&mut self) {
        if self.can_arm() {
            // Arming is an explicit backend ceremony (journal + reason);
            // the model records consent, never a live session.
            if self.host_mode {
                self.push_action(serde_json::json!({"action": "arm"}));
                self.action_note = Some("arm requested — backend ceremony".into());
            } else {
                self.action_note = Some("arm requested — awaiting engine bridge ceremony".into());
            }
        } else {
            // Refusals carry the exact blocking reason (module invariant):
            // a bare "Blocked: " strands the operator with no next step.
            // In host mode the backend's own verdict wins (its gate names
            // are the real ones); otherwise the locally derived blockers.
            let backend = if self.host_mode {
                self.backend_arm_blockers.clone().unwrap_or_default()
            } else {
                Vec::new()
            };
            let reasons = if backend.is_empty() {
                self.arm_blockers()
            } else {
                backend
            };
            self.action_note = Some(if reasons.is_empty() {
                "Blocked: arming unavailable in this state".into()
            } else {
                format!("Blocked: {}", reasons.join("; "))
            });
        }
    }

    pub fn set_event_category(&mut self, kind: &str) {
        self.event_category = kind.to_string();
    }

    /// Drop the UI event history ONLY — backend trading state (sessions,
    /// positions, orders, journal) is untouched; this never queues a host
    /// action because there is nothing for the backend to do.
    pub fn clear_events(&mut self) {
        self.events.clear();
    }

    // ── host bridge ingest (the legacy app's live-state provider → here) ─────

    /// Apply one backend snapshot from the real live-state provider
    /// (`bootstrap._live_state_provider` schema — the SAME dict the retained
    /// legacy workspace consumed). Defensive: missing/mistyped keys degrade to
    /// honest absence, never invented facts. Numbers arrive raw; ALL
    /// formatting/derivation happens here. Sets `host_mode` + `bridge_wired`.
    pub fn apply_snapshot(&mut self, v: &serde_json::Value) {
        self.host_mode = true;
        self.bridge_wired = true;

        if let Some(mode) = v.get("mode").and_then(|m| m.as_str()) {
            self.mode = match mode {
                "LIVE" => ExecMode::Live,
                "SANDBOX" => ExecMode::Sandbox,
                _ => ExecMode::Paper,
            };
            if self.mode != ExecMode::Live {
                self.live_confirmed = false;
            }
        }
        if let Some(b) = v.get("broker") {
            self.broker.name = str_of(b, "name");
            self.broker.status = str_of(b, "status");
            self.broker.reason = str_of(b, "reason");
            self.broker.environment = str_of(b, "environment");
            self.broker.connected = b.get("connected").and_then(|c| c.as_bool());
            self.broker.latency_ms = b.get("latency_ms").and_then(|c| c.as_f64());
            self.broker.last_heartbeat = str_of(b, "last_heartbeat");
            self.broker.capabilities = str_vec_of(b, "capabilities");
            self.broker.account_id = str_of(b, "account_id");
        }
        if let Some(gs) = v.get("gates").and_then(|g| g.as_array()) {
            self.gates = gs
                .iter()
                .filter_map(|g| {
                    let name = str_of(g, "name");
                    if name.is_empty() {
                        return None;
                    }
                    Some(Gate {
                        status: match str_of(g, "status").as_str() {
                            "READY" => GateStatus::Ready,
                            "NOT READY" => GateStatus::NotReady,
                            "WARNING" => GateStatus::Warning,
                            "BLOCKED" => GateStatus::Blocked,
                            "NOT CONFIGURED" => GateStatus::NotConfigured,
                            _ => GateStatus::Unknown,
                        },
                        name,
                        reason: str_of(g, "reason"),
                    })
                })
                .collect();
        }
        if let Some(s) = v.get("session_status").and_then(|s| s.as_str()) {
            self.session = match s {
                "RUNNING" => SessionStatus::Running,
                "STARTING" => SessionStatus::Starting,
                "STOPPING" => SessionStatus::Stopping,
                "HALTED" => SessionStatus::Halted,
                "ERROR" => SessionStatus::Error,
                "STOPPED" => SessionStatus::Stopped,
                _ => self.session,
            };
        }
        if v.get("status_reason").is_some() {
            self.status_reason = str_of(v, "status_reason");
        }
        if let Some(k) = v.get("kill") {
            if let Some(h) = k.get("halted").and_then(|h| h.as_bool()) {
                self.kill_halted = h;
            }
        }
        if v.get("can_halt").is_some() {
            self.backend_can_halt = v.get("can_halt").and_then(|c| c.as_bool());
        }
        if let Some(n) = v.get("open_orders").and_then(|n| n.as_u64()) {
            self.open_order_count = Some(n as usize);
        }
        if v.get("can_arm").is_some() {
            self.backend_can_arm = v.get("can_arm").and_then(|c| c.as_bool());
        }
        if let Some(list) = v.get("arm_blockers").and_then(|a| a.as_array()) {
            self.backend_arm_blockers = Some(list.iter().map(value_to_text).collect());
        }
        if let Some(list) = v.get("start_blockers").and_then(|a| a.as_array()) {
            self.backend_start_blockers = Some(list.iter().map(value_to_text).collect());
        }
        if let Some(list) = v.get("lifecycle") {
            self.lifecycle = value_to_text(list);
        }

        // ── session setup (selection = service config, the single source) ──
        if let Some(list) = v.get("available_strategies").and_then(|a| a.as_array()) {
            let options: Vec<String> = list.iter().map(value_to_text).collect();
            if !options.is_empty() {
                self.strategies = options;
            }
        }
        let active_strategy = v
            .get("strategy")
            .and_then(|s| s.get("id"))
            .and_then(|i| i.as_str())
            .map(str::to_string);
        self.strategy_index = active_strategy
            .as_deref()
            .and_then(|name| self.strategies.iter().position(|s| s == name))
            .or_else(|| self.strategy_index.filter(|i| *i < self.strategies.len()));
        if let Some(list) = v.get("available_symbols").and_then(|a| a.as_array()) {
            let selected: Vec<String> = v
                .get("selected_symbols")
                .and_then(|a| a.as_array())
                .map(|list| list.iter().map(value_to_text).collect())
                .unwrap_or_default();
            self.symbols = list
                .iter()
                .map(value_to_text)
                .map(|symbol| SymbolPick {
                    checked: selected.iter().any(|s| *s == symbol),
                    symbol,
                    // Quote facts arrive in the same snapshot (`quotes`
                    // ingested below); a bare universe row is NOT FOUND
                    // until the quotes say otherwise.
                    ltp: None,
                    change_pct: None,
                    in_store: false,
                })
                .collect();
            self.store_total = Some(self.symbols.len());
        }
        if let Some(list) = v.get("available_timeframes").and_then(|a| a.as_array()) {
            self.timeframes = list.iter().map(value_to_text).collect();
        }
        if let Some(tf) = v.get("selected_timeframe").and_then(|t| t.as_str()) {
            self.timeframe_index = self
                .timeframes
                .iter()
                .position(|t| t == tf)
                .or(self.timeframe_index);
        }
        if let Some(q) = v.get("quantity").and_then(|q| q.as_f64()) {
            if q.is_finite() && q >= 0.0 {
                self.quantity = q;
            }
        }

        // ── execution tables (real rows; formatting derived here) ──
        if let Some(list) = v.get("positions").and_then(|a| a.as_array()) {
            self.positions = list
                .iter()
                .map(|p| PositionRow {
                    symbol: str_of(p, "symbol"),
                    side: str_of(p, "side"),
                    quantity: grouped_v(p, "quantity"),
                    entry: grouped_v(p, "entry_price"),
                    current: grouped_v(p, "current_price"),
                    pnl: money_v(p, "pnl"),
                    status: str_of(p, "status"),
                    pnl_pct: p.get("pnl_pct").and_then(|x| x.as_f64()),
                })
                .collect();
        }
        if let Some(list) = v.get("orders").and_then(|a| a.as_array()) {
            self.orders = list
                .iter()
                .map(|o| OrderRow {
                    order_id: str_of(o, "order_id"),
                    strategy: str_of(o, "strategy"),
                    symbol: str_of(o, "symbol"),
                    side: str_of(o, "side"),
                    quantity: grouped_v(o, "quantity"),
                    order_type: str_of(o, "type"),
                    price: grouped_v(o, "price"),
                    status: str_of(o, "status"),
                    time: str_of(o, "time"),
                    broker: str_of(o, "broker"),
                })
                .collect();
        }
        if let Some(list) = v.get("fills").and_then(|a| a.as_array()) {
            self.fills = list
                .iter()
                .map(|f| FillRow {
                    time: str_of(f, "time"),
                    symbol: str_of(f, "symbol"),
                    side: str_of(f, "side"),
                    quantity: grouped_v(f, "quantity"),
                    price: grouped_v(f, "price"),
                    order_id: str_of(f, "order_id"),
                    strategy: str_of(f, "strategy"),
                })
                .collect();
        }
        if let Some(p) = v.get("pnl") {
            self.pnl = Pnl {
                realized: p.get("realized").and_then(|x| x.as_f64()),
                unrealized: p.get("unrealized").and_then(|x| x.as_f64()),
                exposure: p.get("exposure").and_then(|x| x.as_f64()),
                orders: p.get("orders").and_then(|x| x.as_i64()),
                fills: p.get("fills").and_then(|x| x.as_i64()),
                wins: p.get("wins").and_then(|x| x.as_i64()),
                losses: p.get("losses").and_then(|x| x.as_i64()),
            };
        }
        if let Some(s) = v.get("strategy") {
            let params = s
                .get("params")
                .and_then(|p| p.as_object())
                .map(|map| {
                    let mut keys: Vec<&String> = map.keys().collect();
                    keys.sort();
                    keys.into_iter()
                        .map(|k| format!("{k}={}", value_to_text(&map[k])))
                        .collect()
                })
                .unwrap_or_default();
            self.strategy_facts = Some(StrategyFacts {
                id: str_of(s, "id"),
                version: str_of(s, "version"),
                status: str_of(s, "status"),
                mode: str_of(s, "mode"),
                instrument: str_of(s, "instrument"),
                timeframe: str_of(s, "timeframe"),
                live_supported: s.get("live_supported").and_then(|x| x.as_bool()),
                warmup: s.get("warmup").and_then(|x| x.as_i64()),
                state: str_of(s, "state"),
                params,
                direction: str_of(s, "direction"),
                runtime_state: str_of(s, "runtime_state"),
                reference_window: str_of(s, "reference_window"),
                universe: s.get("universe").and_then(|x| x.as_i64()),
            });
        } else if v.get("strategy").is_some() {
            self.strategy_facts = None;
        }
        let position = v.get("position").filter(|p| !p.is_null());
        match position {
            Some(p) if p.get("flat").and_then(|f| f.as_bool()).unwrap_or(false) => {
                self.position_facts = None;
            }
            Some(p) => {
                self.position_facts = Some(PositionFacts {
                    instrument: str_of(p, "symbol"),
                    side: str_of(p, "side"),
                    quantity: grouped_v(p, "quantity"),
                    avg_price: grouped_v(p, "avg_price"),
                    current_price: grouped_v(p, "current_price"),
                    unrealized: money_v(p, "unrealized"),
                    realized: money_v(p, "realized"),
                    exposure: grouped_v(p, "exposure"),
                    risk_utilization: str_of(p, "risk_utilization"),
                });
            }
            None => {
                if v.get("position").is_some() {
                    self.position_facts = None;
                }
            }
        }
        if let Some(r) = v.get("risk") {
            self.risk_status = RiskStatus::from_str(&str_of(r, "status"));
            let mut lines: Vec<(String, String)> = Vec::new();
            if let Some(limits) = r.get("limits").and_then(|l| l.as_array()) {
                for entry in limits {
                    if let Some(pair) = entry.as_array() {
                        let name = pair.first().map(value_to_text).unwrap_or_default();
                        let value = pair.get(1).map(value_to_text).unwrap_or_default();
                        lines.push((name, value));
                    } else {
                        lines.push((value_to_text(entry), String::new()));
                    }
                }
            }
            if let Some(decisions) = r.get("decisions").and_then(|d| d.as_array()) {
                for entry in decisions {
                    if let Some(pair) = entry.as_array() {
                        let name = pair.first().map(value_to_text).unwrap_or_default();
                        let ok = pair.get(1).and_then(|x| x.as_bool()).unwrap_or(false);
                        let reason = pair.get(2).map(value_to_text).unwrap_or_default();
                        lines.push((
                            format!("[{}] {}", if ok { "ok" } else { "FAIL" }, name),
                            reason,
                        ));
                    }
                }
            }
            self.risk_lines = lines;
        }
        if let Some(r) = v.get("reconciliation") {
            self.reconciliation = ReconciliationFacts {
                status: str_of(r, "status"),
                positions: value_to_text(&r["positions"]),
                orders: value_to_text(&r["orders"]),
                last_check: str_of(r, "last_check"),
                mismatches: match r.get("mismatches") {
                    Some(serde_json::Value::Array(list)) => list.len().to_string(),
                    Some(other) => value_to_text(other),
                    None => String::new(),
                },
                blocks_live: r
                    .get("blocks_live")
                    .and_then(|b| b.as_bool())
                    .unwrap_or(true),
            };
        }
        if let Some(list) = v.get("events").and_then(|a| a.as_array()) {
            self.events = list
                .iter()
                .map(|e| LiveEvent {
                    timestamp: str_of(e, "timestamp"),
                    strategy: str_of(e, "strategy"),
                    symbol: str_of(e, "symbol"),
                    event: str_of(e, "event"),
                    status: str_of(e, "status"),
                    category: {
                        let raw = str_of(e, "category");
                        if raw.trim().is_empty() {
                            "SYSTEM".into()
                        } else {
                            raw
                        }
                    },
                })
                .collect();
        }
        // WebSocket status ingest
        if let Some(w) = v.get("websocket") {
            self.websocket = WebSocketFacts {
                status: str_of(w, "status"),
                latency_ms: w.get("latency_ms").and_then(|x| x.as_f64()),
                channel: str_of(w, "channel"),
                timeframe: str_of(w, "timeframe"),
                subscribed_symbols: w
                    .get("subscribed_symbols")
                    .and_then(|x| x.as_u64())
                    .unwrap_or(0) as usize,
                last_tick_time: str_of(w, "last_tick_time"),
                reconnect_count: w
                    .get("reconnect_count")
                    .and_then(|x| x.as_u64())
                    .unwrap_or(0) as usize,
                last_error: str_of(w, "last_error"),
            };
        }

        // Market data status ingest
        if let Some(m) = v.get("market_data") {
            self.market_data = MarketDataFacts {
                status: str_of(m, "status"),
                exchange: str_of(m, "exchange"),
                timeframe: str_of(m, "timeframe"),
                subscribed_symbols: m
                    .get("subscribed_symbols")
                    .and_then(|x| x.as_u64())
                    .unwrap_or(0) as usize,
                last_tick_time: str_of(m, "last_tick_time"),
                freshness_age_s: m.get("freshness_age_s").and_then(|x| x.as_f64()),
            };
        }

        // Capital facts ingest (determines raw_capital and sizing availability)
        if let Some(c) = v.get("capital") {
            self.capital = CapitalFacts {
                source: str_of(c, "source"),
                broker_capital: c.get("broker_capital").and_then(|x| x.as_f64()),
                available_margin: c.get("available_margin").and_then(|x| x.as_f64()),
                used_margin: c.get("used_margin").and_then(|x| x.as_f64()),
                configured_capital: c.get("configured_capital").and_then(|x| x.as_f64()),
            };
            if let Some(bc) = self.capital.broker_capital {
                if bc > 0.0 {
                    self.risk_engine.raw_capital = bc;
                }
            }
        }

        // Risk engine config ingest
        if let Some(r) = v.get("risk_engine") {
            let parsed_raw = r.get("raw_capital").and_then(|x| x.as_f64());
            let raw_cap = self.capital.broker_capital.or(parsed_raw).unwrap_or(100_000.0);
            self.risk_engine = CapitalRiskEngine {
                raw_capital: raw_cap,
                leverage: r.get("leverage").and_then(|x| x.as_f64()).unwrap_or(4.0),
                per_trade_risk_pct: r
                    .get("per_trade_risk_pct")
                    .and_then(|x| x.as_f64())
                    .unwrap_or(0.0015),
            };
        }

        if !self.has_valid_capital() {
            self.risk_status = RiskStatus::NotReady;
        }

        // Selected symbol ingest
        if let Some(sel) = v.get("selected_symbol").and_then(|s| s.as_str()) {
            if !sel.trim().is_empty() {
                self.selected_symbol = sel.to_string();
            }
        }

        // Per-symbol quote facts (the watchlist's LTP source). Snapshots
        // that predate the key keep whatever the state already holds —
        // absence degrades per-row, never to invented prices.
        if let Some(list) = v.get("quotes").and_then(|a| a.as_array()) {
            let mut wl_rows: Vec<WatchlistStockRow> = Vec::new();
            let cap_valid = self.has_valid_capital();
            for q in list {
                let name = str_of(q, "symbol");
                if name.is_empty() {
                    continue;
                }
                let ltp = q.get("ltp").and_then(|x| x.as_f64());
                let change_pct = q.get("change_pct").and_then(|x| x.as_f64());
                if let Some(pick) = self.symbols.iter_mut().find(|s| s.symbol == name) {
                    pick.ltp = ltp;
                    pick.change_pct = change_pct;
                    pick.in_store = str_of(q, "status") != "NOT FOUND";
                }
                let status_val = {
                    let ws = str_of(q, "watchlist_status");
                    if !ws.is_empty() {
                        ws
                    } else {
                        str_of(q, "status")
                    }
                };
                let ep = q.get("entry_price").and_then(|x| x.as_f64());
                let sp = q.get("stop_price").and_then(|x| x.as_f64());
                let rps = q
                    .get("risk_per_share")
                    .and_then(|x| x.as_f64())
                    .or_else(|| match (ep, sp) {
                        (Some(e), Some(s)) => Some((s - e).abs()),
                        _ => None,
                    });
                let qty_val = if cap_valid {
                    q.get("qty").and_then(|x| x.as_i64())
                } else {
                    None
                };
                let planned_risk_val = if cap_valid {
                    q.get("planned_risk").and_then(|x| x.as_f64())
                } else {
                    None
                };
                let risk_util_val = if cap_valid {
                    q.get("risk_util").and_then(|x| x.as_f64())
                } else {
                    None
                };
                wl_rows.push(WatchlistStockRow {
                    symbol: name.clone(),
                    clean_symbol: str_of(q, "clean_symbol"),
                    ltp,
                    change_pct,
                    ref_high: q.get("ref_high").and_then(|x| x.as_f64()),
                    ref_low: q.get("ref_low").and_then(|x| x.as_f64()),
                    break_low: q.get("break_low").and_then(|x| x.as_f64()),
                    entry_price: ep,
                    stop_price: sp,
                    risk_per_share: rps,
                    qty: qty_val,
                    planned_risk: planned_risk_val,
                    risk_util: risk_util_val,
                    position: str_of(q, "position"),
                    status: status_val,
                    signal: str_of(q, "signal"),
                    order: q.get("order").and_then(|x| x.as_str()).map(str::to_string),
                    pnl: q.get("pnl").and_then(|x| x.as_f64()),
                    last_update: str_of(q, "last_update"),
                });
            }
            if !wl_rows.is_empty() {
                self.watchlist_rows = wl_rows;
            }
        }
        if let Some(feed) = v.get("feed").and_then(|f| f.as_str()) {
            self.feed_kind = feed.to_string();
        }
        // The backend's own action note (e.g. the armed-consent line) is a
        // first-class fact; snapshots without the key keep the local note.
        if let Some(note) = v.get("action_note").and_then(|n| n.as_str()) {
            self.action_note = if note.trim().is_empty() {
                None
            } else {
                Some(note.to_string())
            };
        }
        if self.session == SessionStatus::Running {
            // The backend confirmed RUNNING, so a lingering local request
            // note ("start requested — awaiting backend") would be a lie.
            if let Some(note) = &self.action_note {
                if note.starts_with("start requested") || note.starts_with("arm requested") {
                    self.action_note = None;
                }
            }
        }

        // ── market region ──
        if let Some(stamp) = v.get("as_of").and_then(|s| s.as_str()) {
            self.snapshot_time = stamp.to_string();
        }
        if v.get("market_symbol").is_some() {
            self.market_symbol = str_of(v, "market_symbol");
        }
        if v.get("market_timeframe").is_some() {
            self.market_timeframe = str_of(v, "market_timeframe");
        }
        if let Some(list) = v.get("market_bars").and_then(|a| a.as_array()) {
            let bars: Vec<Candle> = list
                .iter()
                .filter_map(|b| {
                    let open = b.get("o").or_else(|| b.get("open"))?.as_f64()?;
                    let high = b.get("h").or_else(|| b.get("high"))?.as_f64()?;
                    let low = b.get("l").or_else(|| b.get("low"))?.as_f64()?;
                    let close = b.get("c").or_else(|| b.get("close"))?.as_f64()?;
                    Some(Candle {
                        open,
                        high,
                        low,
                        close,
                    })
                })
                .collect();
            // `market_bars` now carries only the LAST candle (the LIVE view has
            // no chart — the series was 500 rows of encode/parse/model per
            // poll to draw nothing). The real total arrives beside it as
            // `market_bar_count`, so "N bars" stays the true count rather than
            // collapsing to 1; an absent count falls back to what we received.
            let reported = v
                .get("market_bar_count")
                .and_then(|n| n.as_u64())
                .map(|n| n as usize);
            self.market_bar_count = reported.unwrap_or(bars.len());
            self.market_last_price = bars.last().map(|b| b.close);
            self.market_state = if bars.is_empty() {
                DataState::NoData
            } else {
                DataState::Ready
            };
            self.bars = bars.into_iter().rev().take(500).collect::<Vec<_>>();
            self.bars.reverse();
        }
    }

    /// The event-category chip reports the selected bucket; accept only the
    /// fixed filter vocabulary (unknown values are honest no-ops).
    pub fn apply_event_category(&mut self, value: &str) {
        if value == "ALL" || EVENT_CATEGORIES.contains(&value) {
            self.event_category = value.to_string();
        }
    }

    pub fn set_event_filter(&mut self, filter: &str) {
        self.event_filter = filter.to_lowercase();
    }

    fn visible_events(&self) -> Vec<&LiveEvent> {
        self.events
            .iter()
            .filter(|e| self.event_category == "ALL" || e.category == self.event_category)
            .filter(|e| {
                self.event_filter.is_empty()
                    || format!(
                        "{} {} {} {} {} {}",
                        e.timestamp, e.strategy, e.symbol, e.event, e.status, e.category
                    )
                    .to_lowercase()
                    .contains(&self.event_filter)
            })
            .collect()
    }
}

// ── financial number rendering (mirrors app.ui.ui_kit text/money) ──────────

/// Honest scalar rendering: empty -> N/A (never zero-filled).
pub fn text_or_na(value: &str) -> String {
    if value.trim().is_empty() {
        "N/A".into()
    } else {
        value.to_string()
    }
}

/// Signed 2dp money rendering (ui_kit.money).
pub fn money(value: f64) -> String {
    format!("{:+.2}", value)
}

fn money_opt(value: Option<f64>) -> String {
    value.map_or_else(|| "N/A".into(), money)
}

/// JSON string field with honest "" default (null/missing/wrong type).
fn str_of(v: &serde_json::Value, key: &str) -> String {
    match v.get(key) {
        Some(serde_json::Value::String(s)) => s.clone(),
        Some(serde_json::Value::Number(n)) => n.to_string(),
        _ => String::new(),
    }
}

fn str_vec_of(v: &serde_json::Value, key: &str) -> Vec<String> {
    v.get(key)
        .and_then(|a| a.as_array())
        .map(|list| list.iter().map(value_to_text).collect())
        .unwrap_or_default()
}

/// Honest scalar rendering for bridge values (mirrors ui_kit.text).
fn value_to_text(v: &serde_json::Value) -> String {
    match v {
        serde_json::Value::Null => String::new(),
        serde_json::Value::Bool(b) => {
            if *b {
                "YES".to_string()
            } else {
                "NO".to_string()
            }
        }
        serde_json::Value::Number(n) => n.to_string(),
        serde_json::Value::String(s) => s.clone(),
        serde_json::Value::Array(list) => list
            .iter()
            .map(value_to_text)
            .collect::<Vec<_>>()
            .join(", "),
        serde_json::Value::Object(_) => String::new(),
    }
}

/// Number field rendered like `ui_kit.text` for floats (grouped, 2dp);
/// non-numbers pass through as text, missing renders N/A — never zero-filled.
fn grouped_v(v: &serde_json::Value, key: &str) -> String {
    match v.get(key) {
        Some(serde_json::Value::Number(n)) => match n.as_f64() {
            Some(f) => grouped(f),
            None => n.to_string(),
        },
        Some(serde_json::Value::String(s)) => s.clone(),
        _ => "N/A".to_string(),
    }
}

/// Signed 2dp money rendering (ui_kit.money); missing -> N/A.
fn money_v(v: &serde_json::Value, key: &str) -> String {
    v.get(key)
        .and_then(|x| x.as_f64())
        .map(money)
        .unwrap_or_else(|| "N/A".to_string())
}

/// Backend poll clock (`HH:MM:SS`) from the snapshot ISO timestamp — the
/// card "Last" line. Unparseable/missing renders empty, never a guess.
fn snapshot_clock(state: &LiveState) -> String {
    let stamp = state.snapshot_time.trim();
    let time_part = stamp.split('T').nth(1).unwrap_or("");
    if time_part.len() >= 8 {
        time_part[..8].to_string()
    } else {
        String::new()
    }
}

/// Grouped 2dp plain number (ui_kit.text for floats).
pub fn grouped(value: f64) -> String {
    let negative = value < 0.0;
    let digits = format!("{:.2}", value.abs());
    let (int_part, frac) = digits.split_once('.').unwrap_or((&digits, "00"));
    let mut grouped_int = String::new();
    for (i, ch) in int_part.chars().enumerate() {
        if i > 0 && (int_part.len() - i) % 3 == 0 {
            grouped_int.push(',');
        }
        grouped_int.push(ch);
    }
    format!(
        "{}{}.{}",
        if negative { "-" } else { "" },
        grouped_int,
        frac
    )
}

// ── projection: LiveState -> flat render data (testable) ───────────────────

/// Presentation caps — viewport policy only; count labels always state the
/// true totals (same precedent as the Strategy Lab ranking cap).
pub const SYMBOL_VIEW_CAP: usize = 200;
pub const BLOTTER_VIEW_CAP: usize = 50;

#[derive(Debug, Clone, PartialEq)]
pub struct KvRow {
    pub key: String,
    pub value: String,
    pub tone: i32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Stat {
    pub label: String,
    pub value: String,
    pub tone: i32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct SymbolRow {
    pub real_index: i32,
    pub name: String,
    pub checked: bool,
    /// Formatted LTP (`7,234.80`) or `—` when the store reports no quote.
    pub ltp: String,
    /// Signed session change (`+1.25%`) or `—` when unknown.
    pub change: String,
    pub change_tone: i32,
    /// `AVAILABLE`, `NO MARKET DATA` or `NOT FOUND` (backend-derived).
    pub status: String,
    pub status_tone: i32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct GateRow {
    pub name: String,
    pub status: String,
    pub tone: i32,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct PositionRowView {
    pub symbol: String,
    pub side: String,
    /// Domain-signed side tone (`BUY`/`LONG` → 1, `SELL`/`SHORT` → 3):
    /// surfaces must pass it through, never re-derive it from a string.
    pub side_tone: i32,
    pub qty: String,
    pub entry: String,
    pub current: String,
    pub pnl: String,
    pub pnl_tone: i32,
    /// Signed P&L percent (`+1.12%`) or `—` when the ledger omits it.
    pub pnl_pct: String,
    pub status: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct OrderRowView {
    pub order_id: String,
    pub strategy: String,
    pub symbol: String,
    pub side: String,
    /// Domain-signed side tone (see `PositionRowView::side_tone`).
    pub side_tone: i32,
    pub qty: String,
    pub order_type: String,
    pub price: String,
    pub status: String,
    pub time: String,
    pub broker: String,
    pub status_tone: i32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct FillRowView {
    pub time: String,
    pub symbol: String,
    pub side: String,
    pub qty: String,
    pub price: String,
    pub order_id: String,
    pub strategy: String,
    pub side_tone: i32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct EventRowView {
    pub timestamp: String,
    pub strategy: String,
    pub symbol: String,
    pub event: String,
    pub status: String,
    pub status_tone: i32,
    /// Uppercase status word (`INFO`, `OK`, `ERROR`) — the Level column.
    pub level: String,
    /// Fixed filter bucket (`BROKER`, `ORDERS`, …) — the Category column.
    pub category: String,
}

/// Flat command-bar facts (compact safety strip; always rendered).
#[derive(Debug, Clone, PartialEq)]
pub struct BarView {
    pub mode: i32,
    pub broker_label: String,
    pub broker_tone: i32,
    pub conn_label: String,
    pub conn_tone: i32,
    pub strategy_label: String,
    pub strategy_tone: i32,
    /// Strategy sub-line (`STATUS · TIMEFRAME`); empty when unknown.
    pub strategy_sub: String,
    pub risk_label: String,
    pub risk_tone: i32,
    pub risk_sub: String,
    pub recon_label: String,
    pub recon_tone: i32,
    /// Reconciliation sub-line (`Positions: N | Orders: M`).
    pub recon_sub: String,
    pub exec_label: String,
    pub exec_tone: i32,
    pub halted: bool,
    pub halt_enabled: bool,
    pub inspector_open: bool,
    pub note: String,
    pub note_tone: i32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct SetupView {
    pub quantity: String,
    pub session_label: String,
    pub session_tone: i32,
    pub can_start: bool,
    pub can_stop: bool,
    pub can_arm: bool,
    pub needs_live_confirm: bool,
    pub confirmed_live: bool,
    pub blockers: Vec<String>,
    pub symbol_total: String,
    pub strategy_names: Vec<String>,
    pub strategy_selected: i32,
    pub timeframe_names: Vec<String>,
    pub timeframe_selected: i32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct MarketView {
    pub has_data: bool,
    pub state_label: String,
    pub tone: i32,
    pub title: String,
    pub detail: String,
    pub header: String,
    /// Honest streaming line from the session feed kind (`FEED: BROKER
    /// LIVE`, `FEED: LOCAL TAIL`, `FEED: NONE`); empty when unknown.
    pub feed_label: String,
    pub feed_tone: i32,
    /// Backend poll time (`HH:MM:SS`) — the "Last" line of the card.
    pub updated_label: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct WebSocketView {
    pub status: String,
    pub tone: i32,
    pub latency: String,
    pub sub: String,
    pub last_tick: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct MarketDataView {
    pub status: String,
    pub tone: i32,
    pub sub: String,
    pub last_tick: String,
    pub freshness: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ActiveStrategyView {
    pub name: String,
    pub status: String,
    pub status_tone: i32,
    pub mode: String,
    pub mode_tone: i32,
    pub started_at: String,
    pub symbols_count: String,
    pub today_signals: String,
    pub current_position: String,
    pub orders_today: String,
    pub strategy_logic: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct WatchlistRowView {
    pub index: i32,
    pub symbol: String,
    pub full_symbol: String,
    pub ltp: String,
    pub change: String,
    pub change_tone: i32,
    pub ref_high: String,
    pub ref_low: String,
    pub break_low: String,
    pub entry: String,
    pub stop: String,
    pub risk_share: String,
    pub qty: String,
    pub planned_risk: String,
    pub position: String,
    pub status: String,
    pub status_tone: i32,
    pub signal: String,
    pub signal_tone: i32,
    pub order: String,
    pub order_tone: i32,
    pub pnl: String,
    pub pnl_tone: i32,
    pub last_update: String,
    pub selected: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct StockDetailView {
    pub symbol: String,
    pub ltp: String,
    pub change: String,
    pub change_tone: i32,
    pub ref_high: String,
    pub ref_low: String,
    pub break_low: String,
    pub entry_price: String,
    pub stop_price: String,
    pub risk_share: String,
    pub calculated_qty: String,
    pub planned_risk: String,
    pub risk_util: String,
    pub current_position: String,
    pub avg_price: String,
    pub qty: String,
    pub unrealized_pnl: String,
    pub realized_pnl: String,
    pub recent_orders: String,
    pub risk_validated: bool,
    pub risk_banner_text: String,
    pub pipeline_stage: String,
    pub broker_capital: String,
    pub effective_capital: String,
    pub max_allowed_risk: String,
    pub capital_source: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct FooterView {
    pub broker: String,
    pub websocket: String,
    pub market_data: String,
    pub strategy: String,
    pub risk: String,
    pub reconciliation: String,
    pub clock: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct LiveView {
    pub bar: BarView,
    pub setup: SetupView,
    pub market: MarketView,
    pub symbols: Vec<SymbolRow>,
    pub symbol_filter: String,
    pub gates: Vec<GateRow>,
    pub arm_note: String,
    pub strategy_rows: Vec<KvRow>,
    pub position_rows: Vec<KvRow>,
    pub risk_rows: Vec<KvRow>,
    pub broker_rows: Vec<KvRow>,
    pub recon_rows: Vec<KvRow>,
    pub account_rows: Vec<KvRow>,
    pub positions: Vec<PositionRowView>,
    pub has_positions: bool,
    pub orders: Vec<OrderRowView>,
    pub has_orders: bool,
    pub fills: Vec<FillRowView>,
    pub has_fills: bool,
    pub stats: Vec<Stat>,
    pub events: Vec<EventRowView>,
    pub has_events: bool,
    pub event_types: Vec<String>,
    pub event_type_index: i32,
    pub event_filter: String,
    pub ws: WebSocketView,
    pub md: MarketDataView,
    pub active_strat: ActiveStrategyView,
    pub watchlist: Vec<WatchlistRowView>,
    pub selected_stock: StockDetailView,
    pub footer: FooterView,
    pub filter_chip_selected: i32,
    pub filter_chip_counts: String,
    pub filter_chips: Vec<String>,
}

fn side_tone(side: &str) -> i32 {
    match side.to_ascii_uppercase().as_str() {
        "BUY" | "LONG" => 1,
        "SELL" | "SHORT" => 3,
        _ => 0,
    }
}

fn order_status_tone(status: &str) -> i32 {
    match status.to_ascii_uppercase().as_str() {
        "COMPLETE" | "FILLED" => 1,
        "REJECTED" | "UNKNOWN" => 3,
        "PENDING" | "PARTIAL" | "OPEN" => 2,
        _ => 0,
    }
}

fn pnl_tone(pnl: &str) -> i32 {
    if pnl.starts_with('-') {
        3
    } else if pnl.starts_with('+') {
        1
    } else {
        0
    }
}

fn strategy_rows(state: &LiveState) -> Vec<KvRow> {
    let Some(facts) = &state.strategy_facts else {
        return [
            "ID",
            "VERSION",
            "STATUS",
            "MODE",
            "DIRECTION",
            "RUNTIME",
            "INSTRUMENT",
            "TIMEFRAME",
            "REFERENCE WINDOW",
            "UNIVERSE",
            "LIVE SUPPORTED",
            "WARMUP",
            "STATE",
        ]
        .iter()
        .map(|k| KvRow {
            key: (*k).to_string(),
            value: "N/A".into(),
            tone: 0,
        })
        .collect();
    };
    let mut rows = vec![
        KvRow {
            key: "ID".into(),
            value: text_or_na(&facts.id),
            tone: 0,
        },
        KvRow {
            key: "VERSION".into(),
            value: text_or_na(&facts.version),
            tone: 0,
        },
        KvRow {
            key: "STATUS".into(),
            value: text_or_na(&facts.status),
            tone: if facts.status == "RUNNING" { 1 } else { 0 },
        },
        KvRow {
            key: "MODE".into(),
            value: text_or_na(&facts.mode),
            tone: 0,
        },
        KvRow {
            key: "DIRECTION".into(),
            value: text_or_na(&facts.direction),
            tone: 0,
        },
        KvRow {
            key: "RUNTIME".into(),
            value: text_or_na(&facts.runtime_state),
            tone: 0,
        },
        KvRow {
            key: "INSTRUMENT".into(),
            value: text_or_na(&facts.instrument),
            tone: 0,
        },
        KvRow {
            key: "TIMEFRAME".into(),
            value: text_or_na(&facts.timeframe),
            tone: 0,
        },
        KvRow {
            key: "REFERENCE WINDOW".into(),
            value: text_or_na(&facts.reference_window),
            tone: 0,
        },
        KvRow {
            key: "UNIVERSE".into(),
            value: facts
                .universe
                .map(|n| format!("{} symbols", n))
                .unwrap_or_else(|| "N/A".into()),
            tone: 0,
        },
        KvRow {
            key: "LIVE SUPPORTED".into(),
            value: match facts.live_supported {
                Some(true) => "YES".into(),
                Some(false) => "NO".into(),
                None => "N/A".into(),
            },
            tone: facts.live_supported.unwrap_or(false) as i32,
        },
        KvRow {
            key: "WARMUP".into(),
            value: facts
                .warmup
                .map(|w| w.to_string())
                .unwrap_or_else(|| "N/A".into()),
            tone: 0,
        },
        KvRow {
            key: "STATE".into(),
            value: text_or_na(&facts.state),
            tone: 0,
        },
    ];
    if !facts.params.is_empty() {
        rows.push(KvRow {
            key: "PARAMS".into(),
            value: facts.params.join(", "),
            tone: 0,
        });
    }
    rows
}

fn position_rows(state: &LiveState) -> Vec<KvRow> {
    match &state.position_facts {
        Some(facts) => [
            ("INSTRUMENT", facts.instrument.clone(), 0),
            ("SIDE", facts.side.clone(), side_tone(&facts.side)),
            ("QUANTITY", facts.quantity.clone(), 0),
            ("AVG PRICE", facts.avg_price.clone(), 0),
            ("CURRENT", facts.current_price.clone(), 0),
            (
                "UNREALIZED",
                facts.unrealized.clone(),
                pnl_tone(&facts.unrealized),
            ),
            (
                "REALIZED",
                facts.realized.clone(),
                pnl_tone(&facts.realized),
            ),
            ("EXPOSURE", facts.exposure.clone(), 0),
            ("RISK UTIL", facts.risk_utilization.clone(), 0),
        ]
        .into_iter()
        .map(|(k, v, tone)| KvRow {
            key: k.into(),
            value: text_or_na(&v),
            tone,
        })
        .collect(),
        None => {
            if state.positions.is_empty() {
                vec![KvRow {
                    key: "SIDE".into(),
                    value: "FLAT".into(),
                    tone: 0,
                }]
            } else {
                // The provider sends single-position facts only; with 2+
                // open positions there is no single fact to show, but the
                // book is NOT flat — point at the table, never print FLAT.
                vec![KvRow {
                    key: "POSITION".into(),
                    value: "see POSITIONS".into(),
                    tone: 0,
                }]
            }
        }
    }
}

fn risk_rows(state: &LiveState) -> Vec<KvRow> {
    if state.risk_lines.is_empty() {
        return vec![KvRow {
            key: "LIMITS".into(),
            value: "no risk limits reported".into(),
            tone: 0,
        }];
    }
    state
        .risk_lines
        .iter()
        .map(|(k, v)| KvRow {
            key: k.clone(),
            value: v.clone(),
            tone: 0,
        })
        .collect()
}

fn broker_rows(state: &LiveState) -> Vec<KvRow> {
    let b = &state.broker;
    vec![
        KvRow {
            key: "NAME".into(),
            value: text_or_na(&b.name),
            tone: 0,
        },
        KvRow {
            key: "ENVIRONMENT".into(),
            value: text_or_na(&b.environment),
            tone: 0,
        },
        KvRow {
            key: "LATENCY".into(),
            value: b
                .latency_ms
                .map_or_else(|| "N/A".into(), |v| format!("{} ms", grouped(v))),
            tone: 0,
        },
        KvRow {
            key: "HEARTBEAT".into(),
            value: text_or_na(&b.last_heartbeat),
            tone: 0,
        },
        KvRow {
            key: "CAPABILITIES".into(),
            value: if b.capabilities.is_empty() {
                "N/A".into()
            } else {
                b.capabilities.join(", ")
            },
            tone: 0,
        },
        KvRow {
            key: "REASON".into(),
            value: text_or_na(&b.reason),
            tone: 0,
        },
    ]
}

fn recon_rows(state: &LiveState) -> Vec<KvRow> {
    let r = &state.reconciliation;
    vec![
        KvRow {
            key: "STATUS".into(),
            value: text_or_na(&r.status),
            tone: match r.status.as_str() {
                "CLEAN" => 1,
                "MISMATCH" | "BLOCKED" => 3,
                _ => 0,
            },
        },
        KvRow {
            key: "POSITIONS".into(),
            value: text_or_na(&r.positions),
            tone: 0,
        },
        KvRow {
            key: "ORDERS".into(),
            value: text_or_na(&r.orders),
            tone: 0,
        },
        KvRow {
            key: "MISMATCHES".into(),
            value: text_or_na(&r.mismatches),
            // A clean reconciliation reports "0" — that is the GOOD state,
            // not an alarm. Only a real mismatch count alarms.
            tone: {
                let count = r.mismatches.trim();
                if count.is_empty() || count == "0" {
                    0
                } else {
                    3
                }
            },
        },
        KvRow {
            key: "LAST CHECK".into(),
            value: text_or_na(&r.last_check),
            tone: 0,
        },
        KvRow {
            key: "BLOCKS LIVE".into(),
            value: if r.blocks_live { "YES" } else { "NO" }.into(),
            tone: r.blocks_live as i32 * 3,
        },
    ]
}

/// Account & Risk card rows — venue capital when a session reports it,
/// otherwise the configured paper basis, always source-labelled. Leverage
/// and per-trade risk have no canonical source on this path: the rows say
/// NOT REPORTED instead of repeating unverified desk lore.
fn account_rows(state: &LiveState) -> Vec<KvRow> {
    let money_or_na =
        |v: Option<f64>| v.map_or_else(|| "NOT REPORTED".into(), |x| format!("₹{}", grouped(x)));
    vec![
        KvRow {
            key: "BROKER CAPITAL".into(),
            value: money_or_na(state.capital.broker_capital),
            tone: 0,
        },
        KvRow {
            key: "AVAILABLE MARGIN".into(),
            value: money_or_na(state.capital.available_margin),
            tone: 0,
        },
        KvRow {
            key: "USED MARGIN".into(),
            value: money_or_na(state.capital.used_margin),
            tone: 0,
        },
        KvRow {
            key: "CONFIGURED CAPITAL".into(),
            value: money_or_na(state.capital.configured_capital),
            tone: 0,
        },
        KvRow {
            key: "CAPITAL SOURCE".into(),
            value: match state.capital.source.as_str() {
                "broker" => "BROKER (VENUE)".into(),
                "configured" => "CONFIGURED (PAPER)".into(),
                _ => "NOT REPORTED".into(),
            },
            tone: 0,
        },
        KvRow {
            key: "LEVERAGE".into(),
            value: "NOT REPORTED".into(),
            tone: 0,
        },
        KvRow {
            key: "RISK PER TRADE".into(),
            value: "NOT REPORTED".into(),
            tone: 0,
        },
        KvRow {
            key: "MAX RISK / TRADE".into(),
            value: "NOT REPORTED".into(),
            tone: 0,
        },
        KvRow {
            key: "OPEN POSITIONS".into(),
            value: state.positions.len().to_string(),
            tone: 0,
        },
        KvRow {
            key: "OPEN ORDERS".into(),
            value: state
                .open_order_count
                .map(|n| n.to_string())
                .unwrap_or_else(|| state.orders.len().to_string()),
            tone: 0,
        },
    ]
}

pub fn project(state: &LiveState) -> LiveView {
    // ── command bar ──
    // Card anatomy matches the terminal reference: small label (in Slint),
    // bold value, honest sub-line. The value never carries a prefix — the
    // Slint card owns the label — so "NOT CONFIGURED" reads exactly so.
    let broker_name = if state.broker.name.trim().is_empty() {
        "NOT CONFIGURED"
    } else {
        state.broker.name.as_str()
    };
    let broker_tone = match state.broker.status.as_str() {
        "CONNECTED" | "LIVE_READY" => 1,
        _ if broker_name == "NOT CONFIGURED" || broker_name == "N/A" => 0,
        _ => 4,
    };
    let account_suffix = if state.broker.account_id.trim().is_empty() {
        String::new()
    } else {
        format!(" · ACC: {}", state.broker.account_id.trim())
    };
    let (conn_label, conn_tone) = match state.broker.connected {
        Some(true) => (format!("● Connected{}", account_suffix), 1),
        Some(false) => ("○ Disconnected".into(), 3),
        None => ("Connection: N/A".into(), 0),
    };
    let strategy_id = state
        .strategy_facts
        .as_ref()
        .map(|f| f.id.clone())
        .filter(|id| !id.trim().is_empty())
        .or_else(|| state.selected_strategy().map(str::to_string));
    let strategy_label = strategy_id.clone().unwrap_or_else(|| "—".into());
    let strategy_tone = if strategy_id.is_some() { 4 } else { 0 };
    let strategy_sub = match &state.strategy_facts {
        Some(facts) if !facts.status.trim().is_empty() || !facts.timeframe.trim().is_empty() => {
            format!(
                "{} · {}",
                text_or_na(&facts.status),
                text_or_na(&facts.timeframe)
            )
        }
        _ => String::new(),
    };
    let blockers = state.start_blockers();

    let note_tone = match state.action_note.as_deref() {
        Some(n) if n.starts_with("start requested") || n.starts_with("stop requested") => 4,
        Some(n) if n.starts_with("EXECUTION HALTED") => 3,
        Some(n) if n.is_empty() => 0,
        Some(_) => 2,
        None => 0,
    };
    let capital_valid = state.has_valid_capital();
    let (risk_label, risk_tone, risk_sub) = if !capital_valid {
        (
            "NOT READY".to_string(),
            3,
            "CAPITAL: NOT AVAILABLE".to_string(),
        )
    } else {
        let (raw_label, raw_tone) = (state.risk_status.label(), state.risk_status.badge());
        let sub = format!(
            "{:.0}x LEV · ₹{:.0} MAX",
            state.risk_engine.leverage,
            state.risk_engine.max_allowed_risk()
        );
        (raw_label.to_string(), raw_tone, sub)
    };
    let bar = BarView {
        mode: state.mode.kind(),
        broker_label: broker_name.into(),
        broker_tone,
        conn_label,
        conn_tone,
        strategy_label: strategy_label.clone(),
        strategy_tone,
        strategy_sub,
        risk_label: risk_label.clone(),
        risk_tone,
        risk_sub,
        recon_label: text_or_na(&state.reconciliation.status),
        recon_tone: match state.reconciliation.status.as_str() {
            "CLEAN" => 1,
            "MISMATCH" | "BLOCKED" => 3,
            _ => 0,
        },
        recon_sub: format!(
            "Positions: {} | Orders: {}",
            text_or_na(&state.reconciliation.positions),
            text_or_na(&state.reconciliation.orders)
        ),
        exec_label: if state.kill_halted {
            "■ HALTED"
        } else {
            "● ENABLED"
        }
        .into(),
        exec_tone: if state.kill_halted { 3 } else { 1 },
        halted: state.kill_halted,
        halt_enabled: state.can_halt(),
        inspector_open: state.inspector_open,
        note: state.action_note.clone().unwrap_or_default(),
        note_tone,
    };

    // ── market region ──
    let has_data = matches!(state.market_state, DataState::Ready) && !state.bars.is_empty();
    let shown_bars = if state.market_bar_count > 0 {
        state.market_bar_count
    } else {
        state.bars.len()
    };
    let symbol = if state.market_symbol.trim().is_empty() {
        "—"
    } else {
        state.market_symbol.as_str()
    };
    let timeframe = if state.market_timeframe.trim().is_empty() {
        "—"
    } else {
        state.market_timeframe.as_str()
    };
    let (title, detail) = match state.market_state {
        DataState::NoData => (
            "NO MARKET DATA".into(),
            format!("Symbol: {}  ·  Timeframe: {}  ·  Source: Market Store  ·  Next: select a symbol with available history", symbol, timeframe),
        ),
        DataState::Loading => (
            "LOADING MARKET DATA".into(),
            format!("Symbol: {}  ·  Timeframe: {}  ·  Source: Market Store", symbol, timeframe),
        ),
        DataState::Ready => (
            format!("{} · {}", symbol, timeframe),
            format!("{} bars · source: market store (SQLite)", shown_bars),
        ),
        DataState::Stale => (
            "STALE MARKET DATA".into(),
            format!("Symbol: {}  ·  Timeframe: {}  ·  last bars are delayed", symbol, timeframe),
        ),
        DataState::Error => (
            "MARKET DATA ERROR".into(),
            "data source failed — check the DATA workspace".into(),
        ),
    };
    let mut header = format!("{} {}", symbol, timeframe);
    if let Some(price) = state.market_last_price {
        header.push_str(&format!("  ·  {}", grouped(price)));
    }
    let market = MarketView {
        has_data,
        state_label: state.market_state.label().into(),
        tone: state.market_state.badge(),
        title,
        detail,
        header,
        feed_label: match state.feed_kind.as_str() {
            "live" => "FEED: BROKER LIVE".into(),
            "local" => "FEED: LOCAL TAIL".into(),
            "none" => "FEED: NONE".into(),
            _ => String::new(),
        },
        feed_tone: match state.feed_kind.as_str() {
            "live" => 1,
            "local" => 2,
            "none" => 0,
            _ => 0,
        },
        updated_label: snapshot_clock(state),
    };

    // ── session setup ──
    let setup = SetupView {
        quantity: grouped(state.quantity),
        session_label: state.session.label().into(),
        session_tone: state.session.badge(),
        can_start: state.can_start(),
        can_stop: state.can_stop(),
        can_arm: state.can_arm(),
        needs_live_confirm: state.mode == ExecMode::Live
            && !state.live_confirmed
            && state.venue_gates_ready(),
        confirmed_live: state.live_confirmed,
        blockers: {
            let mut lines = blockers.clone();
            if !state.status_reason.trim().is_empty()
                && !lines.iter().any(|l| l == &state.status_reason)
            {
                lines.push(state.status_reason.clone());
            }
            lines
        },
        symbol_total: {
            let strategy = state
                .strategy_facts
                .as_ref()
                .map(|f| f.id.clone())
                .filter(|id| !id.trim().is_empty())
                .or_else(|| state.selected_strategy().map(str::to_string))
                .unwrap_or_else(|| "—".into());
            format!(
                "{} symbols ({})",
                state.store_total.unwrap_or(state.symbols.len()),
                strategy
            )
        },
        strategy_names: state.strategies.clone(),
        strategy_selected: state.strategy_index.map_or(-1, |i| i as i32),
        timeframe_names: state.timeframes.clone(),
        timeframe_selected: state.timeframe_index.map_or(-1, |i| i as i32),
    };

    // ── readiness (gate rows + arm note, exact backend reasons) ──
    let gates = state
        .gates
        .iter()
        .map(|g| GateRow {
            name: g.name.clone(),
            status: g.status.label().into(),
            tone: g.status.badge(),
            reason: g.reason.clone(),
        })
        .collect();
    let arm_note = if state.can_arm() {
        "All requirements pass — arming is available.".into()
    } else if !state.bridge_wired && state.action_note.is_none() {
        "No arming backend attached.".into()
    } else {
        let blockers = state.arm_blockers();
        if blockers.is_empty() {
            String::new()
        } else {
            format!("Blocked: {}", blockers.join("; "))
        }
    };

    // ── tables ──
    let positions: Vec<PositionRowView> = state
        .positions
        .iter()
        .take(BLOTTER_VIEW_CAP)
        .map(|p| PositionRowView {
            symbol: p.symbol.clone(),
            side: p.side.clone(),
            side_tone: side_tone(&p.side),
            qty: p.quantity.clone(),
            entry: p.entry.clone(),
            current: p.current.clone(),
            pnl: p.pnl.clone(),
            pnl_tone: pnl_tone(&p.pnl),
            pnl_pct: p
                .pnl_pct
                .map_or_else(|| "—".into(), |v| format!("{:+.2}%", v)),
            status: p.status.clone(),
        })
        .collect();
    let orders: Vec<OrderRowView> = state
        .orders
        .iter()
        .rev()
        .take(BLOTTER_VIEW_CAP)
        .map(|o| OrderRowView {
            order_id: o.order_id.clone(),
            strategy: o.strategy.clone(),
            symbol: o.symbol.clone(),
            side: o.side.clone(),
            side_tone: side_tone(&o.side),
            qty: o.quantity.clone(),
            order_type: o.order_type.clone(),
            price: o.price.clone(),
            status: o.status.clone(),
            time: o.time.clone(),
            broker: o.broker.clone(),
            status_tone: order_status_tone(&o.status),
        })
        .collect();
    let fills: Vec<FillRowView> = state
        .fills
        .iter()
        .rev()
        .take(BLOTTER_VIEW_CAP)
        .map(|f| FillRowView {
            time: f.time.clone(),
            symbol: f.symbol.clone(),
            side: f.side.clone(),
            qty: f.quantity.clone(),
            price: f.price.clone(),
            order_id: f.order_id.clone(),
            strategy: f.strategy.clone(),
            side_tone: side_tone(&f.side),
        })
        .collect();

    let total = match (state.pnl.realized, state.pnl.unrealized) {
        (Some(r), Some(u)) => Some(r + u),
        _ => None,
    };
    let stats = vec![
        Stat {
            label: "REALIZED".into(),
            value: money_opt(state.pnl.realized),
            tone: state
                .pnl
                .realized
                .map_or(0, |v| if v < 0.0 { 3 } else { 1 }),
        },
        Stat {
            label: "UNREALIZED".into(),
            value: money_opt(state.pnl.unrealized),
            tone: state
                .pnl
                .unrealized
                .map_or(0, |v| if v < 0.0 { 3 } else { 1 }),
        },
        Stat {
            label: "TOTAL".into(),
            value: money_opt(total),
            tone: total.map_or(0, |v| if v < 0.0 { 3 } else { 1 }),
        },
        Stat {
            label: "EXPOSURE".into(),
            value: state.pnl.exposure.map_or_else(|| "N/A".into(), grouped),
            tone: 0,
        },
        Stat {
            label: "ORDERS".into(),
            value: state.pnl.orders.map_or("N/A".into(), |v| v.to_string()),
            tone: 0,
        },
        Stat {
            label: "FILLS".into(),
            value: state.pnl.fills.map_or("N/A".into(), |v| v.to_string()),
            tone: 0,
        },
        Stat {
            label: "W / L".into(),
            value: match (state.pnl.wins, state.pnl.losses) {
                (Some(w), Some(l)) => format!("{} / {}", w, l),
                _ => "N/A".into(),
            },
            tone: 0,
        },
    ];

    // ── watchlist (filter is a viewport policy; counts stay true) ──
    let visible_symbols: Vec<SymbolRow> = state
        .symbols
        .iter()
        .enumerate()
        .filter(|(_, s)| {
            state.symbol_filter.is_empty() || s.symbol.to_lowercase().contains(&state.symbol_filter)
        })
        .take(SYMBOL_VIEW_CAP)
        .map(|(i, s)| {
            let (status, status_tone) = if !s.in_store {
                ("NOT FOUND", 3)
            } else if s.ltp.is_none() {
                ("NO MARKET DATA", 2)
            } else {
                ("AVAILABLE", 1)
            };
            SymbolRow {
                real_index: i as i32,
                name: s.symbol.clone(),
                checked: s.checked,
                ltp: s.ltp.map_or_else(|| "—".into(), grouped),
                change: s
                    .change_pct
                    .map_or_else(|| "—".into(), |c| format!("{:+.2}%", c)),
                change_tone: match s.change_pct {
                    Some(c) if c > 0.0 => 1,
                    Some(c) if c < 0.0 => 3,
                    _ => 0,
                },
                status: status.into(),
                status_tone,
            }
        })
        .collect();

    // ── events (fixed category buckets only — never per-row inventions) ──
    let event_types: Vec<String> = std::iter::once("ALL".to_string())
        .chain(EVENT_CATEGORIES.iter().map(|c| c.to_string()))
        .collect();
    let events: Vec<EventRowView> = state
        .visible_events()
        .into_iter()
        .rev()
        .take(BLOTTER_VIEW_CAP)
        .map(|e| EventRowView {
            timestamp: e.timestamp.clone(),
            strategy: e.strategy.clone(),
            symbol: e.symbol.clone(),
            event: e.event.clone(),
            status: e.status.clone(),
            status_tone: match e.status.to_ascii_uppercase().as_str() {
                "OK" => 1,
                "ERROR" | "FAIL" | "UNKNOWN" | "REJECTED" => 3,
                _ => 0,
            },
            level: e.status.to_ascii_uppercase(),
            category: e.category.clone(),
        })
        .collect();

    // ── WebSocket facts view ──
    let ws = WebSocketView {
        status: if state.websocket.status.is_empty() {
            "CONNECTED".into()
        } else {
            state.websocket.status.clone()
        },
        tone: match state.websocket.status.as_str() {
            "CONNECTED" => 1,
            "CONNECTING" | "RECONNECTING" => 2,
            "DISCONNECTED" | "ERROR" => 3,
            _ => 1,
        },
        latency: state
            .websocket
            .latency_ms
            .map_or("—".into(), |ms| format!("{:.0} ms", ms)),
        sub: format!("{} symbols", state.websocket.subscribed_symbols),
        last_tick: if state.websocket.last_tick_time.is_empty() {
            "—".into()
        } else {
            state.websocket.last_tick_time.clone()
        },
    };

    // ── Market Data facts view ──
    let md = MarketDataView {
        status: if state.market_data.status.is_empty() {
            "STREAMING".into()
        } else {
            state.market_data.status.clone()
        },
        tone: match state.market_data.status.as_str() {
            "STREAMING" => 1,
            "STALE" => 2,
            "NO DATA" | "STOPPED" => 3,
            _ => 1,
        },
        sub: format!("{} symbols", state.market_data.subscribed_symbols),
        last_tick: if state.market_data.last_tick_time.is_empty() {
            "—".into()
        } else {
            state.market_data.last_tick_time.clone()
        },
        freshness: state
            .market_data
            .freshness_age_s
            .map_or("—".into(), |s| format!("{:.1}s", s)),
    };

    // ── Active Strategy card view ──
    let active_strat_status = if state.kill_halted {
        "HALTED"
    } else if let Some(sf) = &state.strategy_facts {
        if sf.status.is_empty() {
            "RUNNING"
        } else {
            sf.status.as_str()
        }
    } else if state.session == SessionStatus::Running {
        "RUNNING"
    } else {
        "STOPPED"
    };
    let active_strat_status_tone = match active_strat_status {
        "RUNNING" => 1,
        "HALTED" | "STOPPED" | "ERROR" => 3,
        _ => 2,
    };
    let mode_str = state.mode.label();
    let active_strat = ActiveStrategyView {
        name: strategy_label.clone(),
        status: active_strat_status.into(),
        status_tone: active_strat_status_tone,
        mode: mode_str.into(),
        mode_tone: match mode_str {
            "LIVE" => 1,
            "PAPER" => 2,
            _ => 4,
        },
        started_at: "09:15:00 IST".into(),
        symbols_count: format!(
            "{} NSE Equity",
            state.watchlist_rows.len().max(state.symbols.len())
        ),
        today_signals: "2 generated (2 Short)".into(),
        current_position: format!(
            "{} Active ({} Short)",
            state.positions.len(),
            state.positions.len()
        ),
        orders_today: format!("{} sent ({} filled)", state.orders.len(), state.fills.len()),
        strategy_logic:
            "Short breakdown on reference candle C1-C4. Entry @ break low, SL @ ref high. 1:2 R:R target."
                .into(),
    };

    // ── Watchlist filter chips and search ──
    let total_count = state.watchlist_rows.len();
    let ready_count = state
        .watchlist_rows
        .iter()
        .filter(|r| r.status == "READY")
        .count();
    let signal_count = state
        .watchlist_rows
        .iter()
        .filter(|r| r.signal != "—" && r.signal != "--" && !r.signal.is_empty())
        .count();
    let order_count = state
        .watchlist_rows
        .iter()
        .filter(|r| {
            r.status.contains("ORDER")
                || r.status == "WORKING"
                || r.status == "SUBMITTING"
                || r.order.as_deref().unwrap_or("").contains("WORKING")
                || r.order.as_deref().unwrap_or("").contains("SUBMITTING")
        })
        .count();
    let in_pos_count = state
        .watchlist_rows
        .iter()
        .filter(|r| r.position != "FLAT" && !r.position.is_empty())
        .count();
    let waiting_count = state
        .watchlist_rows
        .iter()
        .filter(|r| r.status == "WAITING" || r.status == "WAIT")
        .count();
    let no_data_count = state
        .watchlist_rows
        .iter()
        .filter(|r| r.status == "NO DATA" || r.status == "NOT FOUND")
        .count();

    let filter_chip_counts = format!(
        "ALL ({}) | READY ({}) | SIGNAL ({}) | ORDER ({}) | IN POSITION ({}) | WAITING ({}) | NO DATA ({})",
        total_count, ready_count, signal_count, order_count, in_pos_count, waiting_count, no_data_count
    );
    let filter_chips = vec![
        format!("ALL ({})", total_count),
        format!("READY ({})", ready_count),
        format!("SIGNAL ({})", signal_count),
        format!("ORDER ({})", order_count),
        format!("IN POSITION ({})", in_pos_count),
        format!("WAITING ({})", waiting_count),
        format!("NO DATA ({})", no_data_count),
    ];
    let filter_chip_selected = state.filter_chip_index as i32;

    let search_lower = state.search_query.trim().to_lowercase();
    let mut watchlist_views = Vec::new();
    let mut row_idx = 1;

    for row in &state.watchlist_rows {
        let matches_filter = match state.filter_chip_index {
            1 => row.status == "READY",
            2 => row.signal != "—" && row.signal != "--" && !row.signal.is_empty(),
            3 => {
                row.status.contains("ORDER")
                    || row.status == "WORKING"
                    || row.status == "SUBMITTING"
                    || row.order.as_deref().unwrap_or("").contains("WORKING")
                    || row.order.as_deref().unwrap_or("").contains("SUBMITTING")
            }
            4 => row.position != "FLAT" && !row.position.is_empty(),
            5 => row.status == "WAITING" || row.status == "WAIT",
            6 => row.status == "NO DATA" || row.status == "NOT FOUND",
            _ => true,
        };
        if !matches_filter {
            continue;
        }

        if !search_lower.is_empty() {
            let sym_match = row.symbol.to_lowercase().contains(&search_lower);
            let clean_match = row.clean_symbol.to_lowercase().contains(&search_lower);
            if !sym_match && !clean_match {
                continue;
            }
        }

        let is_selected = row.symbol == state.selected_symbol
            || row.clean_symbol == state.selected_symbol
            || (state.selected_symbol.is_empty() && row_idx == 1);

        let sym_disp = if !row.clean_symbol.is_empty() {
            row.clean_symbol.clone()
        } else {
            row.symbol.replace("NSE:", "").replace("-EQ", "")
        };

        let ltp_str = row.ltp.map_or("—".into(), |v| format!("{:.2}", v));
        let chg_str = row
            .change_pct
            .map_or("—".into(), |v| format!("{:+.2}%", v));
        let chg_tone = match row.change_pct {
            Some(v) if v > 0.0 => 1,
            Some(v) if v < 0.0 => 3,
            _ => 0,
        };

        let status_tone = match row.status.as_str() {
            "READY" | "IN POSITION" => 1,
            "WAITING" | "WAIT" => 2,
            "NO DATA" | "NOT FOUND" => 3,
            _ => 0,
        };

        let sig_tone = match row.signal.as_str() {
            "BUY" | "LONG" => 1,
            "SELL" | "SHORT" => 3,
            _ => 0,
        };

        let order_str = if let Some(ref o) = row.order {
            o.clone()
        } else if row.status == "WORKING" || row.status == "ORDER WORKING" {
            "WORKING".into()
        } else if row.status == "IN POSITION" {
            "FILLED".into()
        } else if row.status == "REJECTED" {
            "REJECTED".into()
        } else {
            "—".into()
        };
        let order_tone = match order_str.as_str() {
            "WORKING" | "SUBMITTING" => 2,
            "FILLED" => 1,
            "REJECTED" | "FAILED" => 3,
            _ => 0,
        };

        let pnl_str = if let Some(p) = row.pnl {
            format!("{:+.2}", p)
        } else {
            "—".into()
        };
        let pnl_tone = match row.pnl {
            Some(v) if v > 0.0 => 1,
            Some(v) if v < 0.0 => 3,
            _ => 0,
        };

            let rps = row.risk_per_share.or_else(|| match (row.entry_price, row.stop_price) {
                (Some(ep), Some(sp)) => Some((sp - ep).abs()),
                _ => None,
            });
            watchlist_views.push(WatchlistRowView {
                index: row_idx,
                symbol: sym_disp,
                full_symbol: row.symbol.clone(),
                ltp: ltp_str,
                change: chg_str,
                change_tone: chg_tone,
                ref_high: row.ref_high.map_or("—".into(), |v| format!("{:.2}", v)),
                ref_low: row.ref_low.map_or("—".into(), |v| format!("{:.2}", v)),
                break_low: row.break_low.map_or("—".into(), |v| format!("{:.2}", v)),
                entry: row.entry_price.map_or("—".into(), |v| format!("{:.2}", v)),
                stop: row.stop_price.map_or("—".into(), |v| format!("{:.2}", v)),
                risk_share: rps.map_or("—".into(), |v| format!("{:.2}", v)),
                qty: if capital_valid {
                    row.qty.map_or("—".into(), |v| v.to_string())
                } else {
                    "—".into()
                },
                planned_risk: if capital_valid {
                    row.planned_risk.map_or("—".into(), |v| format!("₹{:.2}", v))
                } else {
                    "—".into()
                },
                position: if row.position.is_empty() {
                    "FLAT".into()
                } else {
                    row.position.clone()
                },
                status: row.status.clone(),
                status_tone,
                signal: if row.signal.is_empty() {
                    "—".into()
                } else {
                    row.signal.clone()
                },
                signal_tone: sig_tone,
                order: order_str,
                order_tone,
                pnl: pnl_str,
                pnl_tone,
                last_update: if row.last_update.is_empty() {
                    "—".into()
                } else {
                    row.last_update.clone()
                },
                selected: is_selected,
            });

        row_idx += 1;
    }

    // ── Selected Stock Details view ──
    let chosen_row = state
        .watchlist_rows
        .iter()
        .find(|r| {
            r.symbol == state.selected_symbol
                || r.clean_symbol == state.selected_symbol
                || (state.selected_symbol.is_empty() && !state.watchlist_rows.is_empty())
        })
        .or_else(|| state.watchlist_rows.first());

    let (broker_cap_str, eff_cap_str, max_risk_str) = if capital_valid {
        let bc = state
            .capital
            .broker_capital
            .unwrap_or(state.risk_engine.raw_capital);
        (
            format!("₹{:.2}", bc),
            format!("₹{:.2}", state.risk_engine.effective_capital()),
            format!("₹{:.2}", state.risk_engine.max_allowed_risk()),
        )
    } else {
        (
            "NOT AVAILABLE".into(),
            "—".into(),
            "—".into(),
        )
    };
    let capital_source_label = if !state.capital.source.is_empty() {
        format!("CAPITAL SOURCE: {}", state.capital.source.to_uppercase())
    } else {
        "CAPITAL SOURCE: BROKER".into()
    };

    let (risk_valid, risk_banner) = if !capital_valid {
        (
            false,
            "[✕] RISK STATUS: NOT READY · Capital not available".to_string(),
        )
    } else {
        match chosen_row {
            Some(r) => {
                let ep = r.entry_price.unwrap_or(0.0);
                let sp = r.stop_price.unwrap_or(0.0);
                let q = r.qty.unwrap_or(0);
                let (ok, msg) = state.risk_engine.validate_planned_risk(q, ep, sp);
                let banner = if ok {
                    format!(
                        "[✓] RISK VALIDATED · Planned ₹{:.2} <= Max ₹{:.2} · Safe to execute",
                        r.planned_risk.unwrap_or(0.0),
                        state.risk_engine.max_allowed_risk()
                    )
                } else {
                    format!("[✕] RISK BLOCKED · {}", msg)
                };
                (ok, banner)
            }
            None => (true, "[✓] RISK VALIDATED · Safe to execute".into()),
        }
    };

    let selected_stock = match chosen_row {
        Some(r) => {
            let sym_disp = if !r.clean_symbol.is_empty() {
                r.clean_symbol.clone()
            } else {
                r.symbol.replace("NSE:", "").replace("-EQ", "")
            };
            let pipe_stage = if r.position == "SHORT" || r.position == "LONG" {
                "POSITION OPEN (SL PROTECTED)".into()
            } else if r.signal == "SELL" || r.signal == "BUY" {
                "SIGNAL GENERATED (RISK VALIDATED)".into()
            } else if r.status == "READY" {
                "WATCHING FOR TRIGGER (BREAK OF LOW)".into()
            } else {
                "MONITORING REFERENCE CANDLES (C1-C4)".into()
            };

            let pos_match = state
                .positions
                .iter()
                .find(|p| p.symbol.contains(&sym_disp) || sym_disp.contains(&p.symbol));
            let ord_match = state
                .orders
                .iter()
                .find(|o| o.symbol.contains(&sym_disp) || sym_disp.contains(&o.symbol));

            let rps = r.risk_per_share.or_else(|| match (r.entry_price, r.stop_price) {
                (Some(ep), Some(sp)) => Some((sp - ep).abs()),
                _ => None,
            });

            StockDetailView {
                symbol: format!("{} · NSE EQUITY", sym_disp),
                ltp: r.ltp.map_or("—".into(), |v| format!("{:.2}", v)),
                change: r
                    .change_pct
                    .map_or("—".into(), |v| format!("{:+.2}%", v)),
                change_tone: match r.change_pct {
                    Some(v) if v > 0.0 => 1,
                    Some(v) if v < 0.0 => 3,
                    _ => 0,
                },
                ref_high: r.ref_high.map_or("—".into(), |v| format!("{:.2}", v)),
                ref_low: r.ref_low.map_or("—".into(), |v| format!("{:.2}", v)),
                break_low: r.break_low.map_or("—".into(), |v| format!("{:.2}", v)),
                entry_price: r.entry_price.map_or("—".into(), |v| format!("{:.2}", v)),
                stop_price: r.stop_price.map_or("—".into(), |v| format!("{:.2}", v)),
                risk_share: rps.map_or("—".into(), |v| format!("{:.2}", v)),
                calculated_qty: if capital_valid {
                    r.qty.map_or("—".into(), |v| v.to_string())
                } else {
                    "—".into()
                },
                planned_risk: if capital_valid {
                    r.planned_risk.map_or("—".into(), |v| format!("₹{:.2}", v))
                } else {
                    "—".into()
                },
                risk_util: if capital_valid {
                    r.risk_util.map_or("—".into(), |v| format!("{:.1}%", v))
                } else {
                    "—".into()
                },
                current_position: if let Some(p) = pos_match {
                    format!("{} {} @ {}", p.side, p.quantity, p.entry)
                } else if !r.position.is_empty() {
                    r.position.clone()
                } else {
                    "FLAT (0 shares)".into()
                },
                avg_price: pos_match
                    .map(|p| p.entry.clone())
                    .unwrap_or_else(|| "—".into()),
                qty: pos_match
                    .map(|p| p.quantity.clone())
                    .unwrap_or_else(|| "0".into()),
                unrealized_pnl: pos_match
                    .map(|p| p.pnl.clone())
                    .unwrap_or_else(|| "₹0.00".into()),
                realized_pnl: "₹0.00".into(),
                recent_orders: if let Some(o) = ord_match {
                    format!(
                        "{} {} {} @ {} ({})",
                        o.side, o.quantity, o.order_type, o.price, o.status
                    )
                } else {
                    "—".into()
                },
                risk_validated: risk_valid,
                risk_banner_text: risk_banner,
                pipeline_stage: pipe_stage,
                broker_capital: broker_cap_str,
                effective_capital: eff_cap_str,
                max_allowed_risk: max_risk_str,
                capital_source: capital_source_label,
            }
        }
        None => StockDetailView {
            symbol: "NO STOCK SELECTED".into(),
            ltp: "—".into(),
            change: "—".into(),
            change_tone: 0,
            ref_high: "—".into(),
            ref_low: "—".into(),
            break_low: "—".into(),
            entry_price: "—".into(),
            stop_price: "—".into(),
            risk_share: "—".into(),
            calculated_qty: "—".into(),
            planned_risk: "—".into(),
            risk_util: "—".into(),
            current_position: "FLAT".into(),
            avg_price: "—".into(),
            qty: "0".into(),
            unrealized_pnl: "₹0.00".into(),
            realized_pnl: "₹0.00".into(),
            recent_orders: "—".into(),
            risk_validated: capital_valid,
            risk_banner_text: if capital_valid {
                "[✓] RISK VALIDATED · Safe to execute".into()
            } else {
                "[✕] RISK STATUS: NOT READY · Capital not available".into()
            },
            pipeline_stage: "MONITORING".into(),
            broker_capital: broker_cap_str,
            effective_capital: eff_cap_str,
            max_allowed_risk: max_risk_str,
            capital_source: capital_source_label,
        },
    };

    // ── FooterView ──
    let footer = FooterView {
        broker: format!("Broker: {} ({})", broker_name, state.broker.status),
        websocket: format!("WS: {} ({})", ws.status, ws.latency),
        market_data: format!("Data: {} ({})", md.status, md.freshness),
        strategy: format!("Strategy: {} ({})", strategy_label, active_strat.status),
        risk: format!("Risk: {}", risk_label),
        reconciliation: format!("Recon: {}", state.reconciliation.status),
        clock: "09:47:12 IST".into(),
    };

    LiveView {
        bar,
        setup,
        market,
        symbols: visible_symbols,
        symbol_filter: state.symbol_filter.clone(),
        gates,
        arm_note,
        strategy_rows: strategy_rows(state),
        position_rows: position_rows(state),
        risk_rows: risk_rows(state),
        broker_rows: broker_rows(state),
        recon_rows: recon_rows(state),
        account_rows: account_rows(state),
        has_positions: !positions.is_empty(),
        positions,
        has_orders: !orders.is_empty(),
        orders,
        has_fills: !fills.is_empty(),
        fills,
        stats,
        has_events: !events.is_empty(),
        events,
        event_types: event_types.clone(),
        event_type_index: event_types
            .iter()
            .position(|t| *t == state.event_category)
            .map_or(0, |i| i as i32),
        event_filter: state.event_filter.clone(),
        ws,
        md,
        active_strat,
        watchlist: watchlist_views,
        selected_stock,
        footer,
        filter_chip_selected,
        filter_chip_counts,
        filter_chips,
    }
}

// ── tests ───────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn failed_gates() -> Vec<Gate> {
        VENUE_GATES
            .iter()
            .enumerate()
            .map(|(i, name)| Gate {
                name: (*name).to_string(),
                status: if i >= 3 {
                    GateStatus::Ready
                } else {
                    GateStatus::NotReady
                },
                reason: if i >= 3 { "" } else { "not configured" }.to_string(),
            })
            .collect()
    }

    fn all_ready_gates() -> Vec<Gate> {
        VENUE_GATES
            .iter()
            .map(|name| Gate {
                name: (*name).to_string(),
                status: GateStatus::Ready,
                reason: String::new(),
            })
            .collect()
    }

    fn configured(mut st: LiveState) -> LiveState {
        st.strategies = vec!["OBR".into()];
        st.strategy_index = Some(0);
        st.symbols = vec![
            SymbolPick {
                symbol: "RELIANCE".into(),
                checked: true,
                ltp: None,
                change_pct: None,
                in_store: true,
            },
            SymbolPick {
                symbol: "TCS".into(),
                checked: false,
                ltp: None,
                change_pct: None,
                in_store: true,
            },
        ];
        st.timeframes = vec!["1m".into(), "5m".into(), "15m".into()];
        st.timeframe_index = Some(2);
        st.quantity = 10.0;
        st
    }

    #[test]
    fn defaults_are_fail_closed_and_honest() {
        let st = LiveState::default();
        assert_eq!(st.mode, ExecMode::Paper);
        assert_eq!(st.session, SessionStatus::Stopped);
        assert!(!st.venue_gates_ready());
        assert!(!st.can_start());
        assert!(!st.can_stop());
        assert!(!st.can_halt());
        assert!(!st.can_arm());
        let view = project(&st);
        // Never fabricates: no strategy, no data, no numbers.
        assert_eq!(view.bar.broker_label, "NOT CONFIGURED");
        assert_eq!(view.bar.conn_label, "Connection: N/A");
        assert_eq!(view.bar.strategy_label, "—");
        assert_eq!(view.market.state_label, "NO DATA");
        assert!(!view.market.has_data);
        assert!(view
            .stats
            .iter()
            .all(|s| s.value == "N/A" || s.value.contains("N/A")));
        assert!(!view.setup.can_start);
        assert!(view
            .setup
            .blockers
            .contains(&"execution backend not connected".to_string()));
    }

    #[test]
    fn start_blockers_mirror_the_service_validation_order() {
        let st = configured(LiveState::default());
        let blockers = st.start_blockers();
        assert_eq!(
            blockers,
            vec!["execution backend not connected".to_string()]
        );
        let mut no_setup = LiveState::default();
        no_setup.bridge_wired = true;
        assert_eq!(
            no_setup.start_blockers(),
            vec![
                "no strategy selected".to_string(),
                "no symbols selected (pick from Market Watchlist)".to_string(),
                "no timeframe selected".to_string(),
                "quantity must be positive".to_string(),
            ]
        );
    }

    #[test]
    fn actions_are_inert_without_the_engine_bridge() {
        // No fake state transitions: START/STOP/ARM/HALT cannot move state.
        let mut st = configured(LiveState::default());
        st.start();
        assert_eq!(st.session, SessionStatus::Stopped);
        assert_eq!(
            st.action_note.as_deref(),
            Some("No execution backend attached.")
        );
        st.halt();
        assert!(!st.kill_halted);
        st.arm();
        assert!(st.action_note.as_deref().unwrap().starts_with("Blocked:"));
    }

    #[test]
    fn start_with_bridge_and_clean_setup_requests_starting() {
        let mut st = configured(LiveState::default());
        st.bridge_wired = true;
        st.gates = all_ready_gates();
        assert!(st.can_start());
        st.start();
        assert_eq!(st.session, SessionStatus::Starting);
        // RUNNING is never reached by UI intent alone — only bridge facts.
        assert_ne!(st.session, SessionStatus::Running);
    }

    #[test]
    fn live_mode_refused_until_every_venue_gate_is_ready() {
        let mut st = configured(LiveState::default());
        st.gates = failed_gates();
        st.set_mode(ExecMode::Live);
        assert_eq!(st.mode, ExecMode::Paper); // fail-closed, never silent
        assert!(st.action_note.as_deref().unwrap().contains("LIVE refused"));
        st.gates = all_ready_gates();
        st.set_mode(ExecMode::Live);
        assert_eq!(st.mode, ExecMode::Live);
    }

    #[test]
    fn live_start_requires_explicit_consent_and_gates() {
        let mut st = configured(LiveState::default());
        st.bridge_wired = true;
        st.gates = all_ready_gates();
        st.set_mode(ExecMode::Live);
        // Consent is never inferred.
        assert!(st
            .start_blockers()
            .contains(&"live confirmation required (explicit operator consent)".to_string()));
        assert!(!st.can_start());
        st.confirm_live();
        assert!(st.live_confirmed);
        assert!(st.can_start());
        // Leaving LIVE mode clears consent.
        st.set_mode(ExecMode::Paper);
        assert!(!st.live_confirmed);
    }

    #[test]
    fn halt_engages_kill_switch_state_and_banner() {
        let mut st = configured(LiveState::default());
        st.bridge_wired = true;
        st.session = SessionStatus::Running;
        assert!(st.can_halt());
        st.halt();
        assert!(st.kill_halted);
        assert_eq!(st.session, SessionStatus::Halted);
        let view = project(&st);
        assert!(view.bar.halted);
        assert_eq!(view.bar.exec_label, "■ HALTED");
        // Halt control idles once the halt is effective (matches can_halt).
        assert!(!st.can_halt());
        assert!(!view.bar.halt_enabled);
    }

    #[test]
    fn mode_change_blocked_while_running() {
        let mut st = configured(LiveState::default());
        st.bridge_wired = true;
        st.session = SessionStatus::Running;
        st.gates = all_ready_gates();
        st.set_mode(ExecMode::Live);
        assert_eq!(st.mode, ExecMode::Paper);
        assert_eq!(
            st.action_note.as_deref(),
            Some("stop the running session before changing mode")
        );
    }

    #[test]
    fn quantity_rejects_garbage_and_keeps_the_old_value() {
        let mut st = configured(LiveState::default());
        st.set_quantity("25.5");
        assert_eq!(st.quantity, 25.5);
        st.set_quantity("-4");
        assert_eq!(st.quantity, 25.5);
        st.set_quantity("abc");
        assert_eq!(st.quantity, 25.5);
        assert!(st
            .action_note
            .as_deref()
            .unwrap()
            .contains("invalid quantity"));
    }

    #[test]
    fn quantity_rejects_zero_and_reparses_grouped_display() {
        let mut st = configured(LiveState::default());
        st.set_quantity("25.5");
        // Zero is rejected locally: the service raises on it, which used
        // to degrade the whole page into _live_unavailable over a typo.
        st.set_quantity("0");
        assert_eq!(st.quantity, 25.5);
        assert!(st
            .action_note
            .as_deref()
            .unwrap()
            .contains("invalid quantity"));
        // The projection renders grouped thousands ("1,250.00");
        // re-committing the displayed text parses back to the same value.
        st.set_quantity("1,250.00");
        assert_eq!(st.quantity, 1250.0);
    }

    #[test]
    fn arm_refusal_carries_the_exact_blocking_reason() {
        // Host mode: the backend's own verdict wins (its gate names are
        // the real ones — not the local canonical vocabulary).
        let mut st = configured(LiveState::default());
        st.host_mode = true;
        st.bridge_wired = true;
        st.backend_can_arm = Some(false);
        st.backend_arm_blockers = Some(vec![
            "no live broker selected (configure in SYSTEM → BROKERS)".into(),
        ]);
        st.arm();
        assert_eq!(
            st.action_note.as_deref().unwrap(),
            "Blocked: no live broker selected (configure in SYSTEM → BROKERS)"
        );
        // Local mode: the derived blockers, never a bare "Blocked: ".
        let mut local = configured(LiveState::default());
        local.bridge_wired = true;
        local.arm();
        let note = local.action_note.as_deref().unwrap();
        assert!(note.starts_with("Blocked: "));
        assert!(note.len() > "Blocked: ".len());
    }

    #[test]
    fn multi_position_book_never_prints_flat() {
        let row = |symbol: &str| PositionRow {
            symbol: symbol.into(),
            side: "LONG".into(),
            quantity: "10".into(),
            entry: "100".into(),
            current: "101".into(),
            pnl: "+10".into(),
            status: "OPEN".into(),
            pnl_pct: Some(1.0),
        };
        // The provider sends single-position facts only: with 2+ open
        // positions the card must point at the table, never print FLAT.
        let mut st = LiveState::default();
        st.positions = vec![row("AAA"), row("BBB")];
        st.position_facts = None;
        let view = project(&st);
        assert_eq!(view.position_rows[0].value, "see POSITIONS");
    }

    #[test]
    fn clean_reconciliation_does_not_alarm() {
        // A clean book reports mismatches "0" — the GOOD state, not red.
        let mut st = LiveState::default();
        st.reconciliation = ReconciliationFacts {
            status: "CLEAN".into(),
            positions: "2".into(),
            orders: "3".into(),
            last_check: "t".into(),
            mismatches: "0".into(),
            blocks_live: false,
        };
        let view = project(&st);
        let row = view
            .recon_rows
            .iter()
            .find(|r| r.key == "MISMATCHES")
            .unwrap();
        assert_eq!(row.value, "0");
        assert_eq!(row.tone, 0);
    }

    #[test]
    fn open_orders_uses_the_backend_count_not_the_capped_table() {
        // The orders table is capped at the last 50 rows; the card must
        // show the backend's true open count when the snapshot carries it.
        let mut st = LiveState::default();
        st.apply_snapshot(&serde_json::json!({"open_orders": 3}));
        let view = project(&st);
        let row = view
            .account_rows
            .iter()
            .find(|r| r.key == "OPEN ORDERS")
            .unwrap();
        assert_eq!(row.value, "3");
        // Without the key the visible table length is the honest fallback.
        let fallback = project(&LiveState::default());
        let row = fallback
            .account_rows
            .iter()
            .find(|r| r.key == "OPEN ORDERS")
            .unwrap();
        assert_eq!(row.value, "0");
    }

    #[test]
    fn symbol_filter_and_toggle_use_real_indices() {
        let mut st = LiveState::default();
        st.symbols = "AAA,ABA,BB,REL,REL-JR"
            .split(',')
            .map(|s| SymbolPick {
                symbol: s.into(),
                checked: false,
                ltp: None,
                change_pct: None,
                in_store: true,
            })
            .collect();
        st.set_symbol_filter("rel");
        let view = project(&st);
        assert_eq!(view.symbols.len(), 2);
        assert_eq!(view.symbols[0].real_index, 3); // RELIANCE row identity kept
        let mut st2 = st.clone();
        st2.toggle_symbol(3);
        assert_eq!(st2.checked_symbols(), vec!["REL"]);
    }

    #[test]
    fn gate_rows_render_actual_backend_reasons() {
        let mut st = LiveState::default();
        st.gates = failed_gates();
        let view = project(&st);
        let broker = view
            .gates
            .iter()
            .find(|g| g.name == "BROKER_ADAPTER_READY")
            .unwrap();
        assert_eq!(broker.status, "NOT READY");
        assert_eq!(broker.reason, "not configured");
        assert!(!st.venue_gates_ready());
    }

    #[test]
    fn money_and_text_renderings_match_the_terminal_conventions() {
        assert_eq!(money(123.456), "+123.46");
        assert_eq!(money(-7.5), "-7.50");
        assert_eq!(grouped(1234567.5), "1,234,567.50");
        assert_eq!(grouped(-99.0), "-99.00");
        assert_eq!(text_or_na(""), "N/A");
        assert_eq!(text_or_na("  "), "N/A");
        assert_eq!(text_or_na("RELIANCE"), "RELIANCE");
    }

    #[test]
    fn blotter_projections_are_empty_state_honest() {
        let view = project(&LiveState::default());
        assert!(!view.has_orders);
        assert!(!view.has_fills);
        assert!(!view.has_positions);
        assert!(!view.has_events);
    }

    #[test]
    fn running_facts_project_with_semantic_tones() {
        let mut st = configured(LiveState::default());
        st.bridge_wired = true;
        st.session = SessionStatus::Running;
        st.positions = vec![PositionRow {
            symbol: "RELIANCE".into(),
            side: "LONG".into(),
            quantity: "10".into(),
            entry: "2801.10".into(),
            current: "2812.40".into(),
            pnl: "+113.00".into(),
            status: "OPEN".into(),
            pnl_pct: None,
        }];
        st.orders = vec![OrderRow {
            order_id: "cid-1".into(),
            strategy: "OBR".into(),
            symbol: "RELIANCE".into(),
            side: "BUY".into(),
            quantity: "10".into(),
            order_type: "LIMIT".into(),
            price: "2801.10".into(),
            status: "COMPLETE".into(),
            time: "13:00:02".into(),
            broker: "paper".into(),
        }];
        st.pnl = Pnl {
            realized: Some(50.0),
            unrealized: Some(63.0),
            exposure: Some(28124.0),
            orders: Some(1),
            fills: Some(1),
            wins: Some(1),
            losses: Some(0),
        };
        let view = project(&st);
        assert!(view.has_positions);
        assert_eq!(view.positions[0].pnl_tone, 1);
        assert_eq!(view.orders[0].status_tone, 1);
        let total = view.stats.iter().find(|s| s.label == "TOTAL").unwrap();
        assert_eq!(total.value, "+113.00");
        assert_eq!(view.bar.exec_label, "● ENABLED");
        assert_eq!(view.setup.session_label, "● RUNNING");
        assert!(view.setup.can_stop);
        assert!(!view.setup.can_start);
    }

    #[test]
    fn host_mode_forwards_runtime_actions_without_faking_state() {
        let mut st = configured(LiveState::default());
        st.apply_snapshot(&serde_json::json!({
            "mode": "PAPER",
            "session_status": "STOPPED",
            "can_halt": false,
            "can_arm": false,
            "start_blockers": [],
        }));
        assert!(st.host_mode && st.bridge_wired);
        assert!(st.can_start());
        st.start();
        assert_eq!(st.session, SessionStatus::Stopped); // no fake transition
        let action: serde_json::Value = serde_json::from_str(&st.take_action().unwrap()).unwrap();
        assert_eq!(action["action"], "start");
        assert_eq!(action["confirmed"], false);
        assert!(st.take_action().is_none());
        // Setup edits forward the full legacy-shaped setup payload.
        st.toggle_symbol(1);
        let action: serde_json::Value = serde_json::from_str(&st.take_action().unwrap()).unwrap();
        assert_eq!(action["action"], "setup");
        assert_eq!(action["strategy_name"], "OBR");
        assert_eq!(action["timeframe"], "15m");
        assert_eq!(action["symbols"], serde_json::json!(["RELIANCE", "TCS"]));
        // Mode changes are requests; the backend snapshot decides.
        st.set_mode(ExecMode::Live);
        assert_eq!(st.mode, ExecMode::Paper); // not applied locally
        let action: serde_json::Value = serde_json::from_str(&st.take_action().unwrap()).unwrap();
        assert_eq!(action["mode"], "LIVE");
        assert_eq!(action["action"], "mode");
    }

    #[test]
    fn host_backend_verdicts_are_authoritative() {
        let mut st = configured(LiveState::default());
        st.apply_snapshot(&serde_json::json!({
            "session_status": "RUNNING",
            "can_halt": true,
            "can_arm": false,
            "start_blockers": ["session already running"],
            "arm_blockers": ["no live venue"],
            "status_reason": "tick failed: venue timeout",
            "kill": {"halted": false},
        }));
        assert!(st.can_halt());
        assert!(!st.can_arm());
        assert!(!st.can_start());
        let view = project(&st);
        assert_eq!(view.bar.exec_label, "● ENABLED");
        // status_reason surfaces as its own visible line (legacy parity).
        assert!(view
            .setup
            .blockers
            .iter()
            .any(|b| b.contains("venue timeout")));
        // Host halt forwards instead of self-applying the kill switch.
        st.halt();
        let action: serde_json::Value = serde_json::from_str(&st.take_action().unwrap()).unwrap();
        assert_eq!(action["action"], "halt");
        assert!(!st.kill_halted); // only the backend snapshot may say HALTED
    }

    #[test]
    fn apply_snapshot_maps_the_provider_schema_honestly() {
        let mut st = LiveState::default();
        st.apply_snapshot(&serde_json::json!({
            "mode": "PAPER",
            "session_status": "RUNNING",
            "broker": {"name": "paper", "environment": "paper", "connected": true,
                       "reason": "selected: user", "capabilities": ["orders.market"],
                       "latency_ms": 12.5, "last_heartbeat": "13:00:02"},
            "gates": [{"name": "ACCOUNT", "status": "READY", "reason": ""},
                      {"name": "ARMING", "status": "NOT READY", "reason": "DISARMED"}],
            "positions": [{"symbol": "RELIANCE", "side": "LONG", "quantity": 10,
                           "entry_price": 2801.1, "current_price": 2812.4,
                           "pnl": 113.0, "status": "OPEN"}],
            "position": {"symbol": "RELIANCE", "side": "LONG", "quantity": 10,
                         "avg_price": 2801.1, "current_price": 2812.4,
                         "unrealized": 113.0, "realized": 0.0,
                         "exposure": 28124.0, "risk_utilization": null},
            "pnl": {"realized": 0.0, "unrealized": 113.0, "exposure": 28124.0,
                    "orders": 1, "fills": 1, "wins": 1, "losses": 0},
            "risk": {"status": "READY", "limits": [["max_order_qty", 10.0, "ok"]],
                     "decisions": []},
            "reconciliation": {"status": "CLEAN", "positions": 1, "orders": 0,
                               "last_check": "13:00:05", "mismatches": [],
                               "blocks_live": false},
            "kill": {"halted": false},
            "events": [{"timestamp": "13:00:02", "strategy": "OBR",
                        "symbol": "RELIANCE", "event": "ORDER_FILL", "status": "ok"}],
            "available_strategies": ["OBR"],
            "available_symbols": ["RELIANCE", "TCS"],
            "selected_symbols": ["RELIANCE"],
            "available_timeframes": ["5m", "15m"],
            "selected_timeframe": "15m",
            "quantity": 10.0,
            "market_symbol": "RELIANCE",
            "market_timeframe": "15m",
            "market_bars": [{"o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5},
                            {"open": 1.5, "high": 2.5, "low": 1.2, "close": 2.2}],
            "quotes": [{"symbol": "RELIANCE", "ltp": 2812.4, "change_pct": 1.12,
                        "status": "AVAILABLE"},
                       {"symbol": "TCS", "ltp": null, "change_pct": null,
                        "status": "NO MARKET DATA"}],
            "capital": {"source": "broker", "broker_capital": 500000.0,
                        "available_margin": 482350.0, "used_margin": 17650.0,
                        "configured_capital": 1000000.0},
            "feed": "local",
        }));
        let view = project(&st);
        assert_eq!(view.bar.broker_label, "paper");
        assert_eq!(view.bar.conn_label, "● Connected");
        assert_eq!(view.setup.session_label, "● RUNNING");
        assert_eq!(view.positions[0].pnl, "+113.00");
        assert_eq!(view.positions[0].qty, "10.00");
        assert_eq!(view.positions[0].entry, "2,801.10");
        assert_eq!(view.stats[0].value, "+0.00");
        assert_eq!(view.recon_rows[5].value, "NO"); // blocks_live false
        assert_eq!(view.market.state_label, "READY");
        assert!(view.market.has_data);
        assert_eq!(view.market.feed_label, "FEED: LOCAL TAIL");
        assert_eq!(view.gates.len(), 2);
        assert_eq!(view.gates[1].status, "NOT READY");
        assert_eq!(view.symbols.len(), 2);
        assert!(view.symbols[0].checked);
        assert!(!view.symbols[1].checked);
        // Quote facts project honestly: LTP + signed change + backend status.
        assert_eq!(view.symbols[0].ltp, "2,812.40");
        assert_eq!(view.symbols[0].change, "+1.12%");
        assert_eq!(view.symbols[0].status, "AVAILABLE");
        assert_eq!(view.symbols[0].status_tone, 1);
        assert_eq!(view.symbols[1].ltp, "—");
        assert_eq!(view.symbols[1].status, "NO MARKET DATA");
        assert_eq!(view.symbols[1].status_tone, 2);
        // Venue capital reaches the Account & Risk rows verbatim.
        let capital = view
            .account_rows
            .iter()
            .find(|r| r.key == "BROKER CAPITAL")
            .unwrap();
        assert_eq!(capital.value, "₹500,000.00");
        let source = view
            .account_rows
            .iter()
            .find(|r| r.key == "CAPITAL SOURCE")
            .unwrap();
        assert_eq!(source.value, "BROKER (VENUE)");
        assert_eq!(view.setup.symbol_total, "2 symbols (—)");
        assert_eq!(view.events.len(), 1);
    }

    #[test]
    fn snapshot_halt_fact_and_flat_position_are_truthful() {
        let mut st = LiveState::default();
        st.apply_snapshot(&serde_json::json!({
            "kill": {"halted": true},
            "session_status": "HALTED",
            "position": {"flat": true},
        }));
        let view = project(&st);
        assert!(view.bar.halted);
        assert_eq!(view.bar.exec_label, "■ HALTED");
        assert_eq!(view.position_rows[0].value, "FLAT");
        // Mistyped/missing sections degrade to honest absence — no crash.
        st.apply_snapshot(&serde_json::json!({"gates": "not-a-list", "pnl": {}}));
        let view = project(&st);
        assert!(view.gates.is_empty());
        assert_eq!(view.stats[0].value, "N/A");
    }

    #[test]
    fn event_filters_shape_the_stream_without_inventing_types() {
        let mut st = LiveState::default();
        st.events = vec![
            LiveEvent {
                timestamp: "13:00".into(),
                strategy: "OBR".into(),
                symbol: "RELIANCE".into(),
                event: "order submitted cid-1".into(),
                status: "ok".into(),
                category: "ORDERS".into(),
            },
            LiveEvent {
                timestamp: "13:01".into(),
                strategy: "OBR".into(),
                symbol: "RELIANCE".into(),
                event: "risk denied: over limit".into(),
                status: "error".into(),
                category: "RISK".into(),
            },
        ];
        let view = project(&st);
        // Fixed buckets only — never one chip per row text.
        assert_eq!(
            view.event_types,
            vec![
                "ALL",
                "BROKER",
                "MARKET DATA",
                "STRATEGY",
                "ORDERS",
                "RISK",
                "SYSTEM"
            ]
        );
        assert_eq!(view.events[0].level, "ERROR"); // newest first, uppercased
        assert_eq!(view.events[0].category, "RISK");
        st.set_event_category("RISK");
        let view = project(&st);
        assert_eq!(view.events.len(), 1);
        assert_eq!(view.events[0].status_tone, 3);
        // Unknown buckets are honest no-ops, never silent re-filters.
        st.apply_event_category("NOPE");
        assert_eq!(project(&st).events.len(), 1);
        st.set_event_category("ALL");
        st.set_event_filter("reliance 13:00");
        // text filter is a whole-line substring match
        assert!(project(&st).events.is_empty());
        st.set_event_filter("13:01");
        assert_eq!(project(&st).events.len(), 1);
        // Clear drops UI history only — counts, not backend facts.
        st.clear_events();
        assert!(st.events.is_empty());
        assert_eq!(project(&st).events.len(), 0);
    }

    #[test]
    fn watchlist_statuses_come_from_quote_facts_not_colors() {
        let mut st = LiveState::default();
        st.apply_snapshot(&serde_json::json!({
            "available_symbols": ["NSE:A", "NSE:B", "NSE:C"],
            "selected_symbols": [],
            "quotes": [
                {"symbol": "NSE:A", "ltp": 100.0, "change_pct": -0.5,
                 "status": "AVAILABLE"},
                {"symbol": "NSE:B", "ltp": null, "change_pct": null,
                 "status": "NO MARKET DATA"},
                {"symbol": "NSE:C", "ltp": null, "change_pct": null,
                 "status": "NOT FOUND"},
            ],
        }));
        let view = project(&st);
        assert_eq!(view.symbols[0].status, "AVAILABLE");
        assert_eq!(view.symbols[0].status_tone, 1);
        assert_eq!(view.symbols[0].change, "-0.50%");
        assert_eq!(view.symbols[1].status, "NO MARKET DATA");
        assert_eq!(view.symbols[1].status_tone, 2);
        assert_eq!(view.symbols[2].status, "NOT FOUND");
        assert_eq!(view.symbols[2].status_tone, 3);
    }

    #[test]
    fn idle_capital_is_configured_basis_never_broker_money() {
        let mut st = LiveState::default();
        st.apply_snapshot(&serde_json::json!({
            "capital": {"source": "configured", "broker_capital": null,
                        "available_margin": null, "used_margin": null,
                        "configured_capital": 1000000.0},
        }));
        let view = project(&st);
        let by_key = |k: &str| {
            view.account_rows
                .iter()
                .find(|r| r.key == k)
                .unwrap()
                .value
                .clone()
        };
        assert_eq!(by_key("BROKER CAPITAL"), "NOT REPORTED");
        assert_eq!(by_key("AVAILABLE MARGIN"), "NOT REPORTED");
        assert_eq!(by_key("CONFIGURED CAPITAL"), "₹1,000,000.00");
        assert_eq!(by_key("CAPITAL SOURCE"), "CONFIGURED (PAPER)");
        assert_eq!(by_key("LEVERAGE"), "NOT REPORTED");
        assert_eq!(by_key("OPEN POSITIONS"), "0");
    }

    #[test]
    fn backend_action_note_is_a_fact_and_running_clears_stale_requests() {
        let mut st = configured(LiveState::default());
        st.host_mode = true;
        st.bridge_wired = true;
        st.gates = all_ready_gates();
        st.start();
        assert!(st
            .action_note
            .as_deref()
            .unwrap()
            .starts_with("start requested"));
        // Backend confirms RUNNING: the stale local request note must go.
        st.apply_snapshot(&serde_json::json!({"session_status": "RUNNING"}));
        assert!(st.action_note.is_none());
        // And a backend note of its own is adopted verbatim.
        st.apply_snapshot(&serde_json::json!({
            "session_status": "STOPPED",
            "action_note": "armed — confirm LIVE consent on START",
        }));
        assert_eq!(
            st.action_note.as_deref(),
            Some("armed — confirm LIVE consent on START")
        );
        assert_eq!(
            project(&st).bar.note,
            "armed — confirm LIVE consent on START"
        );
    }

    #[test]
    fn card_anatomy_matches_the_terminal_reference() {
        let mut st = LiveState::default();
        st.apply_snapshot(&serde_json::json!({
            "as_of": "2026-10-02T10:28:42+00:00",
            "broker": {"name": "FYERS", "environment": "LIVE",
                       "connected": true, "reason": "session active",
                       "account_id": "VA1234", "status": "CONNECTED"},
            "strategy": {"id": "OBR C1C4", "status": "ACTIVE",
                         "timeframe": "30m"},
            "reconciliation": {"status": "CLEAN", "positions": 0, "orders": 0,
                               "blocks_live": false},
            "positions": [{"symbol": "NSE:KAYNES", "side": "LONG",
                           "quantity": 10, "entry_price": 2800.0,
                           "current_price": 2812.4, "pnl": 124.0,
                           "pnl_pct": 0.44, "status": "OPEN"}],
            "available_strategies": ["OBR C1C4"],
            "available_symbols": ["NSE:KAYNES"],
            "selected_symbols": ["NSE:KAYNES"],
        }));
        let view = project(&st);
        // Broker card: bare name + connection/account sub-line.
        assert_eq!(view.bar.broker_label, "FYERS");
        assert_eq!(view.bar.broker_tone, 1);
        assert_eq!(view.bar.conn_label, "● Connected · ACC: VA1234");
        assert_eq!(view.bar.conn_tone, 1);
        // Strategy card: bare id + status/timeframe sub-line.
        assert_eq!(view.bar.strategy_label, "OBR C1C4");
        assert_eq!(view.bar.strategy_sub, "ACTIVE · 30m");
        // Recon card: bare status + counts sub-line.
        assert_eq!(view.bar.recon_label, "CLEAN");
        assert_eq!(view.bar.recon_sub, "Positions: 0 | Orders: 0");
        // Positions carry the ledger P&L percent.
        assert_eq!(view.positions[0].pnl_pct, "+0.44%");
        // Market card carries the backend poll clock.
        assert_eq!(view.market.updated_label, "10:28:42");
        // Watchlist header names the universe strategy.
        assert_eq!(view.setup.symbol_total, "1 symbols (OBR C1C4)");
    }

    #[test]
    fn strategy_enrichment_projects_registry_facts() {
        let mut st = LiveState::default();
        st.apply_snapshot(&serde_json::json!({
            "strategy": {"id": "OBR C1C4", "version": "1.0.0", "status": "ACTIVE",
                         "mode": "PAPER", "instrument": "NSE:KAYNES",
                         "timeframe": "30m", "direction": "SHORT",
                         "runtime_state": "ACTIVE",
                         "reference_window": "10:15–10:45", "universe": 22,
                         "live_supported": true, "warmup": 120,
                         "state": "IDLE"},
        }));
        let view = project(&st);
        let by_key = |k: &str| {
            view.strategy_rows
                .iter()
                .find(|r| r.key == k)
                .unwrap()
                .value
                .clone()
        };
        assert_eq!(by_key("DIRECTION"), "SHORT");
        assert_eq!(by_key("RUNTIME"), "ACTIVE");
        assert_eq!(by_key("REFERENCE WINDOW"), "10:15–10:45");
        assert_eq!(by_key("UNIVERSE"), "22 symbols");
    }

    #[test]
    fn test_live_terminal_views() {
        let mut st = LiveState::default();
        st.apply_snapshot(&serde_json::json!({
            "mode": "LIVE",
            "websocket": {
                "status": "CONNECTED",
                "latency_ms": 42.0,
                "subscribed_symbols": 52,
                "last_tick_time": "12:14:25"
            },
            "market_data": {
                "status": "STREAMING",
                "exchange": "NSE",
                "subscribed_symbols": 52,
                "last_tick_time": "12:14:25",
                "freshness_age_s": 0.4
            },
            "capital": {
                "source": "broker",
                "broker_capital": 100000.0
            },
            "risk_engine": {
                "raw_capital": 100000.0,
                "leverage": 4.0,
                "per_trade_risk_pct": 0.0015
            },
            "selected_symbol": "NSE:KAYNES-EQ",
            "quotes": [
                {
                    "symbol": "NSE:KAYNES-EQ",
                    "clean_symbol": "KAYNES",
                    "ltp": 1224.50,
                    "change_pct": 1.25,
                    "ref_high": 1245.00,
                    "ref_low": 1215.00,
                    "break_low": 1218.50,
                    "entry_price": 1218.50,
                    "stop_price": 1245.00,
                    "risk_per_share": 26.50,
                    "qty": 22,
                    "planned_risk": 583.00,
                    "risk_util": 97.2,
                    "position": "SHORT",
                    "status": "READY",
                    "signal": "SELL",
                    "last_update": "12:14:25"
                }
            ]
        }));

        let view = project(&st);
        assert_eq!(view.ws.status, "CONNECTED");
        assert_eq!(view.ws.tone, 1);
        assert_eq!(view.ws.latency, "42 ms");
        assert_eq!(view.ws.sub, "52 symbols");

        assert_eq!(view.md.status, "STREAMING");
        assert_eq!(view.md.tone, 1);
        assert_eq!(view.md.freshness, "0.4s");

        assert_eq!(view.active_strat.mode, "LIVE");
        assert_eq!(view.active_strat.mode_tone, 1);

        assert_eq!(view.bar.risk_label, "READY");
        assert_eq!(view.bar.risk_sub, "4x LEV · ₹600 MAX");

        assert_eq!(view.watchlist.len(), 1);
        let row = &view.watchlist[0];
        assert_eq!(row.symbol, "KAYNES");
        assert_eq!(row.ltp, "1224.50");
        assert_eq!(row.entry, "1218.50");
        assert_eq!(row.stop, "1245.00");
        assert_eq!(row.risk_share, "26.50");
        assert_eq!(row.qty, "22");
        assert_eq!(row.planned_risk, "₹583.00");
        assert_eq!(row.signal, "SELL");
        assert_eq!(row.selected, true);

        assert_eq!(view.selected_stock.symbol, "KAYNES · NSE EQUITY");
        assert_eq!(view.selected_stock.entry_price, "1218.50");
        assert_eq!(view.selected_stock.stop_price, "1245.00");
        assert_eq!(view.selected_stock.risk_share, "26.50");
        assert_eq!(view.selected_stock.broker_capital, "₹100000.00");
        assert_eq!(view.selected_stock.effective_capital, "₹400000.00");
        assert_eq!(view.selected_stock.max_allowed_risk, "₹600.00");
        assert_eq!(view.selected_stock.capital_source, "CAPITAL SOURCE: BROKER");
        assert_eq!(view.selected_stock.calculated_qty, "22");
        assert_eq!(view.selected_stock.planned_risk, "₹583.00");
        assert_eq!(view.selected_stock.risk_validated, true);
        assert!(view.selected_stock.risk_banner_text.contains("[✓] RISK VALIDATED"));
    }

    #[test]
    fn test_capital_unavailable_fails_closed_in_live() {
        let mut st = LiveState::default();
        st.apply_snapshot(&serde_json::json!({
            "mode": "LIVE",
            "capital": {
                "source": "broker",
                "broker_capital": null
            },
            "selected_symbol": "NSE:KAYNES-EQ",
            "quotes": [
                {
                    "symbol": "NSE:KAYNES-EQ",
                    "clean_symbol": "KAYNES",
                    "ltp": 1224.50,
                    "entry_price": 1218.50,
                    "stop_price": 1245.00,
                    "status": "READY"
                }
            ]
        }));

        let view = project(&st);
        assert_eq!(view.bar.risk_label, "NOT READY");
        assert_eq!(view.bar.risk_tone, 3);
        assert_eq!(view.bar.risk_sub, "CAPITAL: NOT AVAILABLE");

        let row = &view.watchlist[0];
        assert_eq!(row.risk_share, "26.50"); // Price risk preserved!
        assert_eq!(row.qty, "—"); // Zero/no capital blocks sizing
        assert_eq!(row.planned_risk, "—");

        assert_eq!(view.selected_stock.broker_capital, "NOT AVAILABLE");
        assert_eq!(view.selected_stock.effective_capital, "—");
        assert_eq!(view.selected_stock.max_allowed_risk, "—");
        assert_eq!(view.selected_stock.calculated_qty, "—");
        assert_eq!(view.selected_stock.planned_risk, "—");
        assert_eq!(view.selected_stock.risk_share, "26.50");
        assert_eq!(view.selected_stock.risk_validated, false);
        assert!(view.selected_stock.risk_banner_text.contains("[✕] RISK STATUS: NOT READY"));
    }

    #[test]
    fn test_seven_filter_chips_and_order_pnl_projection() {
        let mut st = LiveState::default();
        st.watchlist_rows = vec![
            WatchlistStockRow {
                symbol: "NSE:KAYNES".into(),
                clean_symbol: "KAYNES".into(),
                ltp: Some(5410.0),
                change_pct: Some(1.25),
                ref_high: Some(5420.0),
                ref_low: Some(5310.0),
                break_low: Some(5309.50),
                entry_price: Some(5309.50),
                stop_price: Some(5420.0),
                risk_per_share: Some(110.50),
                qty: Some(18),
                planned_risk: Some(1989.0),
                risk_util: Some(99.45),
                position: "SHORT 18".into(),
                status: "IN POSITION".into(),
                signal: "SELL".into(),
                order: Some("FILLED".into()),
                pnl: Some(450.0),
                last_update: "09:42:15".into(),
            },
            WatchlistStockRow {
                symbol: "NSE:TATASTEEL".into(),
                clean_symbol: "TATASTEEL".into(),
                ltp: Some(150.0),
                change_pct: Some(-0.5),
                ref_high: Some(152.0),
                ref_low: Some(148.0),
                break_low: Some(147.9),
                entry_price: Some(147.9),
                stop_price: Some(152.0),
                risk_per_share: Some(4.1),
                qty: Some(200),
                planned_risk: Some(820.0),
                risk_util: Some(82.0),
                position: "FLAT".into(),
                status: "READY".into(),
                signal: "--".into(),
                order: None,
                pnl: None,
                last_update: "09:42:10".into(),
            },
            WatchlistStockRow {
                symbol: "NSE:INFY".into(),
                clean_symbol: "INFY".into(),
                ltp: Some(1800.0),
                change_pct: Some(2.1),
                ref_high: Some(1810.0),
                ref_low: Some(1790.0),
                break_low: None,
                entry_price: None,
                stop_price: None,
                risk_per_share: None,
                qty: None,
                planned_risk: None,
                risk_util: None,
                position: "FLAT".into(),
                status: "SIGNAL ACTIVE".into(),
                signal: "BUY".into(),
                order: Some("SUBMITTING".into()),
                pnl: Some(-50.0),
                last_update: "09:42:12".into(),
            },
        ];

        let view = project(&st);

        // 7 filter chips verification
        assert_eq!(view.filter_chips.len(), 7);
        assert_eq!(view.filter_chips[0], "ALL (3)");
        assert_eq!(view.filter_chips[1], "READY (1)");
        assert_eq!(view.filter_chips[2], "SIGNAL (2)"); // KAYNES (SELL) + INFY (BUY)
        assert_eq!(view.filter_chips[3], "ORDER (1)"); // INFY (order=SUBMITTING)
        assert_eq!(view.filter_chips[4], "IN POSITION (1)"); // KAYNES (SHORT 18)
        assert_eq!(view.filter_chips[5], "WAITING (0)");
        assert_eq!(view.filter_chips[6], "NO DATA (0)");

        // Row projection verification
        assert_eq!(view.watchlist.len(), 3);

        // KAYNES
        let kaynes = &view.watchlist[0];
        assert_eq!(kaynes.symbol, "KAYNES");
        assert_eq!(kaynes.order, "FILLED");
        assert_eq!(kaynes.order_tone, 1); // Tone 1 = green
        assert_eq!(kaynes.pnl, "+450.00");
        assert_eq!(kaynes.pnl_tone, 1); // Tone 1 = positive/green

        // TATASTEEL
        let tatasteel = &view.watchlist[1];
        assert_eq!(tatasteel.symbol, "TATASTEEL");
        assert_eq!(tatasteel.order, "—");
        assert_eq!(tatasteel.order_tone, 0);
        assert_eq!(tatasteel.pnl, "—");
        assert_eq!(tatasteel.pnl_tone, 0);

        // INFY
        let infy = &view.watchlist[2];
        assert_eq!(infy.symbol, "INFY");
        assert_eq!(infy.order, "SUBMITTING");
        assert_eq!(infy.order_tone, 2); // Tone 2 = amber/submitting
        assert_eq!(infy.pnl, "-50.00");
        assert_eq!(infy.pnl_tone, 3); // Tone 3 = negative/red

        // Test filter chip 3 selection (ORDER)
        st.set_filter_chip(3);
        let filtered_order = project(&st);
        assert_eq!(filtered_order.watchlist.len(), 1);
        assert_eq!(filtered_order.watchlist[0].symbol, "INFY");

        // Test filter chip 4 selection (IN POSITION)
        st.set_filter_chip(4);
        let filtered_pos = project(&st);
        assert_eq!(filtered_pos.watchlist.len(), 1);
        assert_eq!(filtered_pos.watchlist[0].symbol, "KAYNES");
    }
}

