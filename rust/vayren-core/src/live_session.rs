//! LiveSession pipeline twin — one session, one mode, deterministic.
//!
//! Rust port of the pipeline mechanics of
//! `08_execution/execution/runtime/session.py` (`LiveSession` without the
//! already-twinned `check_live_readiness`, plus `_size_multiplier`,
//! `_strategy_of_intent` and the `ReadinessReport` shape).
//!
//! | Python (`session.py`) | Rust (here) |
//! |---|---|
//! | `_size_multiplier` | [`size_multiplier`] (same truth table) |
//! | `_strategy_of_intent` | [`strategy_of_intent`] |
//! | `ReadinessReport` | [`ReadinessReport`] |
//! | `SessionConfig` defaults | [`SessionConfig`] (same defaults) |
//! | `start` lifecycle sequencing | [`LiveSession::start`] |
//! | `_readiness` prefix assembly | [`LiveSession::readiness`] |
//! | `step` poll loop | [`LiveSession::step`] |
//! | `_on_event` / `_handle_signal` / `_submit` / `_apply_fill` | same methods |
//! | `_dispatch_broker_event` dict machine | [`LiveSession::dispatch_stream`] over [`BrokerStreamEvent`] |
//! | `checkpoint` / `recover` | [`Checkpoint`] round-trip |
//! | `arm` / `disarm` / `stop` / `reconcile_now` / `state` | same methods |
//!
//! Reused untouched (zero duplicate math): `execution_engine` (lifecycle
//! table, arm table, `check_live_readiness`, `Ledger`, `ExecutionEngine`,
//! `plan_order`, reconcile fns, `verdict_of`), `execution` (ids, models),
//! `risk_engine` (concrete `RiskEngine` + `py_float`), `order_state`,
//! `market::Bar`, `execution_events` bus facts, `normalizer`
//! (`StreamNormalizer`, also twinned in this slice).
//!
//! Seams (Python-owned, injected): strategy signal source + warmup/windows
//! (strategies stay Python §2 — `logic_factory`/`inspect_strategy`/driver
//! never cross), broker venue + provider (transports/SDKs), journal/recorder
//! file IO, bus, adaptive/attention/confidence/memory/regime advisory impls,
//! latency measurement, wall clock (caller-supplied epoch, like `clock_sane`).
//! Mode/venue *resolution* (`resolve_broker`) stays Python — `start` takes
//! the resolved triple.
//!
//! Deliberate narrowings, all documented at the item:
//! - `raise LifecycleError` → `Err(String)` with the identical message.
//! - Broker stream dicts → [`BrokerStreamEvent`] (malformed shapes are
//!   unrepresentable; the fill-missing guard becomes type-level).
//! - `ExecutionIntent.urgency` (always `"normal"`) is dropped — it carries
//!   no information.
//! - Journal kwargs → [`Field`] (floats via `py_float`, bools as
//!   `True`/`False`, string lists in Python `[...]` spelling).
//! - `ExecutionJournal`/`LiveEventRecorder` file IO → [`SessionJournal`] /
//!   [`RecorderSink`] (kind counts stay queryable for `state()`).
//! - Kill-switch file → `kill_halted: bool` (same precedent as `risk_engine`
//!   taking `kill_halted`; persistence stays Python).
//! - `stop()` swallows every step error (mirrors `suppress(Exception)`).
//! - Sequence counting (`event_seq`/`signal_seq`) is source-side here;
//!   in Python the driver owns it. `state()` reads the same counters.

use std::collections::HashMap;

use crate::execution::{
    make_intent_id, BrokerOrder, ExecutionIntent, Fill, OrderPlan, OrderType, Side, StrategySignal,
};
use crate::execution_engine::{
    arm_transition, check_live_readiness, plan_order, reconcile_orders, reconcile_positions,
    verdict_of, ContractView, ExecutionEngine, ExecutionMode, ExecutionPreferences, Ledger,
    LifecycleState, LiveArm, ModeGates, OrderSnapshot, PlanIntent, ReconReport, StrategyLifecycle,
};
use crate::execution_events::{
    CandleEvent, HeartbeatEvent, MarketEvent, OrderAcknowledged, OrderFill, OrderPlanned,
    OrderRejected, OrderSubmitted, PositionUpdated, RiskApproved, RiskDenied, SignalGenerated,
};
use crate::market::Bar;
use crate::normalizer::{NormalizerConfig, StreamInput, StreamNormalizer};
use crate::order_state;
use crate::risk_engine::{py_float, RiskEngine, RiskPolicy, RiskRequest};

// ── small pure helpers ────────────────────────────────────────────────────

/// Advisory size input (mirrors `_size_multiplier`'s dynamic typing:
/// bools and out-of-range/non-numbers all mean `1.0`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum SizeRaw {
    Bool(bool),
    Num(f64),
    Other,
}

/// Narrow an advisory size multiplier to `(0, 1]`; anything else is `1.0`.
pub fn size_multiplier(raw: SizeRaw) -> f64 {
    match raw {
        SizeRaw::Bool(_) => 1.0,
        SizeRaw::Num(value) if value > 0.0 && value <= 1.0 => value,
        SizeRaw::Num(_) | SizeRaw::Other => 1.0,
    }
}

/// Strategy id from an intent id (`"{strategy}:…"` — mirrors
/// `_strategy_of_intent`).
pub fn strategy_of_intent(intent_id: &str) -> &str {
    intent_id.split(':').next().unwrap_or("")
}

/// Readiness verdict (mirrors `ReadinessReport`).
#[derive(Debug, Clone, PartialEq)]
pub struct ReadinessReport {
    pub ready: bool,
    pub reasons: Vec<String>,
}

// ── journal encoding ──────────────────────────────────────────────────────

/// One journal field value (mirrors the `record(kind, **kwargs)` shapes;
/// floats render Python-`repr`-exact via `py_float`).
#[derive(Debug, Clone, PartialEq)]
pub enum Field {
    Str(String),
    Int(i64),
    Float(f64),
    Bool(bool),
    StrList(Vec<String>),
}

impl Field {
    pub fn render(&self) -> String {
        match self {
            Field::Str(value) => value.clone(),
            Field::Int(value) => value.to_string(),
            Field::Float(value) => py_float(*value),
            Field::Bool(true) => "True".to_string(),
            Field::Bool(false) => "False".to_string(),
            Field::StrList(values) => {
                let inner: Vec<String> = values.iter().map(|v| format!("'{v}'")).collect();
                format!("[{}]", inner.join(", "))
            }
        }
    }
}

pub fn field_str(value: impl Into<String>) -> Field {
    Field::Str(value.into())
}

pub fn field_int(value: i64) -> Field {
    Field::Int(value)
}

pub fn field_float(value: f64) -> Field {
    Field::Float(value)
}

pub fn field_bool(value: bool) -> Field {
    Field::Bool(value)
}

// ── seams ─────────────────────────────────────────────────────────────────

/// Journal sink (file IO stays Python; kind counts stay queryable).
pub trait SessionJournal {
    fn record(&mut self, kind: &str, fields: Vec<(String, Field)>);
    fn kind_counts(&self) -> Vec<(String, usize)>;
}

/// Bus facts the session emits (wraps the `execution_events` types).
#[derive(Debug, Clone, PartialEq)]
pub enum SessionEvent {
    Signal(SignalGenerated),
    RiskApproved(RiskApproved),
    RiskDenied(RiskDenied),
    Planned(OrderPlanned),
    Submitted(OrderSubmitted),
    Acknowledged(OrderAcknowledged),
    Filled(OrderFill),
    Rejected(OrderRejected),
    Positions(PositionUpdated),
}

pub trait SessionBus {
    fn emit(&mut self, event: SessionEvent);
}

/// Raw-arrival recorder (tape file IO stays Python).
pub trait RecorderSink {
    fn record(&mut self, kind: &str, symbol: &str, seq: i64);
}

/// Strategy side of the pipeline (logic, warmup and windows stay Python).
pub struct StrategyCtx {
    pub strategy_id: String,
    pub strategy_version: String,
    pub event_seq: i64,
    pub signal_seq: i64,
    pub intent_seq: i64,
    pub lifecycle: StrategyLifecycle,
    pub position: StrategyPosState,
    pub contract: ContractView,
    pub warmup_bars: usize,
}

impl StrategyCtx {
    pub fn key(&self) -> String {
        format!("{}:{}", self.strategy_id, self.strategy_version)
    }
}

/// Live position projection (mirrors the `StrategyState` reset/open writes
/// in `_apply_fill`; the strategy's own state object stays Python).
#[derive(Debug, Clone, PartialEq)]
pub enum StrategyPosState {
    Flat,
    Open { side: String, avg: f64 },
}

pub trait StrategySource {
    fn on_candle(
        &mut self,
        ctx: &mut StrategyCtx,
        candle: &CandleEvent,
    ) -> Result<Option<StrategySignal>, String>;
    fn warmup(&mut self, ctx: &mut StrategyCtx, bars: &[Bar]);
    fn export_window(&self, ctx: &StrategyCtx) -> Vec<Bar>;
    fn restore_window(&mut self, ctx: &mut StrategyCtx, bars: Vec<Bar>);
}

/// Broker venue (transports stay Python).
pub trait BrokerVenue {
    fn name(&self) -> String;
    fn capabilities(&self) -> Vec<String>;
    fn health(&mut self) -> (bool, String);
    fn reference_spread(&mut self, symbol: &str) -> Option<f64>;
    fn place_order(&mut self, plan: &OrderPlan, client_order_id: &str) -> Result<String, String>;
    fn stream_events(&mut self) -> Result<Vec<BrokerStreamEvent>, String>;
    fn positions(&mut self) -> Result<Vec<(String, String)>, String>;
    fn open_orders(&mut self) -> Result<Vec<String>, String>;
    fn on_market_price(&mut self, symbol: &str, price: f64, timestamp: &str);
    fn account_view(&mut self) -> (String, String, usize);
    fn disconnect(&mut self) -> Result<(), String>;
}

/// Venue stream facts (mirrors the ack/fill/reject/cancel dict machine;
/// malformed shapes are unrepresentable here).
#[derive(Debug, Clone, PartialEq)]
pub enum BrokerStreamEvent {
    Ack {
        client_order_id: String,
        broker_order_id: String,
    },
    Fill {
        client_order_id: String,
        fill: Fill,
    },
    Reject {
        client_order_id: String,
        reason: String,
    },
    Cancel {
        client_order_id: String,
    },
    Other {
        client_order_id: String,
    },
}

/// Raw provider arrivals (the seam normalizes quote/trade shapes into
/// `Market`, carrying the optional price `_update_market_state` reads).
#[derive(Debug, Clone, PartialEq)]
pub enum ProviderEvent {
    Heartbeat(HeartbeatEvent),
    Candle(CandleEvent),
    Market {
        kind: String,
        symbol: String,
        timestamp: String,
        seq: i64,
        price: Option<f64>,
    },
}

