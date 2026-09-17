//! Live execution workspace native view-model — pure, headless-testable UI
//! state (constitution §3 / UI_DESIGN_SYSTEM.md §12: Rust owns view-model +
//! interaction state; Slint renders bound properties only).
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

/// Market-data state for the chart region (UI_DESIGN_SYSTEM.md §7 states).
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
}

impl RiskStatus {
    pub fn label(self) -> &'static str {
        match self {
            RiskStatus::Ready => "READY",
            RiskStatus::Warning => "WARNING",
            RiskStatus::Blocked => "BLOCKED",
            RiskStatus::Halted => "HALTED",
        }
    }
    pub fn badge(self) -> i32 {
        match self {
            RiskStatus::Ready => 1,
            RiskStatus::Warning => 2,
            RiskStatus::Blocked | RiskStatus::Halted => 3,
        }
    }
    pub fn from_str(value: &str) -> Self {
        match value {
            "WARNING" => RiskStatus::Warning,
            "BLOCKED" => RiskStatus::Blocked,
            "HALTED" => RiskStatus::Halted,
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
#[derive(Debug, Clone, PartialEq)]
pub struct SymbolPick {
    pub symbol: String,
    pub checked: bool,
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
    pub event_type_filter: String,
    pub event_filter: String,

    // Market workspace facts.
    pub market_state: DataState,
    pub market_symbol: String,
    pub market_timeframe: String,
    pub market_last_price: Option<f64>,
    pub market_bar_count: usize,
    pub bars: Vec<Candle>,
    /// True symbol count in the market store (the displayed watchlist may be
    /// a head slice — the count label always states the truth).
    pub store_total: Option<usize>,

    /// Execution bridge present (false until the native bridge is wired —
    /// every mutating control stays honestly inert, same contract as the
    /// Strategy Lab RUN gate).
    pub bridge_wired: bool,
    /// Host-embedded mode: the Qt shell's Slint viewport feeds real backend
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
            event_type_filter: "ALL EVENTS".into(),
            event_filter: String::new(),
            market_state: DataState::NoData,
            market_symbol: String::new(),
            market_timeframe: String::new(),
            market_last_price: None,
            market_bar_count: 0,
            bars: Vec::new(),
            store_total: None,
            bridge_wired: false,
            host_mode: false,
            backend_start_blockers: None,
            backend_arm_blockers: None,
            backend_can_arm: None,
            backend_can_halt: None,
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

    /// Setup + venue validation, mirroring the service `validate()` order.
    /// An unconnected bridge always blocks (no fake enabled states). In host
    /// mode the backend's verdict is authoritative when present (same
    /// contract the Qt workspace consumed: `start_blockers` from the
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

    /// The setup payload mirrors the Qt workspace's `setup_changed` dict:
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
    pub fn set_quantity(&mut self, raw: &str) {
        match raw.trim().parse::<f64>() {
            Ok(v) if v.is_finite() && v >= 0.0 => {
                self.quantity = v;
                self.action_note = None;
                self.push_setup_action();
            }
            _ => self.action_note = Some("invalid quantity — value must be a number ≥ 0".into()),
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
            self.action_note = Some("Blocked: ".into());
        }
    }

    pub fn set_event_type_filter(&mut self, kind: &str) {
        self.event_type_filter = kind.to_string();
    }

    // ── host bridge ingest (the Qt app's live-state provider → here) ─────

    /// Apply one backend snapshot from the real live-state provider
    /// (`bootstrap._live_state_provider` schema — the SAME dict the retained
    /// Qt workspace consumed). Defensive: missing/mistyped keys degrade to
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
                })
                .collect();
        }

        // ── market region ──
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
            self.market_bar_count = bars.len();
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

    /// The event-type ComboBox reports the selected value; accept only
    /// ALL EVENTS or a type that actually appears in the real rows.
    pub fn apply_event_type(&mut self, value: &str) {
        if value == "ALL EVENTS" || self.events.iter().any(|e| e.event == value) {
            self.event_type_filter = value.to_string();
        }
    }

    pub fn set_event_filter(&mut self, filter: &str) {
        self.event_filter = filter.to_lowercase();
    }

    fn visible_events(&self) -> Vec<&LiveEvent> {
        self.events
            .iter()
            .filter(|e| self.event_type_filter == "ALL EVENTS" || e.event == self.event_type_filter)
            .filter(|e| {
                self.event_filter.is_empty()
                    || format!(
                        "{} {} {} {} {}",
                        e.timestamp, e.strategy, e.symbol, e.event, e.status
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
    pub qty: String,
    pub entry: String,
    pub current: String,
    pub pnl: String,
    pub pnl_tone: i32,
    pub status: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct OrderRowView {
    pub order_id: String,
    pub strategy: String,
    pub symbol: String,
    pub side: String,
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
    pub risk_label: String,
    pub risk_tone: i32,
    pub recon_label: String,
    pub recon_tone: i32,
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
}

#[derive(Debug, Clone, PartialEq)]
pub struct CandleView {
    pub x: f32,
    pub open: f32,
    pub high: f32,
    pub low: f32,
    pub close: f32,
    pub up: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct LiveView {
    pub bar: BarView,
    pub setup: SetupView,
    pub market: MarketView,
    pub candles: Vec<CandleView>,
    pub symbols: Vec<SymbolRow>,
    pub symbol_filter: String,
    pub gates: Vec<GateRow>,
    pub arm_note: String,
    pub strategy_rows: Vec<KvRow>,
    pub position_rows: Vec<KvRow>,
    pub risk_rows: Vec<KvRow>,
    pub broker_rows: Vec<KvRow>,
    pub recon_rows: Vec<KvRow>,
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

/// Normalize real bars into 0..1 view coordinates (x = slot center, y with
/// 0 = highest price). A presentation transform only — never new data.
fn project_candles(state: &LiveState) -> Vec<CandleView> {
    let n = state.bars.len();
    if n == 0 {
        return Vec::new();
    }
    let hi = state
        .bars
        .iter()
        .fold(f64::NEG_INFINITY, |m, c| m.max(c.high));
    let lo = state.bars.iter().fold(f64::INFINITY, |m, c| m.min(c.low));
    let span = if (hi - lo) <= f64::EPSILON {
        1.0
    } else {
        hi - lo
    };
    state
        .bars
        .iter()
        .enumerate()
        .map(|(i, c)| CandleView {
            x: ((i as f64 + 0.5) / n as f64) as f32,
            open: ((hi - c.open) / span) as f32,
            high: ((hi - c.high) / span) as f32,
            low: ((hi - c.low) / span) as f32,
            close: ((hi - c.close) / span) as f32,
            up: c.close >= c.open,
        })
        .collect()
}

fn strategy_rows(state: &LiveState) -> Vec<KvRow> {
    let Some(facts) = &state.strategy_facts else {
        return [
            "ID",
            "VERSION",
            "STATUS",
            "MODE",
            "INSTRUMENT",
            "TIMEFRAME",
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
            if state.positions.len() == 1 {
                // Table already carries the row; show its identity once.
                vec![KvRow {
                    key: "POSITION".into(),
                    value: "see POSITIONS".into(),
                    tone: 0,
                }]
            } else {
                vec![KvRow {
                    key: "SIDE".into(),
                    value: "FLAT".into(),
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
            tone: if r.mismatches.trim().is_empty() { 0 } else { 3 },
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

pub fn project(state: &LiveState) -> LiveView {
    // ── command bar ──
    let broker_name = if state.broker.name.trim().is_empty() {
        "NOT CONFIGURED"
    } else {
        state.broker.name.as_str()
    };
    let broker_label = if state.broker.status.is_empty() {
        format!("Broker: {}", broker_name)
    } else {
        format!("Broker: {} — {}", broker_name, state.broker.status)
    };
    let broker_tone = match state.broker.status.as_str() {
        "CONNECTED" | "LIVE_READY" => 1,
        _ if broker_name == "NOT CONFIGURED" || broker_name == "N/A" => 0,
        _ => 4,
    };
    let (conn_label, conn_tone) = match state.broker.connected {
        Some(true) => ("● Connected".into(), 1),
        Some(false) => ("○ Disconnected".into(), 3),
        None => ("Connection: N/A".into(), 0),
    };
    let strategy_id = state
        .strategy_facts
        .as_ref()
        .map(|f| f.id.clone())
        .filter(|id| !id.trim().is_empty())
        .or_else(|| state.selected_strategy().map(str::to_string));
    let strategy_label = format!(
        "Strategy: {}",
        strategy_id.clone().unwrap_or_else(|| "—".into())
    );
    let strategy_tone = if strategy_id.is_some() { 4 } else { 0 };
    let blockers = state.start_blockers();

    let note_tone = match state.action_note.as_deref() {
        Some(n) if n.starts_with("start requested") || n.starts_with("stop requested") => 4,
        Some(n) if n.starts_with("EXECUTION HALTED") => 3,
        Some(n) if n.is_empty() => 0,
        Some(_) => 2,
        None => 0,
    };
    let bar = BarView {
        mode: state.mode.kind(),
        broker_label,
        broker_tone,
        conn_label,
        conn_tone,
        strategy_label,
        strategy_tone,
        risk_label: format!("Risk: {}", state.risk_status.label()),
        risk_tone: state.risk_status.badge(),
        recon_label: format!(
            "Reconciliation: {}",
            text_or_na(&state.reconciliation.status)
        ),
        recon_tone: match state.reconciliation.status.as_str() {
            "CLEAN" => 1,
            "MISMATCH" | "BLOCKED" => 3,
            _ => 0,
        },
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
        symbol_total: format!(
            "{} in store · {} selected",
            state.store_total.unwrap_or(state.symbols.len()),
            state.checked_symbols().len()
        ),
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
            qty: p.quantity.clone(),
            entry: p.entry.clone(),
            current: p.current.clone(),
            pnl: p.pnl.clone(),
            pnl_tone: pnl_tone(&p.pnl),
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
        .map(|(i, s)| SymbolRow {
            real_index: i as i32,
            name: s.symbol.clone(),
            checked: s.checked,
        })
        .collect();

    // ── events (type options from real rows only) ──
    let mut event_types: Vec<String> = state.events.iter().map(|e| e.event.clone()).collect();
    event_types.sort();
    event_types.dedup();
    event_types.insert(0, "ALL EVENTS".into());
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
                "FAIL" | "UNKNOWN" | "REJECTED" => 3,
                _ => 0,
            },
        })
        .collect();

    LiveView {
        bar,
        setup,
        market,
        candles: project_candles(state),
        symbols: visible_symbols,
        symbol_filter: state.symbol_filter.clone(),
        gates,
        arm_note,
        strategy_rows: strategy_rows(state),
        position_rows: position_rows(state),
        risk_rows: risk_rows(state),
        broker_rows: broker_rows(state),
        recon_rows: recon_rows(state),
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
            .position(|t| *t == state.event_type_filter)
            .map_or(0, |i| i as i32),
        event_filter: state.event_filter.clone(),
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
            },
            SymbolPick {
                symbol: "TCS".into(),
                checked: false,
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
        assert_eq!(view.bar.broker_label, "Broker: NOT CONFIGURED");
        assert_eq!(view.bar.conn_label, "Connection: N/A");
        assert_eq!(view.bar.strategy_label, "Strategy: —");
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
    fn symbol_filter_and_toggle_use_real_indices() {
        let mut st = LiveState::default();
        st.symbols = "AAA,ABA,BB,REL,REL-JR"
            .split(',')
            .map(|s| SymbolPick {
                symbol: s.into(),
                checked: false,
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
        // Setup edits forward the full Qt-shaped setup payload.
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
        // status_reason surfaces as its own visible line (Qt parity).
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
        }));
        let view = project(&st);
        assert_eq!(view.bar.broker_label, "Broker: paper");
        assert_eq!(view.bar.conn_label, "● Connected");
        assert_eq!(view.setup.session_label, "● RUNNING");
        assert_eq!(view.positions[0].pnl, "+113.00");
        assert_eq!(view.positions[0].qty, "10.00");
        assert_eq!(view.positions[0].entry, "2,801.10");
        assert_eq!(view.stats[0].value, "+0.00");
        assert_eq!(view.recon_rows[5].value, "NO"); // blocks_live false
        assert_eq!(view.market.state_label, "READY");
        assert!(view.market.has_data);
        assert_eq!(view.candles.len(), 2);
        assert_eq!(view.gates.len(), 2);
        assert_eq!(view.gates[1].status, "NOT READY");
        assert_eq!(view.symbols.len(), 2);
        assert!(view.symbols[0].checked);
        assert!(!view.symbols[1].checked);
        assert_eq!(view.setup.symbol_total, "2 in store · 1 selected");
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
                event: "ORDER_SUBMITTED".into(),
                status: "ok".into(),
            },
            LiveEvent {
                timestamp: "13:01".into(),
                strategy: "OBR".into(),
                symbol: "RELIANCE".into(),
                event: "RISK_DENIED".into(),
                status: "FAIL".into(),
            },
        ];
        let view = project(&st);
        assert_eq!(
            view.event_types,
            vec!["ALL EVENTS", "ORDER_SUBMITTED", "RISK_DENIED"]
        );
        st.set_event_type_filter("RISK_DENIED");
        let view = project(&st);
        assert_eq!(view.events.len(), 1);
        assert_eq!(view.events[0].status_tone, 3);
        st.set_event_type_filter("ALL EVENTS");
        st.set_event_filter("reliance 13:00");
        // text filter is a whole-line substring match
        assert!(project(&st).events.is_empty());
        st.set_event_filter("13:01");
        assert_eq!(project(&st).events.len(), 1);
    }
}