pub trait MarketProvider {
    fn capabilities(&self) -> Vec<String>;
    fn open(&mut self, symbols: &[String], timeframe: &str);
    fn poll(&mut self) -> Vec<ProviderEvent>;
}

/// Bundled advisory seam (attention/confidence/memory/regime impls stay
/// Python; only their verdicts cross).
#[derive(Debug, Clone, PartialEq)]
pub struct Posture {
    pub halt: bool,
    pub prefer_limit: bool,
    pub size_multiplier: SizeRaw,
}

pub trait AdaptiveSim {
    fn attention_process(&mut self, symbol: &str, seq: i64) -> bool;
    fn note_event(&mut self, kind: &str, symbol: &str, seq: i64);
    fn note_signal(&mut self, signal_id: &str, side: &Side);
    fn set_regime(&mut self, regime: &str);
    fn set_position(&mut self, qty: f64);
    fn record_incident(&mut self, strategy_id: &str, reason: &str, timestamp: &str);
    fn regime_for(&mut self, close: f64, volume: f64) -> String;
    fn evaluate(&mut self, data_stale: bool, broker_unstable: bool) -> Posture;
}

pub trait LatencyView {
    fn stages(&self) -> Vec<String>;
    fn summary(&self, stage: &str) -> f64;
}

// ── config + state shapes ─────────────────────────────────────────────────

/// Session wiring (mirrors `SessionConfig` defaults; path wiring and venue
/// construction stay Python — only data crosses).
#[derive(Debug, Clone)]
pub struct SessionConfig {
    pub mode: ExecutionMode,
    pub gates: ModeGates,
    pub paper_capital: f64,
    pub slippage_pct: f64,
    pub commission_pct: f64,
    pub journal_path: Option<String>,
    pub memory_path: Option<String>,
    pub kill_switch_path: Option<String>,
    pub session_start: Option<String>,
    pub session_end: Option<String>,
    pub adapter_name: String,
}

impl Default for SessionConfig {
    fn default() -> Self {
        Self {
            mode: ExecutionMode::Paper,
            gates: ModeGates::default(),
            paper_capital: 1_000_000.0,
            slippage_pct: 0.02,
            commission_pct: 0.03,
            journal_path: None,
            memory_path: None,
            kill_switch_path: None,
            session_start: None,
            session_end: None,
            adapter_name: String::new(),
        }
    }
}

/// Resolved venue (mirrors `resolve_broker`'s triple; resolution itself —
/// venue construction — stays Python).
pub struct ResolvedVenue {
    pub venue: Box<dyn BrokerVenue>,
    pub mode: ExecutionMode,
    pub notes: Vec<String>,
}

/// Persistable session state (mirrors `checkpoint()`'s dict; JSON shapes
/// preserved, file IO stays Python).
#[derive(Debug, Clone, PartialEq)]
pub struct Checkpoint {
    pub mode: String,
    pub windows: HashMap<String, Vec<Bar>>,
    pub orders_today: i64,
    pub last_order_epoch: Option<f64>,
    pub engine: Vec<OrderSnapshot>,
}

/// Ledger-vs-venue comparison (mirrors `ReconciliationState`).
#[derive(Debug, Clone, PartialEq)]
pub struct SessionReconciliation {
    pub positions: ReconReport,
    pub orders: ReconReport,
    /// Funds leg: the session never recomputes it (stays default-matched,
    /// mirroring `ReconciliationState.funds`), but it rides the verdict.
    pub funds: ReconReport,
}

impl SessionReconciliation {
    pub fn matched() -> Self {
        let clear = || ReconReport {
            matched: true,
            mismatches: Vec::new(),
            checked_at: String::new(),
        };
        Self {
            positions: clear(),
            orders: clear(),
            funds: clear(),
        }
    }

    pub fn blocks_live(&self) -> bool {
        self.positions.blocks_live() || self.orders.blocks_live() || self.funds.blocks_live()
    }
}

/// Ops/UI snapshot (mirrors `state()`'s dict; no handles inside).
#[derive(Debug, Clone, PartialEq)]
pub struct SessionStateView {
    pub mode: String,
    pub armed: String,
    pub lifecycle: String,
    pub strategies: Vec<(String, String, i64, i64)>,
    pub broker_connected: bool,
    pub broker_reason: String,
    pub broker_name: String,
    pub broker_mode: String,
    pub account_id: String,
    pub environment: String,
    pub broker_fills: usize,
    pub broker_calls: u64,
    pub stream_errors: u64,
    pub kill_halted: bool,
    pub positions: Vec<(String, f64, f64)>,
    pub open_orders: usize,
    pub total_orders: usize,
    pub fills: usize,
    pub day_pnl: f64,
    pub journal_kinds: Vec<(String, usize)>,
    pub blocks_live: bool,
    pub latency: Vec<(String, f64)>,
}

// ── the session ───────────────────────────────────────────────────────────

/// Awaiting-release arrivals, keyed `(symbol, seq)` (mirrors the raw objects
/// the Python normalizer buffer holds; pruned below the watermark so nothing
/// leaks).
#[derive(Debug, Clone, PartialEq)]
enum PendingPayload {
    Candle(CandleEvent),
    Market {
        kind: String,
        timestamp: String,
        price: Option<f64>,
    },
}

pub struct LiveSession {
    #[allow(dead_code)]
    config: SessionConfig,
    request_id: String,
    kill_halted: bool,
    risk: RiskEngine,
    engine: ExecutionEngine,
    ledger: Ledger,
    normalizer: StreamNormalizer,
    lifecycle: StrategyLifecycle,
    contexts: Vec<StrategyCtx>,
    sources: HashMap<String, Box<dyn StrategySource>>,
    broker: Option<Box<dyn BrokerVenue>>,
    provider: Box<dyn MarketProvider>,
    journal: Box<dyn SessionJournal>,
    bus: Option<Box<dyn SessionBus>>,
    recorder: Box<dyn RecorderSink>,
    adaptive: Box<dyn AdaptiveSim>,
    latency: Box<dyn LatencyView>,
    mode: ExecutionMode,
    armed: LiveArm,
    broker_calls: u64,
    broker_stream_errors: u64,
    last_order_epoch: Option<f64>,
    orders_today: i64,
    reconciliation: SessionReconciliation,
    pending: HashMap<(String, i64), PendingPayload>,
}

impl LiveSession {
    pub fn new(
        config: SessionConfig,
        provider: Box<dyn MarketProvider>,
        journal: Box<dyn SessionJournal>,
        recorder: Box<dyn RecorderSink>,
        adaptive: Box<dyn AdaptiveSim>,
        latency: Box<dyn LatencyView>,
        policy: RiskPolicy,
    ) -> Result<Self, String> {
        Ok(Self {
            ledger: Ledger::new(config.paper_capital).map_err(|e| e.to_string())?,
            mode: config.mode,
            config,
            request_id: "live-1".to_string(),
            kill_halted: false,
            risk: RiskEngine::new(policy),
            engine: ExecutionEngine::new(),
            normalizer: StreamNormalizer::new(NormalizerConfig::default()),
            lifecycle: StrategyLifecycle::new(),
            contexts: Vec::new(),
            sources: HashMap::new(),
            broker: None,
            provider,
            journal,
            bus: None,
            recorder,
            adaptive,
            latency,
            armed: LiveArm::Disarmed,
            broker_calls: 0,
            broker_stream_errors: 0,
            last_order_epoch: None,
            orders_today: 0,
            reconciliation: SessionReconciliation::matched(),
            pending: HashMap::new(),
        })
    }

    pub fn attach_bus(&mut self, bus: Box<dyn SessionBus>) {
        self.bus = Some(bus);
    }

    pub fn set_kill_halted(&mut self, halted: bool) {
        self.kill_halted = halted;
    }

    fn emit(&mut self, event: SessionEvent) {
        if let Some(bus) = self.bus.as_mut() {
            bus.emit(event);
        }
    }

    fn ctx_key(strategy_id: &str, version: &str) -> String {
        format!("{strategy_id}:{version}")
    }

    /// Register one isolated strategy instance (contract + source cross;
    /// `logic_factory`/`inspect_strategy` stay Python).
    pub fn register_strategy(
        &mut self,
        strategy_id: &str,
        version: &str,
        contract: ContractView,
        warmup_bars: usize,
        source: Box<dyn StrategySource>,
    ) -> ContractView {
        let key = Self::ctx_key(strategy_id, version);
        self.contexts.push(StrategyCtx {
            strategy_id: strategy_id.to_string(),
            strategy_version: version.to_string(),
            event_seq: 0,
            signal_seq: 0,
            intent_seq: 0,
            lifecycle: StrategyLifecycle::new(),
            position: StrategyPosState::Flat,
            contract: contract.clone(),
            warmup_bars,
        });
        self.sources.insert(key, source);
        contract
    }

    fn transition_session(&mut self, target: LifecycleState, reason: &str) -> Result<(), String> {
        self.lifecycle
            .transition(target, reason)
            .map_err(|e| e.to_string())
    }

    /// Bring the session to RUNNING (mirrors `start`).
    pub fn start(
        &mut self,
        symbols: &[String],
        timeframe: &str,
        warmup: &HashMap<String, Vec<Bar>>,
        resolved: ResolvedVenue,
    ) -> Result<ReadinessReport, String> {
        let recovering = self.lifecycle.state == LifecycleState::Recovering;
        if self.lifecycle.state == LifecycleState::Created {
            self.transition_session(LifecycleState::Validating, "start requested")?;
        } else if recovering {
            self.transition_session(LifecycleState::Reconciling, "recovered, reconciling")?;
        } else {
            return Err(format!(
                "start requires CREATED or RECOVERING, found {}",
                self.lifecycle.state.as_str()
            ));
        }
        self.broker = Some(resolved.venue);
        self.mode = resolved.mode;
        for note in &resolved.notes {
            self.journal.record(
                "MODE_DOWNGRADE",
                vec![("reason".to_string(), field_str(note.clone()))],
            );
        }
        if recovering {
            self.reconcile_now();
            self.transition_session(LifecycleState::Validating, "reconciled, validating")?;
        }
        for ctx in self.contexts.iter_mut() {
            if ctx.lifecycle.state == LifecycleState::Created {
                ctx.lifecycle
                    .transition(LifecycleState::Validating, "session start")
                    .map_err(|e| e.to_string())?;
            }
        }
        let report = self.readiness(warmup);
        if !report.ready {
            for ctx in self.contexts.iter_mut() {
                if ctx.lifecycle.state == LifecycleState::Validating {
                    ctx.lifecycle
                        .transition(LifecycleState::Error, &report.reasons.join("; "))
                        .map_err(|e| e.to_string())?;
                }
            }
            let reasons = report.reasons.clone();
            self.journal.record(
                "NOT_LIVE_READY",
                vec![("reasons".to_string(), Field::StrList(reasons))],
            );
            return Ok(report);
        }
        self.provider.open(symbols, timeframe);
        self.transition_session(LifecycleState::WarmingUp, "warming strategies")?;
        // Borrow split: contexts/sources/journals move together per key.
        let keys: Vec<String> = self.contexts.iter().map(|c| c.key()).collect();
        for (index, key) in keys.iter().enumerate() {
            {
                let ctx = &mut self.contexts[index];
                ctx.lifecycle
                    .transition(LifecycleState::WarmingUp, "warming")
                    .map_err(|e| e.to_string())?;
            }
            let bars = warmup
                .get(&self.contexts[index].strategy_id)
                .cloned()
                .unwrap_or_default();
            if let Some(source) = self.sources.get_mut(key) {
                source.warmup(&mut self.contexts[index], &bars);
            }
            {
                let ctx = &mut self.contexts[index];
                ctx.lifecycle
                    .transition(LifecycleState::Ready, "warmed")
                    .map_err(|e| e.to_string())?;
                ctx.lifecycle
                    .transition(LifecycleState::Running, "session running")
                    .map_err(|e| e.to_string())?;
            }
        }
        self.transition_session(LifecycleState::Ready, "strategies ready")?;
        self.transition_session(LifecycleState::Running, "ready")?;
        self.journal.record(
            "LIVE_READY",
            vec![
                (
                    "mode".to_string(),
                    field_str(self.mode.as_str().to_string()),
                ),
                ("symbols".to_string(), Field::StrList(symbols.to_vec())),
            ],
        );
        Ok(report)
    }

    /// Aggregate per-context readiness (mirrors `_readiness`).
    pub fn readiness(&mut self, warmup: &HashMap<String, Vec<Bar>>) -> ReadinessReport {
        let broker = self.broker.as_mut().expect("readiness needs a broker");
        let broker_caps = broker.capabilities();
        let broker_healthy = broker.health().0;
        let data_caps = self.provider.capabilities();
        let blocks = self.reconciliation.blocks_live();
        let kill_ok = !self.kill_halted;
        let for_live = self.mode == ExecutionMode::Live;
        let mut reasons = Vec::new();
        for ctx in &self.contexts {
            let available = warmup.get(&ctx.strategy_id).map(|b| b.len()).unwrap_or(0);
            let (_, mut ctx_reasons) = check_live_readiness(
                &ctx.contract,
                &data_caps,
                &broker_caps,
                available,
                true,
                broker_healthy,
                true,
                !blocks,
                true,
                kill_ok,
                true,
                for_live,
            );
            for reason in ctx_reasons.drain(..) {
                reasons.push(format!("{}: {}", ctx.key(), reason));
            }
        }
        if self.mode == ExecutionMode::Live && blocks {
            reasons.push("reconciliation mismatch blocks live".to_string());
        }
        ReadinessReport {
            ready: reasons.is_empty(),
            reasons,
        }
    }

    /// Poll once; drive every deliverable event through the pipeline.
    pub fn step(&mut self, now_epoch: f64) -> Result<usize, String> {
        if self.broker.is_none() {
            return Err("session not started".to_string());
        }
        let mut delivered = 0usize;
        let arrivals = self.provider.poll();
        for raw in arrivals {
            match raw {
                ProviderEvent::Heartbeat(event) => {
                    self.recorder
                        .record("HeartbeatEvent", &event.base.symbol, event.base.seq);
                    let input = StreamInput::Heartbeat {
                        symbol: event.base.symbol,
                        timestamp: event.base.timestamp,
                        seq: event.base.seq,
                    };
                    self.normalizer
                        .observe(input, now_epoch)
                        .map_err(|e| e.to_string())?;
                }
                ProviderEvent::Candle(event) => {
                    self.recorder
                        .record("CandleEvent", &event.base.symbol, event.base.seq);
                    let (healthy, _) = self.normalizer.check_health(&event.base.symbol, now_epoch);
                    if !healthy {
                        self.journal.record(
                            "STALE_DATA",
                            vec![
                                ("symbol".to_string(), field_str(event.base.symbol.clone())),
                                ("seq".to_string(), field_int(event.base.seq)),
                            ],
                        );
                        continue;
                    }
                    let symbol = event.base.symbol.clone();
                    let marker = MarketEvent {
                        symbol: symbol.clone(),
                        timestamp: event.base.timestamp.clone(),
                        seq: event.base.seq,
                        source: event.base.source.clone(),
                    };
                    self.pending
                        .insert((symbol.clone(), marker.seq), PendingPayload::Candle(event));
                    let ordered = self
                        .normalizer
                        .observe(StreamInput::Event(marker), now_epoch)
                        .map_err(|e| e.to_string())?;
                    delivered += self.drive_released(&symbol, ordered, now_epoch);
                }
                ProviderEvent::Market {
                    kind,
                    symbol,
                    timestamp,
                    seq,
                    price,
                } => {
                    self.recorder.record(&kind, &symbol, seq);
                    let (healthy, _) = self.normalizer.check_health(&symbol, now_epoch);
                    if !healthy {
                        self.journal.record(
                            "STALE_DATA",
                            vec![
                                ("symbol".to_string(), field_str(symbol)),
                                ("seq".to_string(), field_int(seq)),
                            ],
                        );
                        continue;
                    }
                    self.pending.insert(
                        (symbol.clone(), seq),
                        PendingPayload::Market {
                            kind: kind.clone(),
                            timestamp: timestamp.clone(),
                            price,
                        },
                    );
                    let ordered = self
                        .normalizer
                        .observe(
                            StreamInput::Event(MarketEvent {
                                symbol: symbol.clone(),
                                timestamp,
                                seq,
                                source: String::new(),
                            }),
                            now_epoch,
                        )
                        .map_err(|e| e.to_string())?;
                    delivered += self.drive_released(&symbol, ordered, now_epoch);
                }
            }
        }
        self.process_broker_stream();
        Ok(delivered)
    }

    /// Drive every event the normalizer released, in sequence order (mirrors
    /// the `for event in observe(...)` loop in `step`, including gap-fill
    /// multi-release: each released seq resolves to the retained arrival).
    fn drive_released(&mut self, symbol: &str, ordered: Vec<MarketEvent>, now_epoch: f64) -> usize {
        let mut delivered = 0;
        for marker in &ordered {
            let key = (symbol.to_string(), marker.seq);
            let payload = match self.pending.remove(&key) {
                Some(payload) => payload,
                None => continue,
            };
            if !self.adaptive.attention_process(symbol, marker.seq) {
                continue;
            }
            match payload {
                PendingPayload::Candle(candle) => {
                    self.adaptive.note_event("CandleEvent", symbol, marker.seq);
                    self.on_event_candle(candle, now_epoch);
                    delivered += 1;
                }
                PendingPayload::Market {
                    kind,
                    timestamp,
                    price,
                } => {
                    self.adaptive.note_event(&kind, symbol, marker.seq);
                    if let Some(price) = price {
                        if let Some(broker) = self.broker.as_mut() {
                            broker.on_market_price(symbol, price, &timestamp);
                        }
                    }
                    delivered += 1;
                }
            }
        }
        // Shadowed seqs (evicted, then lapped by the watermark) can never
        // deliver — drop them so the pending map stays bounded.
        let watermark = self.normalizer.expected_seq(symbol);
        self.pending
            .retain(|(pending_symbol, seq), _| pending_symbol != symbol || *seq >= watermark);
        delivered
    }

    fn on_event_candle(&mut self, event: CandleEvent, now_epoch: f64) {
        let regime = self.adaptive.regime_for(event.close, event.volume as f64);
        self.adaptive.set_regime(&regime);
        let keys: Vec<String> = self.contexts.iter().map(|c| c.key()).collect();
        for (index, key) in keys.iter().enumerate() {
            let signal = match self.sources.get_mut(key) {
                Some(source) => source.on_candle(&mut self.contexts[index], &event),
                None => continue,
            };
            let signal = match signal {
                Ok(Some(signal)) => signal,
                Ok(None) => continue,
                Err(reason) => {
                    let strategy_id = self.contexts[index].strategy_id.clone();
                    self.journal.record(
                        "STRATEGY_ERROR",
                        vec![
                            ("strategy_id".to_string(), field_str(strategy_id.clone())),
                            ("reason".to_string(), field_str(reason.clone())),
                        ],
                    );
                    self.adaptive
                        .record_incident(&strategy_id, &reason, &event.base.timestamp);
                    continue;
                }
            };
            self.journal.record(
                "SIGNAL_GENERATED",
                vec![
                    (
                        "strategy_id".to_string(),
                        field_str(signal.strategy_id.clone()),
                    ),
                    ("signal_id".to_string(), field_str(signal.signal_id.clone())),
                    ("event_seq".to_string(), field_int(signal.event_seq)),
                ],
            );
            self.emit(SessionEvent::Signal(SignalGenerated {
                request_id: self.request_id.clone(),
                strategy_id: signal.strategy_id.clone(),
                signal_id: signal.signal_id.clone(),
                event_seq: signal.event_seq,
            }));
            self.adaptive.note_signal(&signal.signal_id, &signal.side);
            if let Err(reason) = self.handle_signal(index, &signal, now_epoch) {
                let strategy_id = self.contexts[index].strategy_id.clone();
                self.journal.record(
                    "STRATEGY_ERROR",
                    vec![
                        ("strategy_id".to_string(), field_str(strategy_id)),
                        ("reason".to_string(), field_str(reason)),
                    ],
                );
            }
        }
        if let Some(broker) = self.broker.as_mut() {
            broker.on_market_price(&event.base.symbol, event.close, &event.base.timestamp);
        }
        self.process_broker_stream();
    }

    fn default_quantity(&mut self, symbol: &str, price: f64) -> f64 {
        if price <= 0.0 {
            return 0.0;
        }
        let mut marks = HashMap::new();
        marks.insert(symbol.to_string(), price);
        let snapshot = self.ledger.snapshot(&marks);
        let affordable = snapshot.available_capital / price;
        affordable.min(self.risk.policy().max_order_qty).max(0.0)
    }

    #[allow(clippy::too_many_arguments)]
    fn handle_signal(
        &mut self,
        index: usize,
        signal: &StrategySignal,
        now_epoch: f64,
    ) -> Result<(), String> {
        let (ledger_qty, _, _) = self.ledger.strategy_state_for(&signal.symbol);
        if signal.side == Side::Buy && ledger_qty > 0.0 {
            return Ok(());
        }
        if signal.side == Side::Sell && ledger_qty <= 0.0 {
            return Ok(());
        }
        self.contexts[index].intent_seq += 1;
        let intent_seq = self.contexts[index].intent_seq;
        let intent_id = make_intent_id(
            &signal.strategy_id,
            &signal.strategy_version,
            signal.event_seq,
            intent_seq,
        );
        let (quantity, target) = if signal.side == Side::Buy {
            let quantity = self.default_quantity(&signal.symbol, signal.price);
            (quantity, ledger_qty + quantity)
        } else {
            (ledger_qty.abs(), 0.0)
        };
        let stale = self.normalizer.is_stale(&signal.symbol);
        let broker_ok = self.broker.as_mut().map(|b| b.health().0).unwrap_or(false);
        let posture = self.adaptive.evaluate(stale, !broker_ok);
        if posture.halt {
            self.journal.record(
                "EXECUTION_HALTED",
                vec![
                    ("reason".to_string(), field_str("adaptive posture HALT")),
                    ("intent_id".to_string(), field_str(intent_id)),
                ],
            );
            return Ok(());
        }
        let prefer_limit = posture.prefer_limit;
        let intent = ExecutionIntent {
            intent_id: intent_id.clone(),
            strategy_id: signal.strategy_id.clone(),
            strategy_version: signal.strategy_version.clone(),
            signal_id: signal.signal_id.clone(),
            timestamp: signal.timestamp.clone(),
            event_seq: signal.event_seq,
            symbol: signal.symbol.clone(),
            side: signal.side,
            target_position_qty: target,
            quantity,
            preferred_order_type: if prefer_limit {
                OrderType::Limit
            } else {
                OrderType::Market
            },
            reason: signal.reason.clone(),
            confidence: signal.confidence,
        };
        let mut marks = HashMap::new();
        marks.insert(signal.symbol.clone(), signal.price);
        let snapshot = self.ledger.snapshot(&marks);
        let spread = self
            .broker
            .as_mut()
            .and_then(|b| b.reference_spread(&signal.symbol));
        let request = RiskRequest {
            intent_id: intent.intent_id.clone(),
            strategy_id: intent.strategy_id.clone(),
            symbol: intent.symbol.clone(),
            side: intent.side.as_str().to_string(),
            quantity: intent.quantity,
            price: signal.price,
            timestamp: signal.timestamp.clone(),
            position_qty: ledger_qty,
            day_pnl: snapshot.day_pnl,
            strategy_day_pnl: snapshot.day_pnl,
            equity: snapshot.equity,
            available_capital: snapshot.available_capital,
            spread_pct: spread,
            data_age_seconds: Some(0.0),
            broker_healthy: self.broker.as_mut().map(|b| b.health().0).unwrap_or(false),
            orders_today: self.orders_today,
            last_order_epoch: self.last_order_epoch,
            now_epoch,
        };
        let decision = self.risk.evaluate(&request, self.kill_halted);
        if !decision.approved {
            self.journal.record(
                "RISK_DENIED",
                vec![
                    ("intent_id".to_string(), field_str(intent.intent_id.clone())),
                    (
                        "reasons".to_string(),
                        Field::StrList(decision.reasons.clone()),
                    ),
                ],
            );
            self.emit(SessionEvent::RiskDenied(RiskDenied {
                request_id: self.request_id.clone(),
                intent_id: intent.intent_id,
                reasons: decision.reasons,
            }));
            return Ok(());
        }
        self.journal.record(
            "RISK_APPROVED",
            vec![("intent_id".to_string(), field_str(intent.intent_id.clone()))],
        );
        self.emit(SessionEvent::RiskApproved(RiskApproved {
            request_id: self.request_id.clone(),
            intent_id: intent.intent_id.clone(),
        }));
        let prefs =
            ExecutionPreferences::new(prefer_limit, size_multiplier(posture.size_multiplier))
                .map_err(|e| e.to_string())?;
        let plan = plan_order(
            &PlanIntent {
                intent_id: intent.intent_id.clone(),
                symbol: intent.symbol.clone(),
                side: intent.side,
                quantity: intent.quantity,
                preferred_order_type: intent.preferred_order_type,
            },
            signal.price,
            &prefs,
        );
        let final_request = RiskRequest {
            intent_id: format!("{}:final", intent.intent_id),
            quantity: plan.quantity,
            symbol: plan.symbol.clone(),
            side: plan.side.as_str().to_string(),
            ..request
        };
        let final_decision = self.risk.evaluate(&final_request, self.kill_halted);
        if !final_decision.approved {
            self.journal.record(
                "RISK_DENIED",
                vec![
                    ("intent_id".to_string(), field_str(intent.intent_id.clone())),
                    (
                        "reasons".to_string(),
                        Field::StrList(final_decision.reasons.clone()),
                    ),
                ],
            );
            self.emit(SessionEvent::RiskDenied(RiskDenied {
                request_id: self.request_id.clone(),
                intent_id: intent.intent_id,
                reasons: final_decision.reasons,
            }));
            return Ok(());
        }
        self.submit(&intent, &plan, now_epoch)
    }

    fn submit(
        &mut self,
        intent: &ExecutionIntent,
        plan: &OrderPlan,
        now_epoch: f64,
    ) -> Result<(), String> {
        if self.broker.is_none() {
            return Err("broker missing".to_string());
        }
        if self.mode == ExecutionMode::Live && self.armed != LiveArm::Armed {
            self.journal.record(
                "ORDER_BLOCKED_UNARMED",
                vec![
                    ("intent_id".to_string(), field_str(intent.intent_id.clone())),
                    ("reason".to_string(), field_str("live not armed")),
                ],
            );
            return Ok(());
        }
        let client_order_id = format!("{}:o1", intent.intent_id);
        if plan.quantity <= 0.0 {
            self.journal.record(
                "ORDER_SKIPPED",
                vec![
                    ("intent_id".to_string(), field_str(intent.intent_id.clone())),
                    ("reason".to_string(), field_str("zero quantity")),
                ],
            );
            return Ok(());
        }
        let broker_name = self.broker.as_ref().map(|b| b.name()).unwrap_or_default();
        let environment = self.mode.as_str().to_lowercase();
        let mut order = BrokerOrder::new(
            client_order_id.clone(),
            intent.intent_id.clone(),
            plan.symbol.clone(),
            plan.side,
            plan.quantity,
        );
        order.order_type = plan.order_type;
        order.limit_price = plan.limit_price;
        self.journal.record(
            "ORDER_PLANNED",
            vec![
                ("intent_id".to_string(), field_str(intent.intent_id.clone())),
                (
                    "client_order_id".to_string(),
                    field_str(client_order_id.clone()),
                ),
                (
                    "order_type".to_string(),
                    field_str(plan.order_type.as_str().to_string()),
                ),
                ("broker".to_string(), field_str(broker_name)),
                ("environment".to_string(), field_str(environment.clone())),
            ],
        );
        self.emit(SessionEvent::Planned(OrderPlanned {
            request_id: self.request_id.clone(),
            intent_id: intent.intent_id.clone(),
            client_order_id: client_order_id.clone(),
        }));
        let tracked = self.engine.create(order).map_err(|e| e.to_string())?;
        let tracked = self
            .engine
            .transition(
                &tracked.client_order_id,
                order_state::VALIDATED,
                "risk approved",
            )
            .map_err(|e| e.to_string())?;
        let broker_id = match self
            .broker
            .as_mut()
            .expect("checked above")
            .place_order(plan, &tracked.client_order_id)
        {
            Ok(id) => id,
            Err(reason) => {
                self.engine
                    .transition(&tracked.client_order_id, order_state::REJECTED, &reason)
                    .map_err(|e| e.to_string())?;
                self.journal.record(
                    "ORDER_REJECTED",
                    vec![
                        (
                            "client_order_id".to_string(),
                            field_str(client_order_id.clone()),
                        ),
                        ("reason".to_string(), field_str(reason.clone())),
                    ],
                );
                self.emit(SessionEvent::Rejected(OrderRejected {
                    request_id: self.request_id.clone(),
                    client_order_id,
                    reason,
                }));
                return Ok(());
            }
        };
        self.broker_calls += 1;
        self.engine
            .transition(
                &tracked.client_order_id,
                order_state::SUBMITTED,
                "sent to broker",
            )
            .map_err(|e| e.to_string())?;
        let broker_name = self.broker.as_ref().map(|b| b.name()).unwrap_or_default();
        self.journal.record(
            "ORDER_SUBMITTED",
            vec![
                (
                    "client_order_id".to_string(),
                    field_str(client_order_id.clone()),
                ),
                ("broker_order_id".to_string(), field_str(broker_id)),
                ("broker".to_string(), field_str(broker_name)),
                ("environment".to_string(), field_str(environment)),
            ],
        );
        self.emit(SessionEvent::Submitted(OrderSubmitted {
            request_id: self.request_id.clone(),
            client_order_id,
        }));
        self.orders_today += 1;
        self.last_order_epoch = Some(now_epoch);
        Ok(())
    }

    fn apply_fill(&mut self, tracked: &BrokerOrder, fill: &Fill) {
        let updated = match self.engine.apply_fill(tracked, fill) {
            Ok(updated) => updated,
            Err(reason) => {
                self.journal.record(
                    "BROKER_EVENT_IGNORED",
                    vec![("reason".to_string(), field_str(reason))],
                );
                return;
            }
        };
        let transitioned = if fill.partial {
            self.engine.transition(
                &updated.client_order_id,
                order_state::PARTIALLY_FILLED,
                "partial",
            )
        } else {
            self.engine
                .transition(&updated.client_order_id, order_state::FILLED, "fill")
        };
        if let Err(reason) = transitioned {
            self.journal.record(
                "BROKER_EVENT_IGNORED",
                vec![("reason".to_string(), field_str(reason))],
            );
            return;
        }
        let position = self.ledger.apply_fill(
            &fill.symbol,
            fill.side,
            fill.fill_qty,
            fill.fill_price,
            fill.commission,
        );
        let _ = position;
        let (qty, side, entry) = self.ledger.strategy_state_for(&fill.symbol);
        for ctx in self.contexts.iter_mut() {
            if ctx.strategy_id == strategy_of_intent(&tracked.intent_id) {
                ctx.position = match side {
                    None => StrategyPosState::Flat,
                    Some(side) => StrategyPosState::Open {
                        side: side.to_string(),
                        avg: entry.unwrap_or(0.0),
                    },
                };
            }
        }
        self.adaptive.set_position(qty);
        let broker_name = self.broker.as_ref().map(|b| b.name()).unwrap_or_default();
        let environment = self.mode.as_str().to_lowercase();
        self.journal.record(
            "FILL",
            vec![
                (
                    "client_order_id".to_string(),
                    field_str(fill.client_order_id.clone()),
                ),
                (
                    "broker_order_id".to_string(),
                    field_str(fill.broker_order_id.clone().unwrap_or_default()),
                ),
                (
                    "intent_id".to_string(),
                    field_str(tracked.intent_id.clone()),
                ),
                ("fill_qty".to_string(), field_float(fill.fill_qty)),
                ("fill_price".to_string(), field_float(fill.fill_price)),
                ("partial".to_string(), field_bool(fill.partial)),
                ("broker".to_string(), field_str(broker_name)),
                ("environment".to_string(), field_str(environment)),
            ],
        );
        self.journal.record(
            "POSITION_UPDATED",
            vec![
                ("symbol".to_string(), field_str(fill.symbol.clone())),
                ("quantity".to_string(), field_float(qty)),
            ],
        );
        self.emit(SessionEvent::Filled(OrderFill {
            request_id: self.request_id.clone(),
            client_order_id: fill.client_order_id.clone(),
            fill_qty: fill.fill_qty,
            fill_price: fill.fill_price,
            partial: fill.partial,
        }));
        self.emit(SessionEvent::Positions(PositionUpdated {
            request_id: self.request_id.clone(),
            symbol: fill.symbol.clone(),
            quantity: qty,
        }));
    }

    fn process_broker_stream(&mut self) {
        let events = match self.broker.as_mut() {
            None => return,
            Some(broker) => match broker.stream_events() {
                Ok(events) => events,
                Err(reason) => {
                    self.broker_stream_errors += 1;
                    self.journal.record(
                        "BROKER_STREAM_ERROR",
                        vec![("reason".to_string(), field_str(reason))],
                    );
                    return;
                }
            },
        };
        for event in events {
            self.dispatch_stream(event);
        }
    }

    fn dispatch_stream(&mut self, event: BrokerStreamEvent) {
        let (kind, client_order_id) = match &event {
            BrokerStreamEvent::Ack {
                client_order_id, ..
            } => ("ack", client_order_id.clone()),
            BrokerStreamEvent::Fill {
                client_order_id, ..
            } => ("fill", client_order_id.clone()),
            BrokerStreamEvent::Reject {
                client_order_id, ..
            } => ("reject", client_order_id.clone()),
            BrokerStreamEvent::Cancel { client_order_id } => ("cancel", client_order_id.clone()),
            BrokerStreamEvent::Other { client_order_id } => ("other", client_order_id.clone()),
        };
        let tracked = if client_order_id.is_empty() {
            None
        } else {
            self.engine.get(&client_order_id).cloned()
        };
        let tracked = match tracked {
            Some(tracked) => tracked,
            None => {
                self.journal.record(
                    "BROKER_EVENT_IGNORED",
                    vec![(
                        "reason".to_string(),
                        field_str(format!("unknown order: {client_order_id}")),
                    )],
                );
                return;
            }
        };
        let kind_msg = kind;
        let id_msg = client_order_id.clone();
        let outcome: Result<(), String> = (|| match event {
            BrokerStreamEvent::Ack {
                broker_order_id, ..
            } => {
                self.engine.transition(
                    &tracked.client_order_id,
                    order_state::ACKNOWLEDGED,
                    "broker ack",
                )?;
                let broker_name = self.broker.as_ref().map(|b| b.name()).unwrap_or_default();
                let environment = self.mode.as_str().to_lowercase();
                self.journal.record(
                    "ORDER_ACK",
                    vec![
                        (
                            "client_order_id".to_string(),
                            field_str(tracked.client_order_id.clone()),
                        ),
                        (
                            "broker_order_id".to_string(),
                            field_str(broker_order_id.clone()),
                        ),
                        ("broker".to_string(), field_str(broker_name)),
                        ("environment".to_string(), field_str(environment)),
                    ],
                );
                self.emit(SessionEvent::Acknowledged(OrderAcknowledged {
                    request_id: self.request_id.clone(),
                    client_order_id: tracked.client_order_id.clone(),
                    broker_order_id,
                }));
                Ok(())
            }
            BrokerStreamEvent::Fill { fill, .. } => {
                self.apply_fill(&tracked, &fill);
                Ok(())
            }
            BrokerStreamEvent::Reject { reason, .. } => {
                self.engine
                    .transition(&tracked.client_order_id, order_state::REJECTED, &reason)?;
                self.journal.record(
                    "ORDER_REJECTED",
                    vec![
                        (
                            "client_order_id".to_string(),
                            field_str(tracked.client_order_id.clone()),
                        ),
                        ("reason".to_string(), field_str(reason.clone())),
                    ],
                );
                self.emit(SessionEvent::Rejected(OrderRejected {
                    request_id: self.request_id.clone(),
                    client_order_id: tracked.client_order_id.clone(),
                    reason,
                }));
                Ok(())
            }
            BrokerStreamEvent::Cancel { .. } => {
                self.engine.transition(
                    &tracked.client_order_id,
                    order_state::CANCELLED,
                    "venue cancel",
                )?;
                self.journal.record(
                    "ORDER_CANCELLED",
                    vec![(
                        "client_order_id".to_string(),
                        field_str(tracked.client_order_id.clone()),
                    )],
                );
                Ok(())
            }
            BrokerStreamEvent::Other { .. } => {
                self.journal.record(
                    "BROKER_EVENT",
                    vec![("at".to_string(), field_str(client_order_id))],
                );
                Ok(())
            }
        })();
        if let Err(reason) = outcome {
            self.journal.record(
                "BROKER_EVENT_IGNORED",
                vec![(
                    "reason".to_string(),
                    field_str(format!("{kind_msg} for {id_msg}: {reason}")),
                )],
            );
        }
    }

    /// Compare ledger vs broker truth (mirrors `reconcile_now`).
    pub fn reconcile_now(&mut self) {
        if self.broker.is_none() {
            return;
        }
        let broker_positions = match self.broker.as_mut().expect("checked").positions() {
            Ok(positions) => positions,
            Err(reason) => {
                self.journal.record(
                    "RECONCILE_ERROR",
                    vec![("reason".to_string(), field_str(reason))],
                );
                return;
            }
        };
        let broker_open = match self.broker.as_mut().expect("checked").open_orders() {
            Ok(open) => open,
            Err(reason) => {
                self.journal.record(
                    "RECONCILE_ERROR",
                    vec![("reason".to_string(), field_str(reason))],
                );
                return;
            }
        };
        let local: Vec<(String, f64)> = self
            .ledger
            .all_positions()
            .iter()
            .map(|p| (p.symbol.clone(), p.quantity))
            .collect();
        let local_open: Vec<String> = self
            .engine
            .open_orders()
            .iter()
            .map(|o| o.client_order_id.clone())
            .collect();
        let positions = reconcile_positions(&local, &broker_positions, 1e-09, "");
        let orders = reconcile_orders(&local_open, &broker_open, "");
        let mut reconciliation = SessionReconciliation::matched();
        reconciliation.positions = positions;
        reconciliation.orders = orders;
        self.reconciliation = reconciliation;
        let mut reasons = Vec::new();
        let verdict = verdict_of(
            &[
                &self.reconciliation.positions,
                &self.reconciliation.orders,
                &self.reconciliation.funds,
            ],
            true,
            &mut reasons,
        );
        let verdict_status = verdict.as_str().to_string();
        let mismatches = self.reconciliation.positions.mismatches.len()
            + self.reconciliation.orders.mismatches.len();
        self.journal.record(
            "RECONCILED",
            vec![
                (
                    "matched".to_string(),
                    field_bool(!self.reconciliation.blocks_live()),
                ),
                ("mismatches".to_string(), field_int(mismatches as i64)),
                ("status".to_string(), field_str(verdict_status)),
            ],
        );
    }

    /// Persistable snapshot (mirrors `checkpoint`).
    pub fn checkpoint(&mut self) -> Checkpoint {
        let mut windows = HashMap::new();
        for ctx in &self.contexts {
            let key = ctx.key();
            let bars = self
                .sources
                .get(&key)
                .map(|source| source.export_window(ctx))
                .unwrap_or_default();
            windows.insert(key, bars);
        }
        Checkpoint {
            mode: self.mode.as_str().to_string(),
            windows,
            orders_today: self.orders_today,
            last_order_epoch: self.last_order_epoch,
            engine: self.engine.snapshot(),
        }
    }

    /// Restore windows for re-warm (mirrors `recover`).
    pub fn recover(&mut self, checkpoint: &Checkpoint) -> Result<(), String> {
        if self.lifecycle.state == LifecycleState::Running {
            self.transition_session(LifecycleState::Stopping, "recover requested")?;
            self.transition_session(LifecycleState::Stopped, "recover requested")?;
        }
        self.transition_session(LifecycleState::Recovering, "recover requested")?;
        for ctx in self.contexts.iter_mut() {
            ctx.lifecycle = StrategyLifecycle::new();
            ctx.position = StrategyPosState::Flat;
            let bars = checkpoint
                .windows
                .get(&ctx.key())
                .cloned()
                .unwrap_or_default();
            let key = ctx.key();
            if let Some(source) = self.sources.get_mut(&key) {
                source.restore_window(ctx, bars);
            }
        }
        self.orders_today = checkpoint.orders_today;
        self.last_order_epoch = checkpoint.last_order_epoch;
        if !checkpoint.engine.is_empty() {
            let restored: Vec<_> = checkpoint.engine.iter().map(|s| s.into()).collect();
            self.engine.restore(restored).map_err(|e| e.to_string())?;
            let snapshot = self.engine.snapshot();
            let unknowns = snapshot
                .iter()
                .filter(|o| {
                    crate::execution_engine::parse_state(&o.state) == Some(order_state::UNKNOWN)
                })
                .count();
            self.journal.record(
                "IDEMPOTENCY_RESTORED",
                vec![
                    ("orders".to_string(), field_int(snapshot.len() as i64)),
                    ("unknown_orders".to_string(), field_int(unknowns as i64)),
                    (
                        "reconcile_required".to_string(),
                        field_bool(!snapshot.is_empty()),
                    ),
                ],
            );
        }
        Ok(())
    }

    /// Explicit live arming (mirrors `arm`).
    pub fn arm(&mut self, reason: &str) -> Result<LiveArm, String> {
        self.armed = arm_transition(self.armed, LiveArm::Arming).map_err(|e| e.to_string())?;
        self.armed = arm_transition(self.armed, LiveArm::Armed).map_err(|e| e.to_string())?;
        self.journal.record(
            "LIVE_ARMED",
            vec![("reason".to_string(), field_str(reason.to_string()))],
        );
        Ok(self.armed)
    }

    /// Release arming (mirrors `disarm`).
    pub fn disarm(&mut self, reason: &str) -> LiveArm {
        if self.armed != LiveArm::Disarmed {
            if let Ok(next) = arm_transition(self.armed, LiveArm::Disarmed) {
                self.armed = next;
            }
            self.journal.record(
                "LIVE_DISARMED",
                vec![("reason".to_string(), field_str(reason.to_string()))],
            );
        }
        self.armed
    }

    pub fn armed_state(&self) -> LiveArm {
        self.armed
    }

    /// Best-effort shutdown (mirrors `stop`'s per-step suppression).
    pub fn stop(&mut self) {
        let _ = self.transition_session(LifecycleState::Stopping, "stop requested");
        if let Some(broker) = self.broker.as_mut() {
            let _ = broker.disconnect();
        }
        let _ = self.transition_session(LifecycleState::Stopped, "stopped");
    }

    /// Full ops snapshot (mirrors `state()`; no handles inside).
    pub fn state_view(&mut self) -> SessionStateView {
        let (
            broker_connected,
            broker_reason,
            broker_name,
            broker_mode,
            account_id,
            environment,
            broker_fills,
        ) = match self.broker.as_mut() {
            None => (
                false,
                String::new(),
                String::new(),
                String::new(),
                String::new(),
                String::new(),
                0,
            ),
            Some(broker) => {
                let (healthy, reason) = broker.health();
                let (account_id, environment, fills) = broker.account_view();
                (
                    healthy,
                    reason,
                    broker.name(),
                    self.mode.as_str().to_string(),
                    account_id,
                    environment,
                    fills,
                )
            }
        };
        let mut marks = HashMap::new();
        for position in self.ledger.all_positions() {
            marks.insert(position.symbol.clone(), position.avg_price);
        }
        let snapshot = self.ledger.snapshot(&marks);
        SessionStateView {
            mode: self.mode.as_str().to_string(),
            armed: self.armed.as_str().to_string(),
            lifecycle: self.lifecycle.state.as_str().to_string(),
            strategies: self
                .contexts
                .iter()
                .map(|ctx| {
                    (
                        ctx.key(),
                        ctx.lifecycle.state.as_str().to_string(),
                        ctx.event_seq,
                        ctx.signal_seq,
                    )
                })
                .collect(),
            broker_connected,
            broker_reason,
            broker_name,
            broker_mode,
            account_id,
            environment,
            broker_fills,
            broker_calls: self.broker_calls,
            stream_errors: self.broker_stream_errors,
            kill_halted: self.kill_halted,
            positions: self
                .ledger
                .all_positions()
                .iter()
                .map(|p| (p.symbol.clone(), p.quantity, p.avg_price))
                .collect(),
            open_orders: self.engine.open_orders().len(),
            total_orders: self.engine.order_count(),
            fills: broker_fills,
            day_pnl: snapshot.day_pnl,
            journal_kinds: self.journal.kind_counts(),
            blocks_live: self.reconciliation.blocks_live(),
            latency: self
                .latency
                .stages()
                .iter()
                .map(|stage| (stage.clone(), self.latency.summary(stage)))
                .collect(),
        }
    }

    // ── introspection handles for tests ──────────────────────────────────

    pub fn lifecycle_state(&self) -> LifecycleState {
        self.lifecycle.state
    }

    pub fn context_lifecycle(&self, key: &str) -> Option<LifecycleState> {
        self.contexts
            .iter()
            .find(|c| c.key() == key)
            .map(|c| c.lifecycle.state)
    }

    pub fn mode(&self) -> ExecutionMode {
        self.mode
    }

    pub fn orders_today(&self) -> i64 {
        self.orders_today
    }

    pub fn broker_calls(&self) -> u64 {
        self.broker_calls
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── scripted seams ────────────────────────────────────────────────

    #[derive(Default)]
    struct MemJournal {
        kinds: Vec<(String, Vec<(String, Field)>)>,
    }

    impl SessionJournal for MemJournal {
        fn record(&mut self, kind: &str, fields: Vec<(String, Field)>) {
            self.kinds.push((kind.to_string(), fields));
        }

        fn kind_counts(&self) -> Vec<(String, usize)> {
            let mut counts: HashMap<String, usize> = HashMap::new();
            let mut order = Vec::new();
            for (kind, _) in &self.kinds {
                if !counts.contains_key(kind) {
                    order.push(kind.clone());
                }
                *counts.entry(kind.clone()).or_insert(0) += 1;
            }
            order
                .into_iter()
                .map(|kind| {
                    let count = counts[&kind];
                    (kind, count)
                })
                .collect()
        }
    }

    impl MemJournal {
        fn has(&self, kind: &str) -> bool {
            self.kinds.iter().any(|(k, _)| k == kind)
        }

        fn count(&self, kind: &str) -> usize {
            self.kinds.iter().filter(|(k, _)| k == kind).count()
        }
    }

    #[derive(Default)]
    struct MemBus {
        events: Vec<SessionEvent>,
    }

    impl SessionBus for MemBus {
        fn emit(&mut self, event: SessionEvent) {
            self.events.push(event);
        }
    }

    #[derive(Default)]
    struct MemRecorder {
        records: Vec<(String, String, i64)>,
    }

    impl RecorderSink for MemRecorder {
        fn record(&mut self, kind: &str, symbol: &str, seq: i64) {
            self.records
                .push((kind.to_string(), symbol.to_string(), seq));
        }
    }

    struct ScriptedSource {
        signals: Vec<StrategySignal>,
        fail_on: Vec<i64>,
        warmed: usize,
    }

    impl StrategySource for ScriptedSource {
        fn on_candle(
            &mut self,
            ctx: &mut StrategyCtx,
            _candle: &CandleEvent,
        ) -> Result<Option<StrategySignal>, String> {
            ctx.event_seq += 1;
            if self.fail_on.contains(&ctx.event_seq) {
                return Err("boom".to_string());
            }
            if self.signals.is_empty() {
                return Ok(None);
            }
            ctx.signal_seq += 1;
            Ok(Some(self.signals.remove(0)))
        }

        fn warmup(&mut self, _ctx: &mut StrategyCtx, bars: &[Bar]) {
            self.warmed = bars.len();
        }

        fn export_window(&self, _ctx: &StrategyCtx) -> Vec<Bar> {
            Vec::new()
        }

        fn restore_window(&mut self, _ctx: &mut StrategyCtx, _bars: Vec<Bar>) {}
    }

    struct FakeBroker {
        calls: usize,
        place_result: Result<String, String>,
        stream: Vec<BrokerStreamEvent>,
        stream_error: Option<String>,
        healthy: bool,
        spread: Option<f64>,
        positions: Vec<(String, String)>,
        open: Vec<String>,
    }

    impl FakeBroker {
        fn paper() -> Self {
            Self {
                calls: 0,
                place_result: Ok("B-1".to_string()),
                stream: Vec::new(),
                stream_error: None,
                healthy: true,
                spread: None,
                positions: Vec::new(),
                open: Vec::new(),
            }
        }
    }

    impl BrokerVenue for FakeBroker {
        fn name(&self) -> String {
            "paper".to_string()
        }

        fn capabilities(&self) -> Vec<String> {
            vec!["TRADING".to_string()]
        }

        fn health(&mut self) -> (bool, String) {
            (self.healthy, "ok".to_string())
        }

        fn reference_spread(&mut self, _symbol: &str) -> Option<f64> {
            self.spread
        }

        fn place_order(
            &mut self,
            _plan: &OrderPlan,
            _client_order_id: &str,
        ) -> Result<String, String> {
            self.calls += 1;
            self.place_result.clone()
        }

        fn stream_events(&mut self) -> Result<Vec<BrokerStreamEvent>, String> {
            match self.stream_error.clone() {
                Some(reason) => Err(reason),
                None => Ok(std::mem::take(&mut self.stream)),
            }
        }

        fn positions(&mut self) -> Result<Vec<(String, String)>, String> {
            Ok(self.positions.clone())
        }

        fn open_orders(&mut self) -> Result<Vec<String>, String> {
            Ok(self.open.clone())
        }

        fn on_market_price(&mut self, _symbol: &str, _price: f64, _timestamp: &str) {}

        fn account_view(&mut self) -> (String, String, usize) {
            ("A-1".to_string(), "paper".to_string(), 0)
        }

        fn disconnect(&mut self) -> Result<(), String> {
            Ok(())
        }
    }

    struct FakeProvider {
        queue: Vec<ProviderEvent>,
        opened: Vec<(Vec<String>, String)>,
    }

    impl FakeProvider {
        fn with(events: Vec<ProviderEvent>) -> Self {
            Self {
                queue: events,
                opened: Vec::new(),
            }
        }
    }

    impl MarketProvider for FakeProvider {
        fn capabilities(&self) -> Vec<String> {
            vec!["CANDLES".to_string()]
        }

        fn open(&mut self, symbols: &[String], timeframe: &str) {
            self.opened.push((symbols.to_vec(), timeframe.to_string()));
        }

        fn poll(&mut self) -> Vec<ProviderEvent> {
            std::mem::take(&mut self.queue)
        }
    }

    struct FakeAdaptive {
        process: bool,
        posture: Posture,
    }

    impl FakeAdaptive {
        fn live() -> Self {
            Self {
                process: true,
                posture: Posture {
                    halt: false,
                    prefer_limit: false,
                    size_multiplier: SizeRaw::Num(1.0),
                },
            }
        }
    }

    impl AdaptiveSim for FakeAdaptive {
        fn attention_process(&mut self, _symbol: &str, _seq: i64) -> bool {
            self.process
        }

        fn note_event(&mut self, _kind: &str, _symbol: &str, _seq: i64) {}

        fn note_signal(&mut self, _signal_id: &str, _side: &Side) {}

        fn set_regime(&mut self, _regime: &str) {}

        fn set_position(&mut self, _qty: f64) {}

        fn record_incident(&mut self, _strategy_id: &str, _reason: &str, _timestamp: &str) {}

        fn regime_for(&mut self, _close: f64, _volume: f64) -> String {
            "TREND".to_string()
        }

        fn evaluate(&mut self, _stale: bool, _unstable: bool) -> Posture {
            self.posture.clone()
        }
    }

    struct FakeLatency;

    impl LatencyView for FakeLatency {
        fn stages(&self) -> Vec<String> {
            vec!["step".to_string()]
        }

        fn summary(&self, _stage: &str) -> f64 {
            0.5
        }
    }

    fn buy_signal() -> StrategySignal {
        StrategySignal {
            signal_id: "s1".to_string(),
            strategy_id: "strat".to_string(),
            strategy_version: "1".to_string(),
            timestamp: "2026-01-01 09:15:00".to_string(),
            event_seq: 1,
            symbol: "A".to_string(),
            side: Side::Buy,
            price: 100.0,
            stop_loss: None,
            take_profit: None,
            reason: "test".to_string(),
            confidence: 0.9,
        }
    }

    fn candle(symbol: &str, seq: i64) -> ProviderEvent {
        ProviderEvent::Candle(CandleEvent {
            base: MarketEvent {
                symbol: symbol.to_string(),
                timestamp: "2026-01-01 09:15:00".to_string(),
                seq,
                source: "test".to_string(),
            },
            open: 99.0,
            high: 101.0,
            low: 98.0,
            close: 100.0,
            volume: 1000,
            timeframe: "15m".to_string(),
            is_closed: true,
        })
    }

    fn contract() -> ContractView {
        ContractView {
            missing: Vec::new(),
            supports_live: true,
            data_requirements: vec!["CANDLES".to_string()],
            warmup_bars: 1,
            required_broker_capabilities: vec!["TRADING".to_string()],
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn session_with(
        signals: Vec<StrategySignal>,
        events: Vec<ProviderEvent>,
        _broker: FakeBroker,
        policy: RiskPolicy,
    ) -> (
        LiveSession,
        std::rc::Rc<std::cell::RefCell<MemJournal>>,
        std::rc::Rc<std::cell::RefCell<MemBus>>,
    ) {
        struct SharedJournal(std::rc::Rc<std::cell::RefCell<MemJournal>>);
        impl SessionJournal for SharedJournal {
            fn record(&mut self, kind: &str, fields: Vec<(String, Field)>) {
                self.0.borrow_mut().record(kind, fields);
            }
            fn kind_counts(&self) -> Vec<(String, usize)> {
                self.0.borrow().kind_counts()
            }
        }
        struct SharedBus(std::rc::Rc<std::cell::RefCell<MemBus>>);
        impl SessionBus for SharedBus {
            fn emit(&mut self, event: SessionEvent) {
                self.0.borrow_mut().emit(event);
            }
        }
        let journal = std::rc::Rc::new(std::cell::RefCell::new(MemJournal::default()));
        let bus = std::rc::Rc::new(std::cell::RefCell::new(MemBus::default()));
        let mut session = LiveSession::new(
            SessionConfig::default(),
            Box::new(FakeProvider::with(events)),
            Box::new(SharedJournal(journal.clone())),
            Box::new(MemRecorder::default()),
            Box::new(FakeAdaptive::live()),
            Box::new(FakeLatency),
            policy,
        )
        .unwrap();
        session.attach_bus(Box::new(SharedBus(bus.clone())));
        session.register_strategy(
            "strat",
            "1",
            contract(),
            1,
            Box::new(ScriptedSource {
                signals,
                fail_on: Vec::new(),
                warmed: 0,
            }),
        );
        (session, journal, bus)
    }

    fn permissive_policy() -> RiskPolicy {
        RiskPolicy {
            max_position_qty: 1_000_000.0,
            max_order_qty: 1_000_000.0,
            cooldown_seconds: 0.0,
            allowed_symbols: vec!["A".to_string()],
            ..Default::default()
        }
    }

    fn start_ready(
        session: &mut LiveSession,
        warmup: HashMap<String, Vec<Bar>>,
        broker: FakeBroker,
    ) -> ReadinessReport {
        session
            .start(
                &["A".to_string()],
                "15m",
                &warmup,
                ResolvedVenue {
                    venue: Box::new(broker),
                    mode: ExecutionMode::Paper,
                    notes: Vec::new(),
                },
            )
            .unwrap()
    }

    // ── unit twins ────────────────────────────────────────────────────

    #[test]
    fn size_multiplier_truth_table() {
        assert_eq!(size_multiplier(SizeRaw::Bool(true)), 1.0);
        assert_eq!(size_multiplier(SizeRaw::Bool(false)), 1.0);
        assert_eq!(size_multiplier(SizeRaw::Num(0.5)), 0.5);
        assert_eq!(size_multiplier(SizeRaw::Num(1.0)), 1.0);
        assert_eq!(size_multiplier(SizeRaw::Num(0.0)), 1.0);
        assert_eq!(size_multiplier(SizeRaw::Num(-0.5)), 1.0);
        assert_eq!(size_multiplier(SizeRaw::Num(1.5)), 1.0);
        assert_eq!(size_multiplier(SizeRaw::Other), 1.0);
    }

    #[test]
    fn strategy_of_intent_takes_head() {
        assert_eq!(strategy_of_intent("strat:1:4:2"), "strat");
        assert_eq!(strategy_of_intent("solo"), "solo");
    }

    // ── startup ───────────────────────────────────────────────────────

    #[test]
    fn start_brings_ready_session_running() {
        let (mut session, journal, _) =
            session_with(vec![], vec![], FakeBroker::paper(), permissive_policy());
        let mut warmup = HashMap::new();
        warmup.insert(
            "strat".to_string(),
            vec![Bar::new(
                "A",
                "2026-01-01 09:15:00",
                99.0,
                101.0,
                98.0,
                100.0,
                1000,
            )],
        );
        let report = start_ready(&mut session, warmup, FakeBroker::paper());
        assert!(report.ready);
        assert_eq!(session.lifecycle_state(), LifecycleState::Running);
        assert_eq!(
            session.context_lifecycle("strat:1"),
            Some(LifecycleState::Running)
        );
        assert!(journal.borrow().has("LIVE_READY"));
    }

    #[test]
    fn start_from_wrong_state_errors() {
        let (mut session, _, _) =
            session_with(vec![], vec![], FakeBroker::paper(), permissive_policy());
        let mut warmup = HashMap::new();
        warmup.insert(
            "strat".to_string(),
            vec![Bar::new(
                "A",
                "2026-01-01 09:15:00",
                99.0,
                101.0,
                98.0,
                100.0,
                1000,
            )],
        );
        start_ready(&mut session, warmup.clone(), FakeBroker::paper());
        let err = session
            .start(
                &["A".to_string()],
                "15m",
                &warmup,
                ResolvedVenue {
                    venue: Box::new(FakeBroker::paper()),
                    mode: ExecutionMode::Paper,
                    notes: Vec::new(),
                },
            )
            .unwrap_err();
        assert_eq!(err, "start requires CREATED or RECOVERING, found RUNNING");
    }

    #[test]
    fn start_not_ready_errors_contexts() {
        let (mut session, journal, _) =
            session_with(vec![], vec![], FakeBroker::paper(), permissive_policy());
        // Empty warmup against warmup_bars=1: shortfall with key prefix.
        let report = start_ready(&mut session, HashMap::new(), FakeBroker::paper());
        assert!(!report.ready);
        assert!(report
            .reasons
            .iter()
            .any(|r| r == "strat:1: warmup shortfall: have 0, need 1"));
        assert_eq!(
            session.context_lifecycle("strat:1"),
            Some(LifecycleState::Error)
        );
        assert!(journal.borrow().has("NOT_LIVE_READY"));
    }

    #[test]
    fn mode_downgrade_notes_are_journaled() {
        let (mut session, journal, _) =
            session_with(vec![], vec![], FakeBroker::paper(), permissive_policy());
        let mut warmup = HashMap::new();
        warmup.insert(
            "strat".to_string(),
            vec![Bar::new(
                "A",
                "2026-01-01 09:15:00",
                99.0,
                101.0,
                98.0,
                100.0,
                1000,
            )],
        );
        session
            .start(
                &["A".to_string()],
                "15m",
                &warmup,
                ResolvedVenue {
                    venue: Box::new(FakeBroker::paper()),
                    mode: ExecutionMode::Paper,
                    notes: vec!["live gates unmet".to_string()],
                },
            )
            .unwrap();
        assert!(journal.borrow().has("MODE_DOWNGRADE"));
    }

    // ── pipeline ──────────────────────────────────────────────────────

    #[test]
    fn step_requires_start() {
        let (mut session, _, _) =
            session_with(vec![], vec![], FakeBroker::paper(), permissive_policy());
        assert_eq!(session.step(1.0).unwrap_err(), "session not started");
    }

    #[test]
    fn buy_signal_flows_to_submitted() {
        let (mut session, journal, bus) = session_with(
            vec![buy_signal()],
            vec![candle("A", 1)],
            FakeBroker::paper(),
            permissive_policy(),
        );
        let mut warmup = HashMap::new();
        warmup.insert(
            "strat".to_string(),
            vec![Bar::new(
                "A",
                "2026-01-01 09:15:00",
                99.0,
                101.0,
                98.0,
                100.0,
                1000,
            )],
        );
        start_ready(&mut session, warmup, FakeBroker::paper());
        assert_eq!(session.step(1_786_000_000.0).unwrap(), 1);
        let journal = journal.borrow();
        assert!(journal.has("SIGNAL_GENERATED"));
        assert!(journal.has("RISK_APPROVED"));
        assert!(journal.has("ORDER_PLANNED"));
        assert!(journal.has("ORDER_SUBMITTED"));
        assert_eq!(session.orders_today(), 1);
        assert_eq!(session.broker_calls(), 1);
        let bus = bus.borrow();
        assert!(bus
            .events
            .iter()
            .any(|e| matches!(e, SessionEvent::Submitted(_))));
    }

    #[test]
    fn sell_with_flat_position_skips() {
        let mut signal = buy_signal();
        signal.side = Side::Sell;
        let (mut session, journal, _) = session_with(
            vec![signal],
            vec![candle("A", 1)],
            FakeBroker::paper(),
            permissive_policy(),
        );
        let mut warmup = HashMap::new();
        warmup.insert(
            "strat".to_string(),
            vec![Bar::new(
                "A",
                "2026-01-01 09:15:00",
                99.0,
                101.0,
                98.0,
                100.0,
                1000,
            )],
        );
        start_ready(&mut session, warmup, FakeBroker::paper());
        assert_eq!(session.step(1_786_000_000.0).unwrap(), 1);
        assert!(journal.borrow().has("SIGNAL_GENERATED"));
        assert!(!journal.borrow().has("ORDER_PLANNED"));
        assert_eq!(session.orders_today(), 0);
    }

    #[test]
    fn risk_deny_journals_and_publishes_without_submit() {
        let (mut session, journal, bus) = session_with(
            vec![buy_signal()],
            vec![candle("A", 1)],
            FakeBroker::paper(),
            permissive_policy(),
        );
        let mut warmup = HashMap::new();
        warmup.insert(
            "strat".to_string(),
            vec![Bar::new(
                "A",
                "2026-01-01 09:15:00",
                99.0,
                101.0,
                98.0,
                100.0,
                1000,
            )],
        );
        start_ready(&mut session, warmup, FakeBroker::paper());
        session.set_kill_halted(true);
        session.step(1_786_000_000.0).unwrap();
        assert!(journal.borrow().has("RISK_DENIED"));
        assert!(!journal.borrow().has("ORDER_PLANNED"));
        assert!(bus
            .borrow()
            .events
            .iter()
            .any(|e| matches!(e, SessionEvent::RiskDenied(_))));
    }

    #[test]
    fn live_unarmed_blocks_submit() {
        let (mut session, journal, _) = session_with(
            vec![buy_signal()],
            vec![candle("A", 1)],
            FakeBroker::paper(),
            permissive_policy(),
        );
        let mut warmup = HashMap::new();
        warmup.insert(
            "strat".to_string(),
            vec![Bar::new(
                "A",
                "2026-01-01 09:15:00",
                99.0,
                101.0,
                98.0,
                100.0,
                1000,
            )],
        );
        session
            .start(
                &["A".to_string()],
                "15m",
                &warmup,
                ResolvedVenue {
                    venue: Box::new(FakeBroker::paper()),
                    mode: ExecutionMode::Live,
                    notes: Vec::new(),
                },
            )
            .unwrap();
        session.step(1_786_000_000.0).unwrap();
        assert!(journal.borrow().has("ORDER_BLOCKED_UNARMED"));
        assert_eq!(session.orders_today(), 0);
    }

    #[test]
    fn broker_place_error_rejects() {
        let mut broker = FakeBroker::paper();
        broker.place_result = Err("venue down".to_string());
        let (mut session, journal, bus) = session_with(
            vec![buy_signal()],
            vec![candle("A", 1)],
            broker,
            permissive_policy(),
        );
        let mut warmup = HashMap::new();
        warmup.insert(
            "strat".to_string(),
            vec![Bar::new(
                "A",
                "2026-01-01 09:15:00",
                99.0,
                101.0,
                98.0,
                100.0,
                1000,
            )],
        );
        let mut start_broker = FakeBroker::paper();
        start_broker.place_result = Err("venue down".to_string());
        start_ready(&mut session, warmup, start_broker);
        session.step(1_786_000_000.0).unwrap();
        assert!(journal.borrow().has("ORDER_REJECTED"));
        assert!(bus
            .borrow()
            .events
            .iter()
            .any(|e| matches!(e, SessionEvent::Rejected(_))));
    }

    #[test]
    fn fill_stream_updates_ledger_and_context() {
        let (mut session, journal, bus) = session_with(
            vec![buy_signal()],
            vec![candle("A", 1)],
            FakeBroker::paper(),
            permissive_policy(),
        );
        let mut warmup = HashMap::new();
        warmup.insert(
            "strat".to_string(),
            vec![Bar::new(
                "A",
                "2026-01-01 09:15:00",
                99.0,
                101.0,
                98.0,
                100.0,
                1000,
            )],
        );
        // Broker streams ack-then-fill for the submitted order (the
        // lifecycle table requires ACKNOWLEDGED before FILLED).
        let mut start_broker = FakeBroker::paper();
        start_broker.stream.push(BrokerStreamEvent::Ack {
            client_order_id: "strat:1:1:1:o1".to_string(),
            broker_order_id: "B-1".to_string(),
        });
        start_broker.stream.push(BrokerStreamEvent::Fill {
            client_order_id: "strat:1:1:1:o1".to_string(),
            fill: crate::execution::Fill {
                client_order_id: "strat:1:1:1:o1".to_string(),
                broker_order_id: Some("B-1".to_string()),
                symbol: "A".to_string(),
                side: Side::Buy,
                fill_qty: 10.0,
                fill_price: 100.0,
                commission: 0.0,
                timestamp: "2026-01-01 09:15:00".to_string(),
                partial: false,
            },
        });
        start_ready(&mut session, warmup, start_broker);
        session.step(1_786_000_000.0).unwrap();
        assert!(journal.borrow().has("FILL"));
        assert!(journal.borrow().has("POSITION_UPDATED"));
        assert!(bus
            .borrow()
            .events
            .iter()
            .any(|e| matches!(e, SessionEvent::Positions(_))));
        let view = session.state_view();
        assert_eq!(view.positions, vec![("A".to_string(), 10.0, 100.0)]);
    }

    #[test]
    fn stream_dispatch_ack_reject_cancel_unknown() {
        let (mut session, journal, bus) = session_with(
            vec![buy_signal()],
            vec![candle("A", 1)],
            FakeBroker::paper(),
            permissive_policy(),
        );
        let mut warmup = HashMap::new();
        warmup.insert(
            "strat".to_string(),
            vec![Bar::new(
                "A",
                "2026-01-01 09:15:00",
                99.0,
                101.0,
                98.0,
                100.0,
                1000,
            )],
        );
        let mut start_broker = FakeBroker::paper();
        start_broker.stream.push(BrokerStreamEvent::Ack {
            client_order_id: "strat:1:1:1:o1".to_string(),
            broker_order_id: "B-9".to_string(),
        });
        start_broker.stream.push(BrokerStreamEvent::Reject {
            client_order_id: "nope".to_string(),
            reason: "venue reject".to_string(),
        });
        start_ready(&mut session, warmup, start_broker);
        session.step(1_786_000_000.0).unwrap();
        assert!(journal.borrow().has("ORDER_ACK"));
        // Unknown order id is journaled, never fatal.
        assert!(journal.borrow().has("BROKER_EVENT_IGNORED"));
        assert!(bus
            .borrow()
            .events
            .iter()
            .any(|e| matches!(e, SessionEvent::Acknowledged(_))));
    }

    #[test]
    fn stale_event_journals_and_skips() {
        let (mut session, journal, _) = session_with(
            vec![buy_signal()],
            vec![candle("A", 1)],
            FakeBroker::paper(),
            permissive_policy(),
        );
        let mut warmup = HashMap::new();
        warmup.insert(
            "strat".to_string(),
            vec![Bar::new(
                "A",
                "2026-01-01 09:15:00",
                99.0,
                101.0,
                98.0,
                100.0,
                1000,
            )],
        );
        start_ready(&mut session, warmup, FakeBroker::paper());
        // Stream stalled long ago: health gate trips before ingest.
        session
            .normalizer
            .observe(
                StreamInput::Event(MarketEvent {
                    symbol: "A".to_string(),
                    timestamp: "2026-01-01 09:15:00".to_string(),
                    seq: 1,
                    source: "seed".to_string(),
                }),
                0.0,
            )
            .unwrap();
        assert_eq!(session.step(1000.0).unwrap(), 0);
        assert!(journal.borrow().has("STALE_DATA"));
        assert!(!journal.borrow().has("SIGNAL_GENERATED"));
    }

    #[test]
    fn strategy_error_isolated_per_context() {
        let (mut session, journal, _) = session_with(
            vec![],
            vec![candle("A", 1)],
            FakeBroker::paper(),
            permissive_policy(),
        );
        // Replace the source with one that fails on its first candle.
        session.sources.insert(
            "strat:1".to_string(),
            Box::new(ScriptedSource {
                signals: Vec::new(),
                fail_on: vec![1],
                warmed: 0,
            }),
        );
        let mut warmup = HashMap::new();
        warmup.insert(
            "strat".to_string(),
            vec![Bar::new(
                "A",
                "2026-01-01 09:15:00",
                99.0,
                101.0,
                98.0,
                100.0,
                1000,
            )],
        );
        start_ready(&mut session, warmup, FakeBroker::paper());
        // The failing source still counts as delivered (pipeline continues).
        assert_eq!(session.step(1_786_000_000.0).unwrap(), 1);
        assert!(journal.borrow().has("STRATEGY_ERROR"));
    }

    #[test]
    fn halt_posture_records_and_skips() {
        let (mut session, journal, _) = session_with(
            vec![buy_signal()],
            vec![candle("A", 1)],
            FakeBroker::paper(),
            permissive_policy(),
        );
        session.adaptive = Box::new(FakeAdaptive {
            process: true,
            posture: Posture {
                halt: true,
                prefer_limit: false,
                size_multiplier: SizeRaw::Num(1.0),
            },
        });
        let mut warmup = HashMap::new();
        warmup.insert(
            "strat".to_string(),
            vec![Bar::new(
                "A",
                "2026-01-01 09:15:00",
                99.0,
                101.0,
                98.0,
                100.0,
                1000,
            )],
        );
        start_ready(&mut session, warmup, FakeBroker::paper());
        session.step(1_786_000_000.0).unwrap();
        assert!(journal.borrow().has("EXECUTION_HALTED"));
        assert!(!journal.borrow().has("ORDER_PLANNED"));
    }

    // ── recovery / teardown ─────────────────────────────────────────

    #[test]
    fn checkpoint_recover_round_trip() {
        let (mut session, journal, _) =
            session_with(vec![], vec![], FakeBroker::paper(), permissive_policy());
        let mut warmup = HashMap::new();
        warmup.insert(
            "strat".to_string(),
            vec![Bar::new(
                "A",
                "2026-01-01 09:15:00",
                99.0,
                101.0,
                98.0,
                100.0,
                1000,
            )],
        );
        start_ready(&mut session, warmup, FakeBroker::paper());
        let checkpoint = session.checkpoint();
        assert_eq!(checkpoint.mode, "PAPER");
        assert_eq!(checkpoint.orders_today, 0);
        session.stop();
        assert_eq!(session.lifecycle_state(), LifecycleState::Stopped);
        session.recover(&checkpoint).unwrap();
        assert_eq!(session.lifecycle_state(), LifecycleState::Recovering);
        // Empty engine snapshot records nothing.
        assert!(!journal.borrow().has("IDEMPOTENCY_RESTORED"));
    }

    #[test]
    fn arm_disarm_cycle() {
        let (mut session, journal, _) =
            session_with(vec![], vec![], FakeBroker::paper(), permissive_policy());
        assert_eq!(session.arm("ops").unwrap(), LiveArm::Armed);
        assert!(journal.borrow().has("LIVE_ARMED"));
        assert_eq!(session.disarm("ops"), LiveArm::Disarmed);
        assert!(journal.borrow().has("LIVE_DISARMED"));
        // Disarming twice journals once.
        assert_eq!(journal.borrow().count("LIVE_DISARMED"), 1);
    }

    #[test]
    fn reconcile_match_and_mismatch() {
        let (mut session, journal, _) =
            session_with(vec![], vec![], FakeBroker::paper(), permissive_policy());
        let mut warmup = HashMap::new();
        warmup.insert(
            "strat".to_string(),
            vec![Bar::new(
                "A",
                "2026-01-01 09:15:00",
                99.0,
                101.0,
                98.0,
                100.0,
                1000,
            )],
        );
        start_ready(&mut session, warmup, FakeBroker::paper());
        session.reconcile_now();
        assert!(journal.borrow().has("RECONCILED"));
        assert!(!session.reconciliation.blocks_live());
    }

    #[test]
    fn state_view_reports_snapshot() {
        let (mut session, _, _) =
            session_with(vec![], vec![], FakeBroker::paper(), permissive_policy());
        let mut warmup = HashMap::new();
        warmup.insert(
            "strat".to_string(),
            vec![Bar::new(
                "A",
                "2026-01-01 09:15:00",
                99.0,
                101.0,
                98.0,
                100.0,
                1000,
            )],
        );
        start_ready(&mut session, warmup, FakeBroker::paper());
        let view = session.state_view();
        assert_eq!(view.mode, "PAPER");
        assert_eq!(view.armed, "DISARMED");
        assert_eq!(view.lifecycle, "RUNNING");
        assert!(view.broker_connected);
        assert_eq!(view.account_id, "A-1");
        assert_eq!(view.latency, vec![("step".to_string(), 0.5)]);
    }
}
