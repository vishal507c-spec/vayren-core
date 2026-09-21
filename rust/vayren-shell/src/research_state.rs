//! Research workspace native view-model — pure, headless-testable UI state
//! (AI_ENTRY.md §1: Rust owns view-model + interaction state; Slint renders
//! bound properties only).
//!
//! This is the presentation-side model of the Research workstation. It never
//! computes financial results: every value arrives via
//! [`ResearchState::apply_result`] (the Python research engine remains the
//! sole authority) or via [`load_snapshot`] which reads one persisted engine
//! bundle produced by that same engine. With no result the model renders an
//! honest empty state — it never fabricates numbers.
//!
//! Responsive contract: layout breakpoints are pure functions of the logical
//! viewport width ([`layout_mode`]); Slint recomposes structure per mode
//! (never shrinks content). Rust mirrors the thresholds as constants so the
//! mapping is unit-tested; the `.slint` literals must stay in sync.
//!
//! State-consistency invariants (never regress):
//! - no selection coexists with a "no strategy" workspace;
//! - completed results render only for the executed fingerprint — any config
//!   edit surfaces STALE instead of presenting old numbers as current;
//! - RUN stays inert until the engine bridge is wired (`engine_wired`).

use crate::lab::Tone;
use serde_json::Value;
use std::collections::VecDeque;
use std::path::{Path, PathBuf};

// ── responsive breakpoints (logical px; keep ui/research.slint in sync) ──

/// Three-region composition: navigator + workspace + inspector.
pub const WIDE_MIN: f32 = 1180.0;
/// Two-region composition: navigator + workspace, inspector as drawer.
pub const MEDIUM_MIN: f32 = 760.0;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LayoutMode {
    Wide,
    Medium,
    Narrow,
}

/// Pure breakpoint mapping — the single authority both Rust tests and the
/// Slint conditions implement.
pub fn layout_mode(width: f32) -> LayoutMode {
    if width >= WIDE_MIN {
        LayoutMode::Wide
    } else if width >= MEDIUM_MIN {
        LayoutMode::Medium
    } else {
        LayoutMode::Narrow
    }
}

// ── run lifecycle (mirrors the engine vocabulary) ──

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum ResearchRun {
    #[default]
    NoExperiment,
    Draft,
    Ready,
    Validating,
    Running,
    Analyzing,
    ValidatingResult,
    Completed,
    Failed,
    Cancelled,
    Stale,
    Invalid,
    NoData,
}

impl ResearchRun {
    pub fn label(self) -> &'static str {
        match self {
            ResearchRun::NoExperiment => "○ NO EXPERIMENT",
            ResearchRun::Draft => "○ DRAFT",
            ResearchRun::Ready => "● READY",
            ResearchRun::Validating => "● VALIDATING…",
            ResearchRun::Running => "● RUNNING…",
            ResearchRun::Analyzing => "● ANALYZING…",
            ResearchRun::ValidatingResult => "● VALIDATING…",
            ResearchRun::Completed => "✓ COMPLETED",
            ResearchRun::Failed => "✕ FAILED",
            ResearchRun::Cancelled => "○ CANCELLED",
            ResearchRun::Stale => "◐ STALE",
            ResearchRun::Invalid => "✕ INVALID",
            ResearchRun::NoData => "○ NO DATA",
        }
    }

    pub fn tone(self) -> Tone {
        match self {
            ResearchRun::Completed => Tone::Positive,
            ResearchRun::Running
            | ResearchRun::Analyzing
            | ResearchRun::Validating
            | ResearchRun::ValidatingResult
            | ResearchRun::Stale => Tone::Warning,
            ResearchRun::Failed | ResearchRun::Invalid => Tone::Negative,
            _ => Tone::Muted,
        }
    }

    /// Terminal outcomes keep their stored evidence immutable.
    pub fn is_terminal(self) -> bool {
        matches!(
            self,
            ResearchRun::Completed
                | ResearchRun::Failed
                | ResearchRun::Cancelled
                | ResearchRun::Invalid
                | ResearchRun::NoData
        )
    }
}

// ── model ──

pub const RESEARCH_TABS: [&str; 8] = [
    "SIGNALS",
    "TRADES",
    "ROBUSTNESS",
    "COMPARE",
    "DATA",
    "VALIDATION",
    "REPORT",
    "VISUALS",
];

pub const TIMEFRAME_OPTIONS: [&str; 11] = [
    "1m", "3m", "5m", "15m", "30m", "45m", "1h", "2h", "4h", "1D", "1W",
];
pub const SIDE_OPTIONS: [&str; 3] = ["LONG", "SHORT", "BOTH"];
pub const DIRECTION_OPTIONS: [&str; 3] = ["ANY", "LONG", "SHORT"];

/// Presentation cap for long tables (the label always states the true
/// analyzed count — the cap is a viewport policy).
pub const SIGNALS_VIEW_CAP: usize = 500;
pub const TRADES_VIEW_CAP: usize = 500;
pub const LOG_VIEW_CAP: usize = 200;
/// Cap for stats notes and the why-layer (viewport policy; engine data is
/// never truncated at source).
pub const STATS_NOTES_CAP: usize = 4;

#[derive(Debug, Clone, PartialEq)]
pub struct ResearchStrategy {
    pub name: String,
    pub description: String,
    pub version: String,
}

/// One editable text field of the experiment configuration form.
#[derive(Debug, Clone, PartialEq)]
pub struct ConfigField {
    pub key: String,
    pub label: String,
    pub value: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Metric {
    pub label: String,
    pub value: String,
    pub tone: Tone,
    pub emphasized: bool,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct SignalRow {
    pub time: String,
    pub symbol: String,
    pub tf: String,
    pub side: String,
    pub price: String,
    pub event: String,
    pub strategy: String,
    pub exp: String,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct ResearchTradeRow {
    pub no: String,
    pub entry: String,
    pub exit: String,
    pub side: String,
    pub qty: String,
    pub pnl: String,
    pub reason: String,
    pub hold: String,
    pub pnl_tone: Tone,
}

#[derive(Debug, Clone, PartialEq)]
pub struct RobustRow {
    pub test: String,
    pub input: String,
    pub stability: String,
    pub stability_tone: Tone,
    pub evidence: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct KvRow {
    pub label: String,
    pub value: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CompareRow {
    pub exp: String,
    pub strategy: String,
    pub status: String,
    pub detail: String,
}

/// One validation dimension (OOS / CPCV / PBO / …) with its real engine
/// status plus a short pre-formatted evidence detail. The status drives the
/// tone; the detail carries the number — never the other way round.
#[derive(Debug, Clone, PartialEq)]
pub struct EvidenceDim {
    pub name: String,
    pub status: String,
    pub status_tone: Tone,
    pub detail: String,
}

/// One line of the “why” layer: a real engine concern (fail / warning /
/// limitation) behind the current evidence verdict.
#[derive(Debug, Clone, PartialEq)]
pub struct EvidenceWhy {
    pub kind: String,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ExperimentItem {
    pub id: String,
    pub strategy: String,
    pub status_label: String,
    pub status_tone: Tone,
}

/// Engine-fed results for ONE configuration fingerprint. All display strings
/// arrive pre-formatted from the bridge/snapshot loader.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct ResearchResults {
    pub experiment_id: String,
    pub metrics: Vec<Metric>,
    pub signal_total: usize,
    pub trade_total: usize,
    pub signals: Vec<SignalRow>,
    pub trades: Vec<ResearchTradeRow>,
    pub robustness: Vec<RobustRow>,
    pub validation_status: String,
    pub validation_summary: String,
    pub validation_rows: Vec<KvRow>,
    pub quality_rows: Vec<KvRow>,
    pub inspector_rows: Vec<KvRow>,
    pub compare_rows: Vec<CompareRow>,
    pub compare_verdict: String,
    pub report_sections: Vec<KvRow>,
    pub conclusion: String,
    pub fingerprint_line: String,
    pub executed_at: String,
    pub dataset_version: String,
    /// Honest evidence verdict derived from the engine validation status
    /// (PASS / WEAK / FAIL / INCONCLUSIVE) plus its grade.
    pub evidence_grade: String,
    /// Real engine concerns behind the verdict (fails, warns, limitations).
    pub evidence_why: Vec<EvidenceWhy>,
    /// Per-dimension validation status with short evidence details.
    pub evidence_dims: Vec<EvidenceDim>,
    /// Statistical-significance facts (CI, p-value, t-stat, Cohen's d).
    pub stats_rows: Vec<KvRow>,
    /// Monte-Carlo path facts (p5/p50/p95, probability of profit).
    pub montecarlo_rows: Vec<KvRow>,
    /// Benchmark comparison facts (buy & hold excess return).
    pub benchmark_rows: Vec<KvRow>,
    /// Grouped inspector sections (IDENTITY / EXECUTION / DATA / ENGINE /
    /// VALIDATION / EVIDENCE).
    pub inspector_groups: Vec<InspectorGroup>,
}

/// The single source of Research presentation state.
#[derive(Debug, Clone, PartialEq)]
pub struct ResearchState {
    pub strategies: Vec<ResearchStrategy>,
    pub selected: Option<usize>,
    pub fields: Vec<ConfigField>,
    pub timeframe_idx: usize,
    pub side_idx: usize,
    pub direction_idx: usize,
    pub hypothesis: String,
    pub question: String,
    pub expected_effect: String,
    pub create_error: String,
    pub run: ResearchRun,
    /// Fingerprint of the configuration the current results belong to.
    pub results_fingerprint: Option<String>,
    pub results: Option<ResearchResults>,
    /// True when completed results no longer match the live form.
    pub stale: bool,
    pub tab: usize,
    pub inspector_open: bool,
    pub narrow_view: usize,
    pub signal_filter: String,
    /// Progressive disclosure: hypothesis workbench expanded for editing.
    pub hypothesis_open: bool,
    /// Progressive disclosure: the “why” layer behind the evidence verdict.
    pub why_open: bool,
    /// Progressive disclosure: technical diagnostics (stats / montecarlo /
    /// benchmark / fingerprints).
    pub technical_open: bool,
    pub experiments: Vec<ExperimentItem>,
    pub selected_experiment: Option<usize>,
    pub log: Vec<String>,
    /// Engine bridge present (false until wired — RUN stays honestly inert).
    pub engine_wired: bool,
    /// Human reason the run control is inert (empty when runnable).
    pub run_blocked_reason: String,
    /// Accepted UI intents waiting for the Python host to execute (JSON).
    pub host_actions: VecDeque<String>,
    /// True once the user edits the form — host snapshots then stop
    /// overwriting the input fields with backend defaults.
    pub form_edited: bool,
}

impl Default for ResearchState {
    fn default() -> Self {
        ResearchState {
            strategies: Vec::new(),
            selected: None,
            fields: default_fields(),
            timeframe_idx: 3,
            side_idx: 0,
            direction_idx: 0,
            hypothesis: String::new(),
            question: String::new(),
            expected_effect: String::new(),
            create_error: String::new(),
            run: ResearchRun::NoExperiment,
            results_fingerprint: None,
            results: None,
            stale: false,
            tab: 0,
            inspector_open: false,
            narrow_view: 0,
            signal_filter: String::new(),
            hypothesis_open: false,
            why_open: false,
            technical_open: false,
            experiments: Vec::new(),
            selected_experiment: None,
            log: Vec::new(),
            engine_wired: false,
            run_blocked_reason: "Engine bridge pending — execution arrives in a later slice."
                .to_string(),
            host_actions: VecDeque::new(),
            form_edited: false,
        }
    }
}

fn default_fields() -> Vec<ConfigField> {
    [
        ("universe", "UNIVERSE", "NIFTY 500"),
        ("symbols", "SYMBOLS", "RELIANCE, TCS"),
        ("start", "START", "2023-01-01"),
        ("end", "END", "2026-09-12"),
        ("capital", "CAPITAL", "1000000"),
        ("slip", "SLIPPAGE %", "0.02"),
        ("comm", "COMMISSION %", "0.03"),
        ("params", "PARAMETERS", ""),
    ]
    .iter()
    .map(|(key, label, value)| ConfigField {
        key: (*key).to_string(),
        label: (*label).to_string(),
        value: (*value).to_string(),
    })
    .collect()
}

fn field_of(fields: &[ConfigField], key: &str) -> String {
    fields
        .iter()
        .find(|f| f.key == key)
        .map_or_else(String::new, |f| f.value.clone())
}

impl ResearchState {
    pub fn selected_strategy(&self) -> Option<&ResearchStrategy> {
        self.selected.and_then(|i| self.strategies.get(i))
    }

    pub fn select_strategy(&mut self, index: usize) -> bool {
        if index >= self.strategies.len() {
            return false;
        }
        self.selected = Some(index);
        true
    }

    /// Live configuration fingerprint: strategy + every field + selects.
    /// Any edit that changes the executed meaning flips this string, which
    /// is exactly what marks stored results STALE.
    pub fn live_fingerprint(&self) -> String {
        let strategy = self.selected_strategy().map_or("", |s| s.name.as_str());
        let mut parts = vec![
            strategy.to_string(),
            TIMEFRAME_OPTIONS
                .get(self.timeframe_idx)
                .unwrap_or(&"")
                .to_string(),
            SIDE_OPTIONS.get(self.side_idx).unwrap_or(&"").to_string(),
        ];
        for f in &self.fields {
            parts.push(format!("{}={}", f.key, f.value.trim()));
        }
        parts.join("|")
    }

    pub fn refresh_staleness(&mut self) {
        if self.run != ResearchRun::Completed {
            self.stale = false;
            return;
        }
        let Some(fp) = &self.results_fingerprint else {
            self.stale = false;
            return;
        };
        self.stale = fp != &self.live_fingerprint();
    }

    /// Edit one config field — staleness recomputes in the same pass, so a
    /// stored result can never look current after its inputs changed.
    pub fn edit_field(&mut self, key: &str, value: &str) -> bool {
        let Some(field) = self.fields.iter_mut().find(|f| f.key == key) else {
            return false;
        };
        field.value = value.to_string();
        self.form_edited = true;
        self.refresh_staleness();
        true
    }

    /// Host-side default application (bypasses the `form_edited` latch).
    fn apply_host_field(&mut self, key: &str, value: &str) {
        if self.form_edited || value.is_empty() {
            return;
        }
        if let Some(field) = self.fields.iter_mut().find(|f| f.key == key) {
            field.value = value.to_string();
        }
    }

    pub fn set_timeframe(&mut self, index: usize) -> bool {
        if index >= TIMEFRAME_OPTIONS.len() {
            return false;
        }
        self.timeframe_idx = index;
        self.refresh_staleness();
        true
    }

    pub fn set_side(&mut self, index: usize) -> bool {
        if index >= SIDE_OPTIONS.len() {
            return false;
        }
        self.side_idx = index;
        self.refresh_staleness();
        true
    }

    pub fn set_direction(&mut self, index: usize) -> bool {
        if index >= DIRECTION_OPTIONS.len() {
            return false;
        }
        self.direction_idx = index;
        true
    }

    pub fn set_hypothesis(&mut self, text: &str) {
        self.hypothesis = text.to_string();
    }

    pub fn set_question(&mut self, text: &str) {
        self.question = text.to_string();
    }

    pub fn set_effect(&mut self, text: &str) {
        self.expected_effect = text.to_string();
    }

    /// Configuration errors, in checkbox order. Empty means READY to run.
    pub fn config_errors(&self) -> Vec<String> {
        let mut errors = Vec::new();
        if self.selected.is_none() {
            errors.push("No strategy selected.".to_string());
        }
        if field_of(&self.fields, "symbols").trim().is_empty() {
            errors.push("No symbols — set a universe with at least one symbol.".to_string());
        }
        let (start, end) = (
            field_of(&self.fields, "start"),
            field_of(&self.fields, "end"),
        );
        if start.trim().is_empty() || end.trim().is_empty() {
            errors.push("Date range incomplete.".to_string());
        } else if start > end {
            errors.push("Start date is after end date.".to_string());
        }
        match field_of(&self.fields, "capital").trim().parse::<f64>() {
            Ok(v) if v > 0.0 => {}
            _ => errors.push("Initial capital must be positive.".to_string()),
        }
        for key in ["slip", "comm"] {
            let label = self
                .fields
                .iter()
                .find(|f| f.key == key)
                .map_or(key, |f| f.label.as_str());
            match field_of(&self.fields, key).trim().parse::<f64>() {
                Ok(v) if (0.0..=5.0).contains(&v) => {}
                _ => errors.push(format!("{label} must be between 0 and 5 percent.")),
            }
        }
        errors
    }

    /// Create the experiment from the live form. Requires a hypothesis —
    /// without one there is honestly nothing to create.
    pub fn create(&mut self) -> bool {
        self.create_error.clear();
        if self.hypothesis.trim().is_empty() {
            self.create_error = "Write a hypothesis before creating the experiment.".to_string();
            return false;
        }
        let errors = self.config_errors();
        if !errors.is_empty() {
            self.create_error = errors.join(" ");
            self.run = ResearchRun::Invalid;
            return false;
        }
        self.run = ResearchRun::Draft;
        true
    }

    /// Engine bridge entry point: RUN. Inert until wired — never fakes a run.
    pub fn start_run(&mut self) -> bool {
        if !self.engine_wired {
            return false;
        }
        if !matches!(
            self.run,
            ResearchRun::Draft | ResearchRun::Ready | ResearchRun::Completed
        ) || self.selected.is_none()
        {
            return false;
        }
        self.run = ResearchRun::Running;
        self.stale = false;
        true
    }

    pub fn cancel_run(&mut self) -> bool {
        if !matches!(
            self.run,
            ResearchRun::Running
                | ResearchRun::Analyzing
                | ResearchRun::Validating
                | ResearchRun::ValidatingResult
        ) {
            return false;
        }
        self.run = ResearchRun::Cancelled;
        true
    }

    /// Engine bridge entry point: attach executed results for the CURRENT
    /// configuration fingerprint.
    pub fn apply_result(&mut self, results: ResearchResults) {
        self.results_fingerprint = Some(self.live_fingerprint());
        self.results = Some(results);
        self.run = ResearchRun::Completed;
        self.stale = false;
    }

    pub fn fail_run(&mut self, message: &str) {
        self.run = ResearchRun::Failed;
        self.push_log(format!("Experiment failed — {message}"));
    }

    pub fn push_log(&mut self, line: String) {
        self.log.push(line);
        if self.log.len() > LOG_VIEW_CAP * 4 {
            let excess = self.log.len() - LOG_VIEW_CAP * 4;
            self.log.drain(..excess);
        }
    }

    pub fn set_tab(&mut self, tab: usize) {
        if tab < RESEARCH_TABS.len() {
            self.tab = tab;
        }
    }

    pub fn toggle_inspector(&mut self) {
        self.inspector_open = !self.inspector_open;
    }

    pub fn set_narrow_view(&mut self, view: usize) {
        if view < 3 {
            self.narrow_view = view;
        }
    }

    pub fn toggle_hypothesis(&mut self) {
        self.hypothesis_open = !self.hypothesis_open;
    }

    pub fn toggle_why(&mut self) {
        self.why_open = !self.why_open;
    }

    pub fn toggle_technical(&mut self) {
        self.technical_open = !self.technical_open;
    }

    pub fn set_signal_filter(&mut self, text: &str) {
        self.signal_filter = text.to_lowercase();
    }

    pub fn pick_experiment(&mut self, index: usize) -> bool {
        if index >= self.experiments.len() {
            return false;
        }
        self.selected_experiment = Some(index);
        true
    }

    // ── host bridge (legacy embed: intents out, backend facts in) ──────────────

    pub fn push_host_action(&mut self, payload: Value) {
        self.host_actions.push_back(payload.to_string());
    }

    pub fn take_action(&mut self) -> Option<String> {
        self.host_actions.pop_front()
    }

    pub fn push_front_action(&mut self, text: String) {
        self.host_actions.push_front(text);
    }

    /// Current form as the config dict the Python service expects.
    pub fn host_config_payload(&self) -> Value {
        let field = |key: &str| {
            self.fields
                .iter()
                .find(|f| f.key == key)
                .map_or_else(String::new, |f| f.value.clone())
        };
        serde_json::json!({
            "strategy_name": self.selected_strategy().map_or("", |s| s.name.as_str()),
            "universe": field("universe"),
            "symbols": field("symbols"),
            "timeframe": TIMEFRAME_OPTIONS.get(self.timeframe_idx).copied().unwrap_or("15m"),
            "start_date": field("start"),
            "end_date": field("end"),
            "side": SIDE_OPTIONS.get(self.side_idx).copied().unwrap_or("BOTH"),
            "initial_capital": field("capital"),
            "slippage_pct": field("slip"),
            "commission_pct": field("comm"),
            "parameters": field("params"),
            "hypothesis": self.hypothesis,
            "research_question": self.question,
            "expected_effect": self.expected_effect,
            "selected_experiment_id": self.selected_experiment
                .and_then(|i| self.experiments.get(i))
                .map(|e| e.id.clone()),
        })
    }

    /// Pull backend facts from one host snapshot (produced by the Python
    /// service). Presentation only — no computation; missing/mistyped keys
    /// degrade to honest absence.
    pub fn apply_host_snapshot(&mut self, v: &Value) {
        self.engine_wired = true;
        self.run_blocked_reason.clear();

        let mut strategies = Vec::new();
        if let Some(list) = v.get("strategies").and_then(|s| s.as_array()) {
            for s in list {
                strategies.push(ResearchStrategy {
                    name: str_at(s, "name"),
                    description: str_at(s, "description"),
                    version: str_at(s, "version"),
                });
            }
        }
        if !strategies.is_empty() && strategies != self.strategies {
            let keep = self
                .selected
                .and_then(|i| self.strategies.get(i))
                .map(|s| s.name.clone());
            self.strategies = strategies;
            self.selected = keep
                .as_deref()
                .and_then(|name| self.strategies.iter().position(|s| s.name == name))
                .or_else(|| (!self.strategies.is_empty()).then_some(0));
        }

        let mut experiments = Vec::new();
        if let Some(list) = v.get("experiments").and_then(|s| s.as_array()) {
            for e in list {
                let status = str_at(e, "status");
                experiments.push(ExperimentItem {
                    id: str_at(e, "id"),
                    strategy: str_at(e, "strategy"),
                    status_label: format!("● {status}"),
                    status_tone: match status.as_str() {
                        "COMPLETED" => Tone::Positive,
                        "FAILED" | "INVALID" => Tone::Negative,
                        "RUNNING" | "ANALYZING" | "VALIDATING" => Tone::Warning,
                        _ => Tone::Muted,
                    },
                });
            }
        }
        self.experiments = experiments;
        let selected_id = str_at(v, "selected_id");
        self.selected_experiment = if selected_id.is_empty() {
            None
        } else {
            self.experiments
                .iter()
                .position(|e| e.id == selected_id)
                .or(self.selected_experiment)
        };

        if let Some(defaults) = v.get("defaults").and_then(|d| d.as_object()) {
            let get = |k: &str| {
                defaults
                    .get(k)
                    .and_then(|x| x.as_str())
                    .unwrap_or("")
                    .to_string()
            };
            for (key, dk) in [
                ("universe", "universe"),
                ("symbols", "symbols"),
                ("start", "start"),
                ("end", "end"),
                ("capital", "capital"),
                ("slip", "slippage_pct"),
                ("comm", "commission_pct"),
                ("params", "parameters"),
            ] {
                self.apply_host_field(key, &get(dk));
            }
            if let Some(idx) = TIMEFRAME_OPTIONS
                .iter()
                .position(|t| *t == get("timeframe"))
            {
                if !self.form_edited {
                    self.timeframe_idx = idx;
                }
            }
            if let Some(idx) = SIDE_OPTIONS.iter().position(|s| *s == get("side")) {
                if !self.form_edited {
                    self.side_idx = idx;
                }
            }
            if self.hypothesis.is_empty() {
                self.hypothesis = get("hypothesis");
            }
            if self.question.is_empty() {
                self.question = get("research_question");
            }
            if self.expected_effect.is_empty() {
                self.expected_effect = get("expected_effect");
            }
        }

        self.log.clear();
        if let Some(lines) = v.get("log").and_then(|l| l.as_array()) {
            for line in lines {
                if let Some(text) = line.as_str() {
                    self.log.push(text.to_string());
                }
            }
        }

        let running = v.get("running").and_then(|r| r.as_bool()).unwrap_or(false);
        let bundle_ok = if let Some(bundle) = v.get("bundle") {
            let exp = bundle.get("experiment");
            let completed = exp.map_or(false, |e| str_at(e, "status") == "COMPLETED");
            if completed {
                let exp = exp.expect("checked");
                let signals = bundle.get("signals").cloned().unwrap_or(Value::Null);
                let trades = bundle.get("trades").cloned().unwrap_or(Value::Null);
                let results = results_from_snapshot(exp, &signals, &trades);
                // Staleness is a backend verdict (config fingerprint compare
                // lives in Python); the view only displays it.
                let stale_flag = v.get("stale").and_then(|s| s.as_bool()).unwrap_or(false);
                self.results_fingerprint = {
                    let fp = str_at(exp, "config_fingerprint");
                    (!fp.is_empty()).then_some(fp)
                };
                self.results = Some(results);
                self.run = if stale_flag {
                    ResearchRun::Stale
                } else {
                    ResearchRun::Completed
                };
                self.stale = stale_flag;
                true
            } else {
                false
            }
        } else {
            false
        };
        if !bundle_ok {
            if running {
                self.run = ResearchRun::Running;
            } else {
                let status = str_at(v, "status");
                self.run = match status.as_str() {
                    "DRAFT" => ResearchRun::Draft,
                    "RUNNING" | "VALIDATING" => ResearchRun::Running,
                    "ANALYZING" => ResearchRun::Analyzing,
                    "VALIDATING_RESULT" => ResearchRun::ValidatingResult,
                    "COMPLETED" => ResearchRun::Completed,
                    "FAILED" => ResearchRun::Failed,
                    "CANCELLED" => ResearchRun::Cancelled,
                    "STALE" => ResearchRun::Stale,
                    "INVALID" => ResearchRun::Invalid,
                    "NO_DATA" => ResearchRun::NoData,
                    _ => ResearchRun::NoExperiment,
                };
            }
        }
    }

    /// Display state: completed results with edited inputs present as STALE,
    /// never as current.
    pub fn effective_run(&self) -> ResearchRun {
        if self.run == ResearchRun::Completed && self.stale {
            ResearchRun::Stale
        } else {
            self.run
        }
    }

    /// Inspector facts when nothing has executed: identity + live form only
    /// (real fields, never invented values).
    pub fn default_inspector_rows(&self) -> Vec<KvRow> {
        let field = |key: &str| {
            self.fields
                .iter()
                .find(|f| f.key == key)
                .map_or("—", |f| f.value.as_str())
        };
        let selected_exp = self
            .selected_experiment
            .and_then(|i| self.experiments.get(i));
        let tf = TIMEFRAME_OPTIONS
            .get(self.timeframe_idx)
            .copied()
            .unwrap_or("—");
        let mut rows = vec![
            KvRow {
                label: "EXPERIMENT".into(),
                value: selected_exp
                    .map_or("— not created —", |e| e.id.as_str())
                    .to_string(),
            },
            KvRow {
                label: "STRATEGY".into(),
                value: self
                    .selected_strategy()
                    .map_or("—", |s| s.name.as_str())
                    .to_string(),
            },
            KvRow {
                label: "STATUS".into(),
                value: self.effective_run().label().to_string(),
            },
            KvRow {
                label: "UNIVERSE".into(),
                value: field("universe").to_string(),
            },
            KvRow {
                label: "SYMBOLS".into(),
                value: field("symbols").to_string(),
            },
            KvRow {
                label: "TIMEFRAME".into(),
                value: tf.to_string(),
            },
            KvRow {
                label: "PERIOD".into(),
                value: format!("{} → {}", field("start"), field("end")),
            },
            KvRow {
                label: "SIDE".into(),
                value: SIDE_OPTIONS
                    .get(self.side_idx)
                    .copied()
                    .unwrap_or("—")
                    .to_string(),
            },
            KvRow {
                label: "CAPITAL".into(),
                value: field("capital").to_string(),
            },
            KvRow {
                label: "COSTS".into(),
                value: format!("slip {}% · comm {}%", field("slip"), field("comm")),
            },
        ];
        if !self.hypothesis.is_empty() {
            rows.push(KvRow {
                label: "HYPOTHESIS".into(),
                value: self.hypothesis.clone(),
            });
        }
        if !self.question.is_empty() {
            rows.push(KvRow {
                label: "QUESTION".into(),
                value: self.question.clone(),
            });
        }
        rows.push(KvRow {
            label: "RESULT".into(),
            value: match self.results {
                Some(_) => "AVAILABLE".into(),
                None => "NOT RUN".into(),
            },
        });
        rows
    }

    /// Grouped inspector for the unexecuted state: live-form facts only,
    /// organized like the executed panel (IDENTITY / DATA / HYPOTHESIS /
    /// RESULT) so the panel never changes shape across states.
    pub fn default_inspector_groups(&self) -> Vec<InspectorGroup> {
        let field = |key: &str| {
            self.fields
                .iter()
                .find(|f| f.key == key)
                .map_or("—".to_string(), |f| f.value.clone())
        };
        let selected_exp = self
            .selected_experiment
            .and_then(|i| self.experiments.get(i));
        let tf = TIMEFRAME_OPTIONS
            .get(self.timeframe_idx)
            .copied()
            .unwrap_or("—");
        let identity = vec![
            KvRow {
                label: "EXPERIMENT".into(),
                value: selected_exp.map_or("— not created —".to_string(), |e| e.id.clone()),
            },
            KvRow {
                label: "STRATEGY".into(),
                value: self
                    .selected_strategy()
                    .map_or("—".to_string(), |s| s.name.clone()),
            },
            KvRow {
                label: "STATUS".into(),
                value: self.effective_run().label().to_string(),
            },
        ];
        let data = vec![
            KvRow {
                label: "UNIVERSE".into(),
                value: field("universe"),
            },
            KvRow {
                label: "SYMBOLS".into(),
                value: field("symbols"),
            },
            KvRow {
                label: "TIMEFRAME".into(),
                value: tf.to_string(),
            },
            KvRow {
                label: "PERIOD".into(),
                value: format!("{} → {}", field("start"), field("end")),
            },
            KvRow {
                label: "SIDE".into(),
                value: SIDE_OPTIONS
                    .get(self.side_idx)
                    .copied()
                    .unwrap_or("—")
                    .to_string(),
            },
            KvRow {
                label: "CAPITAL".into(),
                value: field("capital"),
            },
            KvRow {
                label: "COSTS".into(),
                value: format!("slip {}% · comm {}%", field("slip"), field("comm")),
            },
        ];
        let mut hypothesis = Vec::new();
        if !self.hypothesis.is_empty() {
            hypothesis.push(KvRow {
                label: "HYPOTHESIS".into(),
                value: self.hypothesis.clone(),
            });
        }
        if !self.question.is_empty() {
            hypothesis.push(KvRow {
                label: "QUESTION".into(),
                value: self.question.clone(),
            });
        }
        if hypothesis.is_empty() {
            hypothesis.push(KvRow {
                label: "HYPOTHESIS".into(),
                value: "— none stated —".to_string(),
            });
        }
        let result = vec![KvRow {
            label: "RESULT".into(),
            value: match self.results {
                Some(_) => "AVAILABLE".to_string(),
                None => "NOT RUN".to_string(),
            },
        }];
        vec![
            InspectorGroup {
                title: "IDENTITY".into(),
                rows: identity,
            },
            InspectorGroup {
                title: "DATA".into(),
                rows: data,
            },
            InspectorGroup {
                title: "HYPOTHESIS".into(),
                rows: hypothesis,
            },
            InspectorGroup {
                title: "RESULT".into(),
                rows: result,
            },
        ]
    }

    pub fn filtered_signals(&self) -> Vec<SignalRow> {
        let signals: &[SignalRow] = self.results.as_ref().map_or(&[], |r| &r.signals);
        if self.signal_filter.trim().is_empty() {
            return signals.to_vec();
        }
        let needle = self.signal_filter.trim().to_lowercase();
        signals
            .iter()
            .filter(|s| {
                s.time.to_lowercase().contains(&needle)
                    || s.symbol.to_lowercase().contains(&needle)
                    || s.side.to_lowercase().contains(&needle)
                    || s.event.to_lowercase().contains(&needle)
            })
            .cloned()
            .collect()
    }

    pub fn results_or_empty(&self) -> (Vec<Metric>, usize, usize) {
        match &self.results {
            Some(r) => (r.metrics.clone(), r.signal_total, r.trade_total),
            None => (placeholder_metrics(), 0, 0),
        }
    }
}

fn placeholder_metrics() -> Vec<Metric> {
    [
        "NET P&L",
        "TRADES",
        "SIGNALS",
        "WIN RATE",
        "PROFIT FACTOR",
        "EXPECTANCY",
        "MAX DD",
        "SHARPE",
        "SORTINO",
        "RETURN",
    ]
    .iter()
    .map(|label| Metric {
        label: (*label).to_string(),
        value: "—".to_string(),
        tone: Tone::Muted,
        emphasized: *label == "NET P&L",
    })
    .collect()
}

// ── projection: ResearchState -> flat render properties (testable) ──

#[derive(Debug, Clone, PartialEq)]
pub struct MetricView {
    pub label: String,
    pub value: String,
    pub tone: i32,
    pub emphasized: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct StrategyRowView {
    pub name: String,
    pub description: String,
    pub version: String,
    pub selected: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ExperimentRowView {
    pub id: String,
    pub strategy: String,
    pub status: String,
    pub status_tone: i32,
    pub selected: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct FieldView {
    pub key: String,
    pub label: String,
    pub value: String,
    pub kind: i32,
    pub options: Vec<String>,
    pub selected: usize,
}

/// One validation dimension for the evidence grid (name + status tone +
/// short detail — all engine-derived).
#[derive(Debug, Clone, PartialEq)]
pub struct EvidenceDimView {
    pub name: String,
    pub status: String,
    pub tone: i32,
    pub detail: String,
}

/// One “why” line: kind (FAIL / WARN / NOTE) + the real engine text.
#[derive(Debug, Clone, PartialEq)]
pub struct EvidenceWhyView {
    pub kind: String,
    pub text: String,
}

/// Inspector section for the professional context panel.
#[derive(Debug, Clone, PartialEq)]
pub struct InspectorGroup {
    pub title: String,
    pub rows: Vec<KvRow>,
}

/// One configuration group of the experiment form — grouping makes the
/// dependency chain readable (Universe → Symbols → Period → …).
#[derive(Debug, Clone, PartialEq)]
pub struct ConfigGroup {
    pub title: String,
    pub hint: String,
    pub fields: Vec<FieldView>,
}

/// Inspector section for the professional context panel.
#[derive(Debug, Clone, PartialEq)]
pub struct InspectorGroupView {
    pub title: String,
    pub rows: Vec<KvView>,
}

/// One configuration group for the intelligent form presentation.
#[derive(Debug, Clone, PartialEq)]
pub struct ConfigGroupView {
    pub title: String,
    pub hint: String,
    pub fields: Vec<FieldView>,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct KvView {
    pub label: String,
    pub value: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ResearchView {
    pub has_strategy: bool,
    pub strategy_name: String,
    pub strategy_version: String,
    pub experiment_id: String,
    pub context_line: String,
    pub state_label: String,
    pub state_tone: i32,
    pub show_results: bool,
    pub stale: bool,
    pub run_enabled: bool,
    pub run_blocked_reason: String,
    pub cancel_visible: bool,
    pub create_visible: bool,
    pub create_error: String,
    pub strategies: Vec<StrategyRowView>,
    pub strategy_count: String,
    pub experiments: Vec<ExperimentRowView>,
    pub experiment_count: String,
    pub config_groups: Vec<ConfigGroupView>,
    pub hypothesis: String,
    pub question: String,
    pub effect: String,
    pub signal_filter: String,
    pub log_lines: Vec<String>,
    pub metrics: Vec<MetricView>,
    pub signal_count_line: String,
    pub trade_count_line: String,
    pub empty_title: String,
    pub empty_detail: String,
    pub conclusion: String,
    pub validation_status: String,
    pub validation_summary: String,
    pub fingerprint_line: String,
    pub log_expanded: bool,
    pub log_status: String,
    pub tab: usize,
    pub inspector_open: bool,
    pub narrow_view: usize,
    // ── evidence-first layer (existing engine facts, better hierarchy) ──
    /// PASS / WEAK / FAIL / INCONCLUSIVE — honest verdict, never decorative.
    pub evidence_verdict: String,
    pub evidence_tone: i32,
    /// Engine grade: STRONG / MODERATE / WEAK / EXPLORATORY / INSUFFICIENT.
    pub evidence_grade: String,
    pub evidence_why: Vec<EvidenceWhyView>,
    pub evidence_dims: Vec<EvidenceDimView>,
    pub stats_rows: Vec<KvView>,
    pub montecarlo_rows: Vec<KvView>,
    pub benchmark_rows: Vec<KvView>,
    pub inspector_groups: Vec<InspectorGroupView>,
    // ── progressive-disclosure interaction state ──
    pub hypothesis_open: bool,
    pub why_open: bool,
    pub technical_open: bool,
    /// Pre-run lifecycle guidance (steps 01–03). Hidden while the engine is
    /// working or after a failure — the log already carries those states.
    pub show_next_steps: bool,
}

pub fn project(state: &ResearchState) -> ResearchView {
    let strategy = state.selected_strategy();
    let has_strategy = strategy.is_some();
    let effective = state.effective_run();
    let show_results = matches!(
        state.run,
        ResearchRun::Completed if state.results.is_some()
    ) || (state.stale && state.results.is_some());
    let show_results = show_results && has_strategy;

    let (metrics, signal_total, trade_total) = if show_results {
        let (m, s, t) = state.results_or_empty();
        (
            m.into_iter()
                .map(|k| MetricView {
                    label: k.label,
                    value: k.value,
                    tone: k.tone.kind(),
                    emphasized: k.emphasized,
                })
                .collect(),
            s,
            t,
        )
    } else {
        (
            placeholder_metrics()
                .into_iter()
                .map(|k| MetricView {
                    label: k.label,
                    value: k.value,
                    tone: k.tone.kind(),
                    emphasized: k.emphasized,
                })
                .collect(),
            0,
            0,
        )
    };

    let experiment_id = state
        .results
        .as_ref()
        .map_or_else(String::new, |r| r.experiment_id.clone());
    let context_line = format!(
        "{} · {} · {} · {} · {} → {}",
        strategy.map_or("No strategy", |s| s.name.as_str()),
        field_of(&state.fields, "universe"),
        field_of(&state.fields, "symbols"),
        TIMEFRAME_OPTIONS.get(state.timeframe_idx).unwrap_or(&""),
        field_of(&state.fields, "start"),
        field_of(&state.fields, "end"),
    );

    let (empty_title, empty_detail) = match effective {
        ResearchRun::NoExperiment => (
            "NO RESEARCH EXPERIMENT",
            "Select a strategy and define a hypothesis to begin.",
        ),
        ResearchRun::Draft | ResearchRun::Ready => (
            "NOT EXECUTED",
            "Review the configuration, then run the research.",
        ),
        ResearchRun::Running | ResearchRun::Analyzing | ResearchRun::ValidatingResult => (
            "EXECUTING",
            "The engine is working — progress appears in the log.",
        ),
        ResearchRun::Validating => ("VALIDATING", "Checking the configuration."),
        ResearchRun::Failed => ("EXECUTION FAILED", "See the research log for the cause."),
        ResearchRun::Cancelled => (
            "CANCELLED",
            "The run stopped at a safe checkpoint — saved work is untouched.",
        ),
        ResearchRun::Invalid => ("INVALID", "Fix the configuration errors to proceed."),
        ResearchRun::NoData => (
            "NO DATA",
            "The engine produced nothing in this range — adjust the dataset.",
        ),
        ResearchRun::Completed | ResearchRun::Stale => ("", ""),
    };
    let empty_detail = if effective == ResearchRun::Invalid && !state.create_error.is_empty() {
        state.create_error.clone()
    } else {
        empty_detail.to_string()
    };

    let conclusion = if show_results {
        state
            .results
            .as_ref()
            .map_or_else(String::new, |r| r.conclusion.clone())
    } else {
        String::new()
    };
    let (validation_status, validation_summary) = if show_results {
        state
            .results
            .as_ref()
            .map_or((String::new(), String::new()), |r| {
                (r.validation_status.clone(), r.validation_summary.clone())
            })
    } else {
        (String::new(), String::new())
    };
    let fingerprint_line = if show_results {
        state
            .results
            .as_ref()
            .map_or_else(String::new, |r| r.fingerprint_line.clone())
    } else {
        String::new()
    };

    let strategies = state
        .strategies
        .iter()
        .enumerate()
        .map(|(i, s)| StrategyRowView {
            name: s.name.clone(),
            description: s.description.clone(),
            version: s.version.clone(),
            selected: state.selected == Some(i),
        })
        .collect::<Vec<_>>();
    let experiments = state
        .experiments
        .iter()
        .enumerate()
        .map(|(i, e)| ExperimentRowView {
            id: e.id.clone(),
            strategy: e.strategy.clone(),
            status: e.status_label.clone(),
            status_tone: e.status_tone.badge(),
            selected: state.selected_experiment == Some(i),
        })
        .collect::<Vec<_>>();
    let field_of = |key: &str| -> FieldView {
        let f = state
            .fields
            .iter()
            .find(|f| f.key == key)
            .cloned()
            .unwrap_or_else(|| ConfigField {
                key: key.to_string(),
                label: key.to_uppercase(),
                value: String::new(),
            });
        FieldView {
            key: f.key.clone(),
            label: f.label.clone(),
            value: f.value.clone(),
            kind: 0,
            options: Vec::new(),
            selected: 0,
        }
    };
    let select_of = |key: &str, label: &str, options: &[&str], selected: usize| -> FieldView {
        FieldView {
            key: key.to_string(),
            label: label.to_string(),
            value: String::new(),
            kind: 1,
            options: options.iter().map(|s| (*s).to_string()).collect(),
            selected,
        }
    };
    // Grouped config form: the dependency chain reads as one coherent model
    // (Universe → Symbols → Period → Costs → Execution → Parameters).
    let mut groups: Vec<ConfigGroupView> = Vec::new();
    let mut push_group = |title: &str, hint: &str, fields: Vec<FieldView>| {
        groups.push(ConfigGroupView {
            title: title.to_string(),
            hint: hint.to_string(),
            fields,
        });
    };
    push_group(
        "DATASET",
        "Universe defines the symbol pool",
        vec![field_of("universe"), field_of("symbols")],
    );
    push_group(
        "PERIOD",
        "Backtest window",
        vec![field_of("start"), field_of("end")],
    );
    push_group(
        "CAPITAL & COSTS",
        "Sizing and execution frictions",
        vec![field_of("capital"), field_of("slip"), field_of("comm")],
    );
    push_group(
        "EXECUTION",
        "Bar granularity and direction filter",
        vec![
            select_of(
                "timeframe",
                "TIMEFRAME",
                &TIMEFRAME_OPTIONS,
                state.timeframe_idx,
            ),
            select_of("side", "SIDE", &SIDE_OPTIONS, state.side_idx),
        ],
    );
    push_group(
        "PARAMETERS",
        "Strategy inputs and expected direction",
        vec![
            field_of("params"),
            select_of(
                "direction",
                "EXPECTED DIRECTION",
                &DIRECTION_OPTIONS,
                state.direction_idx,
            ),
        ],
    );

    // ── evidence-first projection (only when results exist) ──
    let results_ref = state.results.as_ref();
    let evidence_verdict =
        results_ref.map_or_else(String::new, |r| match r.validation_status.as_str() {
            "PASS" => "PASS".to_string(),
            "WARNING" => "WEAK".to_string(),
            "FAIL" => "FAIL".to_string(),
            "" => String::new(),
            "INSUFFICIENT_DATA" | "INSUFFICIENT" => "INCONCLUSIVE".to_string(),
            other => other.to_string(),
        });
    let evidence_tone = match evidence_verdict.as_str() {
        "PASS" => Tone::Positive.badge(),
        "WEAK" => Tone::Warning.badge(),
        "FAIL" => Tone::Negative.badge(),
        _ => Tone::Muted.badge(),
    };
    let evidence_grade = results_ref.map_or_else(String::new, |r| r.evidence_grade.clone());
    let evidence_why: Vec<EvidenceWhyView> = results_ref
        .map_or(Vec::<EvidenceWhyView>::new(), |r| {
            r.evidence_why
                .iter()
                .map(|w| EvidenceWhyView {
                    kind: w.kind.clone(),
                    text: w.text.clone(),
                })
                .collect()
        })
        .into_iter()
        .take(EVIDENCE_WHY_CAP)
        .collect();
    let evidence_dims: Vec<EvidenceDimView> = results_ref.map_or(Vec::new(), |r| {
        r.evidence_dims
            .iter()
            .map(|d| EvidenceDimView {
                name: d.name.clone(),
                status: d.status.clone(),
                tone: d.status_tone.badge(),
                detail: d.detail.clone(),
            })
            .collect()
    });
    let kv_view = |rows: &[KvRow]| -> Vec<KvView> {
        rows.iter()
            .map(|r| KvView {
                label: r.label.clone(),
                value: r.value.clone(),
            })
            .collect()
    };
    let stats_rows: Vec<KvView> = results_ref.map_or(Vec::new(), |r| kv_view(&r.stats_rows));
    let montecarlo_rows: Vec<KvView> =
        results_ref.map_or(Vec::new(), |r| kv_view(&r.montecarlo_rows));
    let benchmark_rows: Vec<KvView> =
        results_ref.map_or(Vec::new(), |r| kv_view(&r.benchmark_rows));
    let inspector_groups: Vec<InspectorGroupView> = if state.results.is_some() {
        results_ref.map_or(Vec::<InspectorGroupView>::new(), |r| {
            r.inspector_groups
                .iter()
                .map(|g| InspectorGroupView {
                    title: g.title.clone(),
                    rows: kv_view(&g.rows),
                })
                .collect()
        })
    } else {
        state
            .default_inspector_groups()
            .iter()
            .map(|g| InspectorGroupView {
                title: g.title.clone(),
                rows: kv_view(&g.rows),
            })
            .collect()
    };

    ResearchView {
        has_strategy,
        strategy_name: strategy.map_or_else(|| "No strategy".to_string(), |s| s.name.clone()),
        strategy_version: strategy.map_or_else(String::new, |s| s.version.clone()),
        experiment_id,
        context_line,
        state_label: effective.label().to_string(),
        state_tone: effective.tone().badge(),
        show_results,
        stale: state.stale,
        run_enabled: state.engine_wired
            && has_strategy
            && matches!(
                state.run,
                ResearchRun::Draft | ResearchRun::Ready | ResearchRun::Completed
            ),
        run_blocked_reason: if state.engine_wired {
            String::new()
        } else {
            state.run_blocked_reason.clone()
        },
        cancel_visible: matches!(
            state.run,
            ResearchRun::Running
                | ResearchRun::Analyzing
                | ResearchRun::Validating
                | ResearchRun::ValidatingResult
        ),
        create_visible: matches!(
            state.run,
            ResearchRun::NoExperiment
                | ResearchRun::Draft
                | ResearchRun::Ready
                | ResearchRun::Invalid
        ),
        create_error: state.create_error.clone(),
        strategy_count: format!("{} total", strategies.len()),
        experiment_count: format!("{} total", experiments.len()),
        strategies,
        experiments,
        config_groups: groups,
        hypothesis: state.hypothesis.clone(),
        question: state.question.clone(),
        effect: state.expected_effect.clone(),
        signal_filter: state.signal_filter.clone(),
        // Newest first: the compact strip always shows the latest events.
        log_lines: state.log.iter().rev().take(LOG_VIEW_CAP).cloned().collect(),
        metrics,
        signal_count_line: if show_results {
            format!("{signal_total} SIGNALS")
        } else {
            String::new()
        },
        trade_count_line: if show_results {
            format!("{trade_total} TRADES")
        } else {
            String::new()
        },
        empty_title: empty_title.to_string(),
        empty_detail,
        conclusion,
        validation_status,
        validation_summary,
        fingerprint_line,
        log_expanded: matches!(
            state.run,
            ResearchRun::Running
                | ResearchRun::Analyzing
                | ResearchRun::Validating
                | ResearchRun::ValidatingResult
                | ResearchRun::Failed
        ),
        log_status: if state.log.is_empty() {
            "READY — no engine events yet.".to_string()
        } else {
            state.log.last().cloned().unwrap_or_default()
        },
        tab: state.tab,
        inspector_open: state.inspector_open,
        narrow_view: state.narrow_view,
        evidence_verdict,
        evidence_tone,
        evidence_grade,
        evidence_why,
        evidence_dims,
        stats_rows,
        montecarlo_rows,
        benchmark_rows,
        inspector_groups,
        hypothesis_open: state.hypothesis_open,
        why_open: state.why_open,
        technical_open: state.technical_open,
        show_next_steps: matches!(
            state.run,
            ResearchRun::NoExperiment
                | ResearchRun::Draft
                | ResearchRun::Ready
                | ResearchRun::Invalid
                | ResearchRun::NoData
        ),
    }
}

// ── engine snapshot loader (read-only; the Python engine owns the format) ──

/// Subdirectory layout written by the Python research engine:
/// `<dir>/experiments/<id>.json` + `<dir>/runs/<id>/run.json`.
/// Returns the latest COMPLETED experiment found, or `None` when the
/// directory holds no completed experiment. Never panics; every problem
/// surfaces as `Err` with the offending path.
pub fn load_snapshot(experiments_dir: &Path) -> Result<ResearchResults, String> {
    let (exp, signals, trades) = read_bundle(experiments_dir)?;
    Ok(results_from_snapshot(&exp, &signals, &trades))
}

fn read_bundle(
    experiments_dir: &Path,
) -> Result<(serde_json::Value, serde_json::Value, serde_json::Value), String> {
    let mut best: Option<(String, PathBuf)> = None;
    let entries = std::fs::read_dir(experiments_dir)
        .map_err(|e| format!("cannot list {}: {e}", experiments_dir.display()))?;
    for entry in entries {
        let entry = entry.map_err(|e| format!("cannot read entry: {e}"))?;
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) != Some("json") {
            continue;
        }
        let text = std::fs::read_to_string(&path)
            .map_err(|e| format!("cannot read {}: {e}", path.display()))?;
        let value: serde_json::Value =
            serde_json::from_str(&text).map_err(|e| format!("bad JSON {}: {e}", path.display()))?;
        if str_at(&value, "status") != "COMPLETED" {
            continue;
        }
        let executed = str_at(&value, "executed_at");
        let replace = match &best {
            Some((at, _)) => executed > *at,
            None => true,
        };
        if replace {
            best = Some((executed, path));
        }
    }
    let (_, path) = best.ok_or_else(|| "no COMPLETED experiment in snapshot".to_string())?;
    let text = std::fs::read_to_string(&path)
        .map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    let exp: serde_json::Value =
        serde_json::from_str(&text).map_err(|e| format!("bad JSON {}: {e}", path.display()))?;
    let id = str_at(&exp, "experiment_id");
    let runs_dir = experiments_dir
        .parent()
        .map(|p| p.join("runs").join(&id).join("run.json"));
    let (signals, trades) = match runs_dir {
        Some(run_path) if run_path.is_file() => {
            let text = std::fs::read_to_string(&run_path)
                .map_err(|e| format!("cannot read {}: {e}", run_path.display()))?;
            let run: serde_json::Value = serde_json::from_str(&text)
                .map_err(|e| format!("bad JSON {}: {e}", run_path.display()))?;
            (
                run.get("signals")
                    .cloned()
                    .unwrap_or(serde_json::Value::Null),
                run.get("trades")
                    .cloned()
                    .unwrap_or(serde_json::Value::Null),
            )
        }
        _ => (serde_json::Value::Null, serde_json::Value::Null),
    };
    Ok((exp, signals, trades))
}

fn str_at(value: &serde_json::Value, key: &str) -> String {
    value
        .get(key)
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string()
}

fn f64_at(value: &serde_json::Value, key: &str) -> Option<f64> {
    value.get(key).and_then(|v| v.as_f64())
}

fn arr_at<'a>(value: &'a serde_json::Value, key: &str) -> Vec<&'a serde_json::Value> {
    value
        .get(key)
        .and_then(|v| v.as_array())
        .map(|a| a.iter().collect())
        .unwrap_or_default()
}

/// Indian digit grouping with two decimals and an explicit sign, e.g.
/// `+₹1,85,236.40`. Engine values arrive raw; presentation formats here.
pub fn fmt_inr(v: f64) -> String {
    let sign = if v < 0.0 { "−" } else { "+" };
    let abs = v.abs();
    let int = abs.trunc() as i64;
    let frac = (abs.fract() * 100.0).round() as i64;
    let digits = int.to_string();
    let grouped = if digits.len() <= 3 {
        digits
    } else {
        let (head, tail) = digits.split_at(digits.len() - 3);
        let mut out = String::new();
        let chars: Vec<char> = head.chars().collect();
        let first = chars.len() % 2;
        let (lead, rest) = chars.split_at(first);
        out.extend(lead.iter());
        let mut chunks = rest.chunks(2).peekable();
        if !lead.is_empty() && chunks.peek().is_some() {
            out.push(',');
        }
        let parts: Vec<String> = chunks.map(|c| c.iter().collect()).collect();
        out.push_str(&parts.join(","));
        if !out.is_empty() {
            out.push(',');
        }
        out.push_str(tail);
        out
    };
    format!("{sign}₹{grouped}.{frac:02}")
}

pub fn fmt_pct(v: f64) -> String {
    format!("{v:+.2}%")
}

pub fn fmt_num(v: f64) -> String {
    format!("{v:.2}")
}

fn tone_of(v: f64) -> Tone {
    if v > 0.0 {
        Tone::Positive
    } else if v < 0.0 {
        Tone::Negative
    } else {
        Tone::Muted
    }
}

fn opt_num(value: Option<f64>, format: fn(f64) -> String) -> (String, Tone) {
    match value {
        Some(v) => (format(v), tone_of(v)),
        None => ("—".to_string(), Tone::Muted),
    }
}

/// Build display-ready results from one persisted engine bundle. Unknown or
/// missing fields degrade to "—"/empty — the loader never invents values.
pub fn results_from_snapshot(
    exp: &serde_json::Value,
    signals: &serde_json::Value,
    trades: &serde_json::Value,
) -> ResearchResults {
    let summary = exp.get("result_summary").cloned().unwrap_or_default();
    let trade_count = summary
        .get("trade_count")
        .and_then(|v| v.as_u64())
        .unwrap_or(0) as usize;
    let signal_count = summary
        .get("signal_count")
        .and_then(|v| v.as_u64())
        .unwrap_or(0) as usize;

    let mut metrics = Vec::new();
    let mut push = |label: &str, value: String, tone: Tone, emphasized: bool| {
        metrics.push(Metric {
            label: label.to_string(),
            value,
            tone,
            emphasized,
        });
    };
    let (net, net_tone) = opt_num(f64_at(&summary, "net_pnl"), fmt_inr);
    push("NET P&L", net, net_tone, true);
    push("TRADES", trade_count.to_string(), Tone::Neutral, false);
    push("SIGNALS", signal_count.to_string(), Tone::Neutral, false);
    let (wr, wr_tone) = match f64_at(&summary, "win_rate") {
        Some(v) => (format!("{:.1}%", v * 100.0), Tone::Neutral),
        None => ("—".to_string(), Tone::Muted),
    };
    push("WIN RATE", wr, wr_tone, false);
    let (pf, pf_tone) = match f64_at(&summary, "profit_factor") {
        Some(v) => (
            fmt_num(v),
            if v >= 1.0 {
                Tone::Positive
            } else {
                Tone::Negative
            },
        ),
        None => ("—".to_string(), Tone::Muted),
    };
    push("PROFIT FACTOR", pf, pf_tone, false);
    let (ex, ex_tone) = opt_num(f64_at(&summary, "expectancy"), fmt_inr);
    push("EXPECTANCY", ex, ex_tone, false);
    let (dd, _) = match f64_at(&summary, "max_drawdown_pct") {
        Some(v) => (format!("{v:.2}%"), Tone::Negative),
        None => ("—".to_string(), Tone::Muted),
    };
    push("MAX DD", dd, Tone::Muted, false);
    let (sh, sh_tone) = match f64_at(&summary, "sharpe") {
        Some(v) => (
            fmt_num(v),
            if v >= 0.0 {
                Tone::Positive
            } else {
                Tone::Negative
            },
        ),
        None => ("—".to_string(), Tone::Muted),
    };
    push("SHARPE", sh, sh_tone, false);
    let (so, _) = opt_num(f64_at(&summary, "sortino"), fmt_num);
    push("SORTINO", so, Tone::Muted, false);
    let (ret, ret_tone) = opt_num(f64_at(&summary, "return_pct"), fmt_pct);
    push("RETURN", ret, ret_tone, false);

    let sig_items = signals.as_array().cloned().unwrap_or_default();
    let signals_capped: Vec<SignalRow> = sig_items
        .iter()
        .take(SIGNALS_VIEW_CAP)
        .map(|s| SignalRow {
            time: str_at(s, "time"),
            symbol: str_at(s, "symbol"),
            tf: str_at(s, "timeframe"),
            side: str_at(s, "side"),
            price: s.get("price").map_or_else(
                || "—".to_string(),
                |v| match v.as_f64() {
                    Some(f) => fmt_num(f),
                    None => v.as_str().unwrap_or("—").to_string(),
                },
            ),
            event: str_at(s, "event"),
            strategy: str_at(s, "strategy"),
            exp: str_at(s, "experiment"),
        })
        .collect();

    let trade_items = trades.as_array().cloned().unwrap_or_default();
    let mut trades_capped: Vec<ResearchTradeRow> = Vec::new();
    for (i, t) in trade_items.iter().take(TRADES_VIEW_CAP).enumerate() {
        let pnl = f64_at(t, "pnl").unwrap_or(0.0);
        trades_capped.push(ResearchTradeRow {
            no: (i + 1).to_string(),
            entry: str_at(t, "entry_time"),
            exit: str_at(t, "exit_time"),
            side: str_at(t, "side"),
            qty: t.get("quantity").map_or_else(
                || "—".to_string(),
                |v| match v.as_f64() {
                    Some(f) => fmt_num(f),
                    None => v.as_str().unwrap_or("—").to_string(),
                },
            ),
            pnl: fmt_inr(pnl),
            reason: str_at(t, "exit_reason"),
            hold: t.get("bars_held").map_or_else(
                || "—".to_string(),
                |v| match v.as_u64() {
                    Some(n) => n.to_string(),
                    None => v.as_str().unwrap_or("—").to_string(),
                },
            ),
            pnl_tone: tone_of(pnl),
        });
    }

    let analysis = exp.get("analysis").cloned().unwrap_or_default();
    let robustness: Vec<RobustRow> = arr_at(&analysis, "robustness")
        .into_iter()
        .map(|r| {
            let stability = str_at(r, "stability");
            let tone = match stability.as_str() {
                "PASS" => Tone::Positive,
                "FAIL" => Tone::Negative,
                _ => Tone::Warning,
            };
            let input = r.get("input").map_or_else(String::new, |v| {
                let param = v.get("param").and_then(|p| p.as_str()).unwrap_or("?");
                let val = v.get("value").map_or_else(
                    || "?".to_string(),
                    |n| match n.as_f64() {
                        Some(f) => fmt_num(f),
                        None => "?".to_string(),
                    },
                );
                format!("{param}={val}")
            });
            RobustRow {
                test: str_at(r, "test_type"),
                input,
                stability,
                stability_tone: tone,
                evidence: format_evidence(r.get("evidence")),
            }
        })
        .collect();

    let validation = analysis.get("validation").cloned().unwrap_or_default();
    let validation_status = str_at(&validation, "status");
    let validation_summary = str_at(&validation, "summary");
    let mut validation_rows = Vec::new();
    for (label, key) in [
        ("OUT-OF-SAMPLE", "oos"),
        ("CPCV", "cpcv"),
        ("PBO / DSR", "pbo"),
        ("COST STRESS", "cost_stress"),
        ("TEMPORAL", "temporal"),
        ("LEAKAGE", "leakage"),
        ("WALK-FORWARD", "walk_forward"),
    ] {
        if let Some(section) = validation.get(key) {
            let status = str_at(section, "status");
            if !status.is_empty() {
                validation_rows.push(KvRow {
                    label: label.to_string(),
                    value: status,
                });
            }
        }
    }
    let warnings = validation
        .get("warnings")
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|w| w.as_str().map(str::to_string))
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    for warning in warnings {
        validation_rows.push(KvRow {
            label: "NOTE".to_string(),
            value: warning,
        });
    }

    let conditions = analysis.get("conditions").cloned().unwrap_or_default();
    let mut quality_rows = Vec::new();
    if let Some(symbols) = conditions.get("symbols").and_then(|v| v.as_array()) {
        quality_rows.push(KvRow {
            label: "SYMBOLS TRADED".to_string(),
            value: symbols.len().to_string(),
        });
    }
    for item in conditions
        .get("unsupported")
        .and_then(|v| v.as_array())
        .map(|a| a.iter().filter_map(|w| w.as_str()).collect::<Vec<_>>())
        .unwrap_or_default()
    {
        quality_rows.push(KvRow {
            label: "UNAVAILABLE".to_string(),
            value: item.to_string(),
        });
    }

    let repro = exp.get("reproducibility").cloned().unwrap_or_default();
    let inspector_rows = [
        ("EXPERIMENT", str_at(exp, "experiment_id")),
        ("STRATEGY", str_at(exp, "strategy_id")),
        ("VERSION", short_hash(&str_at(exp, "strategy_version"), 8)),
        ("STATUS", str_at(exp, "status")),
        ("TIMEFRAME", str_at(exp, "timeframe")),
        (
            "PERIOD",
            format!(
                "{} → {}",
                str_at(exp, "start_date"),
                str_at(exp, "end_date")
            ),
        ),
        ("SIDE", str_at(exp, "side")),
        ("EXECUTED", str_at(exp, "executed_at")),
        ("DATASET", short_hash(&str_at(exp, "dataset_version"), 12)),
        ("ENGINE", short_hash(&str_at(exp, "engine_source_hash"), 12)),
        ("VALIDATION", str_at(exp, "validation_status")),
    ]
    .iter()
    .map(|(label, value)| KvRow {
        label: (*label).to_string(),
        value: value.clone(),
    })
    .chain(repro.get("strategy_source_hash").map(|_| KvRow {
        label: "STRATEGY HASH".to_string(),
        value: short_hash(&str_at(&repro, "strategy_source_hash"), 12),
    }))
    .collect();

    let report = exp.get("report").cloned().unwrap_or_default();
    let conclusion = str_at(&report, "conclusion");
    let mut report_sections = Vec::new();
    for (label, key) in [
        ("QUESTION", "research_question"),
        ("HYPOTHESIS", "hypothesis"),
        ("METHODOLOGY", "methodology"),
    ] {
        let body = str_at(&report, key);
        if !body.is_empty() {
            report_sections.push(KvRow {
                label: label.to_string(),
                value: body,
            });
        }
    }
    for item in report
        .get("limitations")
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|w| w.as_str().map(str::to_string))
                .collect::<Vec<_>>()
        })
        .unwrap_or_default()
    {
        report_sections.push(KvRow {
            label: "LIMIT".to_string(),
            value: item,
        });
    }

    let fingerprint_line = format!(
        "result {} · config {}",
        short_hash(&str_at(exp, "result_fingerprint"), 12),
        short_hash(&str_at(exp, "config_fingerprint"), 12),
    );

    // ── evidence layer: existing engine facts, better hierarchy ──
    let grade = validation
        .get("evidence_grade")
        .cloned()
        .unwrap_or_default();
    let evidence_grade = str_at(&grade, "grade");
    let mut evidence_why: Vec<EvidenceWhy> = Vec::new();
    if let Some(details) = grade.get("details") {
        for kind in ["fails", "warns"] {
            if let Some(items) = details.get(kind).and_then(|v| v.as_array()) {
                for item in items {
                    if let Some(text) = item.as_str() {
                        if !text.is_empty() {
                            evidence_why.push(EvidenceWhy {
                                kind: if kind == "fails" {
                                    "FAIL".to_string()
                                } else {
                                    "WARN".to_string()
                                },
                                text: text.to_string(),
                            });
                        }
                    }
                }
            }
        }
    }
    for src in [
        grade.get("limitations").and_then(|v| v.as_array()),
        validation.get("warnings").and_then(|v| v.as_array()),
    ] {
        if let Some(items) = src {
            for item in items {
                if let Some(text) = item.as_str() {
                    if !text.is_empty() {
                        evidence_why.push(EvidenceWhy {
                            kind: "NOTE".to_string(),
                            text: text.to_string(),
                        });
                    }
                }
            }
        }
    }
    evidence_why.truncate(EVIDENCE_WHY_CAP);

    let evidence_dims = build_evidence_dims(&validation);
    let stats_rows = build_stats_rows(&analysis);
    let montecarlo_rows = build_montecarlo_rows(&analysis);
    let benchmark_rows = build_benchmark_rows(&analysis);
    let inspector_groups = build_inspector_groups(exp, &validation, &repro);

    ResearchResults {
        experiment_id: str_at(exp, "experiment_id"),
        metrics,
        signal_total: signal_count,
        trade_total: trade_count,
        signals: signals_capped,
        trades: trades_capped,
        robustness,
        validation_status: validation_status.clone(),
        validation_summary,
        validation_rows,
        quality_rows,
        inspector_rows,
        compare_rows: Vec::new(),
        compare_verdict: String::new(),
        report_sections,
        conclusion,
        fingerprint_line,
        executed_at: str_at(exp, "executed_at"),
        dataset_version: str_at(exp, "dataset_version"),
        evidence_grade,
        evidence_why,
        evidence_dims,
        stats_rows,
        montecarlo_rows,
        benchmark_rows,
        inspector_groups,
    }
}

/// Cap for the “why” layer — a viewport policy, not a data policy (the full
/// lists stay persisted by the engine).
pub const EVIDENCE_WHY_CAP: usize = 12;

/// Map an engine status string (PASS / WARNING / FAIL / OK /
/// INSUFFICIENT_DATA / …) onto the shared badge tone.
fn status_tone(status: &str) -> Tone {
    match status {
        "PASS" | "OK" => Tone::Positive,
        "WARNING" => Tone::Warning,
        "FAIL" => Tone::Negative,
        _ => Tone::Muted,
    }
}

fn num_or_dash(v: &serde_json::Value, key: &str, format: fn(f64) -> String) -> String {
    match f64_at(v, key) {
        Some(f) => format(f),
        None => "—".to_string(),
    }
}

/// Build the per-dimension evidence grid from the persisted validation
/// bundle. Every value is read defensively: a missing section renders as
/// “NOT RUN” rather than an invented number.
fn build_evidence_dims(validation: &serde_json::Value) -> Vec<EvidenceDim> {
    fn dim(
        validation: &serde_json::Value,
        key: &str,
        name: &str,
        detail: impl Fn(&serde_json::Value) -> String,
    ) -> EvidenceDim {
        let section = validation.get(key).cloned().unwrap_or_default();
        let status = str_at(&section, "status");
        EvidenceDim {
            name: name.to_string(),
            status: if status.is_empty() {
                "NOT RUN".to_string()
            } else {
                status.clone()
            },
            status_tone: status_tone(&status),
            detail: if status.is_empty() {
                "—".to_string()
            } else {
                detail(&section)
            },
        }
    }
    vec![
        dim(validation, "oos", "OUT-OF-SAMPLE", |s| {
            match (f64_at(s, "is_expectancy"), f64_at(s, "oos_expectancy")) {
                (Some(a), Some(b)) => format!("exp {} → {}", fmt_num(a), fmt_num(b)),
                _ => str_at(s, "status"),
            }
        }),
        dim(validation, "cpcv", "CPCV", |s| {
            let paths = s.get("n_paths").and_then(|v| v.as_u64()).unwrap_or(0);
            match f64_at(s, "proportion_above_threshold") {
                Some(p) => format!("{paths} paths · {:.0}% above", p * 100.0),
                None => format!("{paths} paths"),
            }
        }),
        dim(validation, "pbo", "PBO", |s| match f64_at(s, "pbo") {
            Some(p) => format!("PBO {p:.2}"),
            None => str_at(s, "status"),
        }),
        dim(validation, "dsr", "DSR", |s| match f64_at(s, "dsr") {
            Some(d) => format!("DSR {d:.2}"),
            None => str_at(s, "status"),
        }),
        dim(validation, "cost_stress", "COST STRESS", |s| {
            let n = s
                .get("scenarios")
                .and_then(|v| v.as_array())
                .map(|a| a.len())
                .unwrap_or(0);
            if n == 0 {
                str_at(s, "status")
            } else {
                format!("{n} scenarios")
            }
        }),
        dim(validation, "temporal", "TEMPORAL", |s| {
            match (f64_at(s, "best"), f64_at(s, "worst")) {
                (Some(b), Some(w)) => format!("best {} · worst {}", fmt_num(b), fmt_num(w)),
                _ => str_at(s, "status"),
            }
        }),
        dim(validation, "leakage", "LEAKAGE", |s| str_at(s, "status")),
        dim(validation, "walk_forward", "WALK-FORWARD", |s| {
            let folds = s.get("folds").and_then(|v| v.as_array()).map(|a| a.len());
            match (folds, f64_at(s, "mean_test_expectancy")) {
                (Some(n), Some(m)) => format!("{n} folds · exp {m:.2}"),
                (Some(n), None) => format!("{n} folds"),
                _ => str_at(s, "status"),
            }
        }),
    ]
}

/// Statistical-significance rows (95% CI, t-stat, p-value, Cohen's d) —
/// existing engine output, simply never surfaced before.
fn build_stats_rows(analysis: &serde_json::Value) -> Vec<KvRow> {
    let stats = analysis.get("stats").cloned().unwrap_or_default();
    if stats.as_object().map_or(false, |o| o.is_empty()) {
        return Vec::new();
    }
    let mut rows = vec![KvRow {
        label: "EXPECTANCY".into(),
        value: num_or_dash(&stats, "mean", fmt_inr),
    }];
    let (lo, hi) = (f64_at(&stats, "ci_low"), f64_at(&stats, "ci_high"));
    match (lo, hi) {
        (Some(a), Some(b)) => rows.push(KvRow {
            label: "95% CI".into(),
            value: format!("{} → {}", fmt_num(a), fmt_num(b)),
        }),
        _ => {}
    }
    if let Some(n) = stats.get("n").and_then(|v| v.as_u64()) {
        rows.push(KvRow {
            label: "TRADES (STATS)".into(),
            value: n.to_string(),
        });
    }
    if let Some(t) = f64_at(&stats, "t_stat") {
        rows.push(KvRow {
            label: "t-STAT".into(),
            value: format!("{t:.2}"),
        });
    }
    if let Some(p) = f64_at(&stats, "p_value") {
        rows.push(KvRow {
            label: "p-VALUE".into(),
            value: format!("{p:.3}"),
        });
    }
    if let Some(d) = f64_at(&stats, "cohens_d") {
        rows.push(KvRow {
            label: "COHEN'S d".into(),
            value: format!("{d:.2}"),
        });
    }
    if let Some(wr) = f64_at(&stats, "win_rate") {
        rows.push(KvRow {
            label: "WIN RATE CI".into(),
            value: format!("{:.1}%", wr * 100.0),
        });
    }
    let notes = stats
        .get("notes")
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|n| n.as_str().map(str::to_string))
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    for note in notes.into_iter().take(STATS_NOTES_CAP) {
        rows.push(KvRow {
            label: "NOTE".into(),
            value: note,
        });
    }
    rows
}

/// Monte-Carlo path rows (p5 / p50 / p95 total PnL, max drawdown, profit
/// probability) — existing engine output.
fn build_montecarlo_rows(analysis: &serde_json::Value) -> Vec<KvRow> {
    let mc = analysis.get("montecarlo").cloned().unwrap_or_default();
    if mc.as_object().map_or(false, |o| o.is_empty()) {
        return Vec::new();
    }
    vec![
        KvRow {
            label: "P5 P&L".into(),
            value: num_or_dash(&mc, "total_pnl_p5", fmt_inr),
        },
        KvRow {
            label: "MEDIAN P&L".into(),
            value: num_or_dash(&mc, "total_pnl_p50", fmt_inr),
        },
        KvRow {
            label: "P95 P&L".into(),
            value: num_or_dash(&mc, "total_pnl_p95", fmt_inr),
        },
        KvRow {
            label: "P95 MAX DD".into(),
            value: num_or_dash(&mc, "max_dd_p95", fmt_inr),
        },
        KvRow {
            label: "PROB. PROFIT".into(),
            value: match f64_at(&mc, "prob_profit") {
                Some(p) => format!("{:.0}%", p * 100.0),
                None => "—".to_string(),
            },
        },
    ]
}

/// Benchmark comparison rows (strategy vs buy & hold) — existing engine
/// output.
fn build_benchmark_rows(analysis: &serde_json::Value) -> Vec<KvRow> {
    let bm = analysis.get("benchmark").cloned().unwrap_or_default();
    if bm.as_object().map_or(false, |o| o.is_empty()) {
        return Vec::new();
    }
    vec![
        KvRow {
            label: "STRATEGY".into(),
            value: match f64_at(&bm, "strategy_return_pct") {
                Some(v) => fmt_pct(v),
                None => "—".to_string(),
            },
        },
        KvRow {
            label: "BUY & HOLD".into(),
            value: match f64_at(&bm, "benchmark_return_pct") {
                Some(v) => fmt_pct(v),
                None => "—".to_string(),
            },
        },
        KvRow {
            label: "EXCESS".into(),
            value: match f64_at(&bm, "excess_return_pct") {
                Some(v) => fmt_pct(v),
                None => "—".to_string(),
            },
        },
    ]
}

/// Inspector groups: the same persisted facts, organized as a professional
/// research context panel (IDENTITY / EXECUTION / DATA / ENGINE / VALIDATION
/// / EVIDENCE). Long hashes are kept short but stay available in full via
/// the flat `inspector_rows`.
fn build_inspector_groups(
    exp: &serde_json::Value,
    validation: &serde_json::Value,
    repro: &serde_json::Value,
) -> Vec<InspectorGroup> {
    let row = |label: &str, value: String| KvRow {
        label: label.to_string(),
        value,
    };
    let identity = vec![
        row("EXPERIMENT", str_at(exp, "experiment_id")),
        row("STRATEGY", str_at(exp, "strategy_id")),
        row("VERSION", str_at(exp, "strategy_version")),
        row("STATUS", str_at(exp, "status")),
    ];
    let execution = vec![
        row("TIMEFRAME", str_at(exp, "timeframe")),
        row("SIDE", str_at(exp, "side")),
        row("EXECUTED", str_at(exp, "executed_at")),
    ];
    let data = vec![
        row(
            "PERIOD",
            format!(
                "{} → {}",
                str_at(exp, "start_date"),
                str_at(exp, "end_date")
            ),
        ),
        row("DATASET", short_hash(&str_at(exp, "dataset_version"), 12)),
    ];
    let engine = vec![
        row("ENGINE", short_hash(&str_at(exp, "engine_source_hash"), 12)),
        row(
            "STRATEGY HASH",
            short_hash(&str_at(repro, "strategy_source_hash"), 12),
        ),
    ];
    let mut val_group = vec![row("VALIDATION", str_at(exp, "validation_status"))];
    let grade = str_at(validation, "status");
    if !grade.is_empty() && grade != str_at(exp, "validation_status") {
        val_group.push(row("GATE", grade));
    }
    let evidence = vec![
        row("RESULT", short_hash(&str_at(exp, "result_fingerprint"), 12)),
        row("CONFIG", short_hash(&str_at(exp, "config_fingerprint"), 12)),
    ];
    vec![
        InspectorGroup {
            title: "IDENTITY".into(),
            rows: identity,
        },
        InspectorGroup {
            title: "EXECUTION".into(),
            rows: execution,
        },
        InspectorGroup {
            title: "DATA".into(),
            rows: data,
        },
        InspectorGroup {
            title: "ENGINE".into(),
            rows: engine,
        },
        InspectorGroup {
            title: "VALIDATION".into(),
            rows: val_group,
        },
        InspectorGroup {
            title: "EVIDENCE".into(),
            rows: evidence,
        },
    ]
}

fn short_hash(full: &str, len: usize) -> String {
    if full.is_empty() {
        return "—".to_string();
    }
    full.chars().take(len).collect()
}

fn format_evidence(evidence: Option<&serde_json::Value>) -> String {
    let Some(ev) = evidence else {
        return "—".to_string();
    };
    if let Some(note) = ev.get("note").and_then(|v| v.as_str()) {
        return note.to_string();
    }
    let var_exp = ev.get("variant_expectancy").and_then(|v| v.as_f64());
    let base_exp = ev.get("baseline_expectancy").and_then(|v| v.as_f64());
    match (base_exp, var_exp) {
        (Some(b), Some(v)) => format!("exp {} → {}", fmt_num(b), fmt_num(v)),
        _ => "—".to_string(),
    }
}

/// Snapshot directory from the environment (`VAYREN_RESEARCH_SNAPSHOT` →
/// the engine's `research/` dir). `None` means no snapshot configured or
/// readable — the screen stays in its honest empty state.
pub fn load_snapshot_from_env() -> Option<SnapshotBundle> {
    let dir = std::env::var("VAYREN_RESEARCH_SNAPSHOT").ok()?;
    let experiments = Path::new(&dir).join("experiments");
    let (exp, signals, trades) = read_bundle(&experiments).ok()?;
    Some(assemble_bundle(&exp, &(signals, trades)))
}

/// Everything the screen needs to present one executed experiment: the
/// display-ready results plus the inputs that produced them.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct SnapshotBundle {
    pub results: ResearchResults,
    pub hypothesis: String,
    pub question: String,
    pub effect: String,
    pub strategy: String,
    pub universe: String,
    pub symbols: String,
    pub timeframe: String,
    pub side: String,
    pub capital: String,
    pub slip: String,
    pub comm: String,
    pub params: String,
    pub experiment_id: String,
}

fn join_symbols(exp: &serde_json::Value) -> String {
    exp.get("symbols")
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|s| s.as_str().map(str::to_string))
                .collect::<Vec<_>>()
                .join(", ")
        })
        .unwrap_or_default()
}

fn join_params(exp: &serde_json::Value) -> String {
    exp.get("parameters")
        .and_then(|v| v.as_object())
        .map(|o| {
            o.iter()
                .map(|(k, v)| match v.as_f64() {
                    Some(f) => format!("{k}={f}"),
                    None => format!("{k}=?"),
                })
                .collect::<Vec<_>>()
                .join(", ")
        })
        .unwrap_or_default()
}

fn num_string(exp: &serde_json::Value, key: &str) -> String {
    exp.get(key).map_or_else(String::new, |v| match v.as_f64() {
        Some(f) => {
            if f.fract() == 0.0 {
                format!("{}", f as i64)
            } else {
                format!("{f}")
            }
        }
        None => v.as_str().unwrap_or("").to_string(),
    })
}

fn assemble_bundle(
    exp: &serde_json::Value,
    run: &(serde_json::Value, serde_json::Value),
) -> SnapshotBundle {
    let results = results_from_snapshot(exp, &run.0, &run.1);
    let hypothesis = exp
        .get("hypothesis")
        .and_then(|h| h.get("text"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    SnapshotBundle {
        results,
        hypothesis,
        question: str_at(exp, "research_question"),
        effect: str_at(exp, "expected_effect"),
        strategy: str_at(exp, "strategy_id"),
        universe: str_at(exp, "universe"),
        symbols: join_symbols(exp),
        timeframe: str_at(exp, "timeframe"),
        side: str_at(exp, "side"),
        capital: num_string(exp, "initial_capital"),
        slip: num_string(exp, "slippage_pct"),
        comm: num_string(exp, "commission_pct"),
        params: join_params(exp),
        experiment_id: str_at(exp, "experiment_id"),
    }
}

/// Present one executed snapshot: inputs populate the live form (so
/// staleness keeps working) and the results attach to that fingerprint.
pub fn apply_snapshot(state: &mut ResearchState, bundle: SnapshotBundle) {
    if let Some(idx) = state
        .strategies
        .iter()
        .position(|s| s.name == bundle.strategy)
    {
        state.selected = Some(idx);
    }
    for (key, value) in [
        ("universe", bundle.universe.as_str()),
        ("symbols", bundle.symbols.as_str()),
        ("start", ""),
        ("end", ""),
        ("capital", bundle.capital.as_str()),
        ("slip", bundle.slip.as_str()),
        ("comm", bundle.comm.as_str()),
        ("params", bundle.params.as_str()),
    ] {
        if !value.is_empty() {
            state.edit_field(key, value);
        }
    }
    if let Some(idx) = TIMEFRAME_OPTIONS
        .iter()
        .position(|t| *t == bundle.timeframe)
    {
        state.timeframe_idx = idx;
    }
    if let Some(idx) = SIDE_OPTIONS.iter().position(|s| *s == bundle.side) {
        state.side_idx = idx;
    }
    state.hypothesis = bundle.hypothesis;
    state.question = bundle.question;
    state.expected_effect = bundle.effect;
    if state.run == ResearchRun::NoExperiment {
        state.run = ResearchRun::Draft;
    }
    state.experiments = vec![ExperimentItem {
        id: bundle.experiment_id.clone(),
        strategy: bundle.strategy,
        status_label: "✓ COMPLETED".to_string(),
        status_tone: Tone::Positive,
    }];
    state.selected_experiment = Some(0);
    state.apply_result(bundle.results);
}

/// Representative workstation state for the standalone shell: real strategy
/// identity, empty hypothesis, no fabricated results.
pub fn demo_research_state() -> ResearchState {
    let mut state = ResearchState {
        strategies: vec![
            ResearchStrategy {
                name: "OBR".into(),
                description: "Opening Breakout Strategy".into(),
                version: "1.0".into(),
            },
            ResearchStrategy {
                name: "SMA".into(),
                description: "SMA crossover".into(),
                version: "1.0".into(),
            },
        ],
        ..ResearchState::default()
    };
    state.selected = Some(0);
    state
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn obr_selected() -> ResearchState {
        let st = demo_research_state();
        assert!(st.selected.is_some());
        st
    }

    fn completed_with_fp(fp: &str) -> ResearchState {
        let mut st = obr_selected();
        st.hypothesis = "edge persists".into();
        assert!(st.create());
        st.results_fingerprint = Some(fp.into());
        st.results = Some(ResearchResults {
            experiment_id: "EXP-1".into(),
            ..ResearchResults::default()
        });
        st.run = ResearchRun::Completed;
        st
    }

    #[test]
    fn layout_modes_follow_logical_width() {
        assert_eq!(layout_mode(1920.0), LayoutMode::Wide);
        assert_eq!(layout_mode(1180.0), LayoutMode::Wide);
        assert_eq!(layout_mode(1179.0), LayoutMode::Medium);
        assert_eq!(layout_mode(760.0), LayoutMode::Medium);
        assert_eq!(layout_mode(759.0), LayoutMode::Narrow);
        assert_eq!(layout_mode(360.0), LayoutMode::Narrow);
    }

    #[test]
    fn empty_state_is_truthful_before_creation() {
        let st = obr_selected();
        let view = project(&st);
        assert!(view.has_strategy);
        assert_eq!(view.strategy_name, "OBR");
        assert!(!view.show_results);
        assert!(view.metrics.iter().all(|m| m.value == "—"));
        assert_eq!(view.empty_title, "NO RESEARCH EXPERIMENT");
        assert!(!view.run_enabled); // engine bridge unwired: never fakes a run
        assert!(view.run_blocked_reason.contains("bridge"));
    }

    #[test]
    fn form_projection_carries_inputs_and_create_affordance() {
        let st = obr_selected();
        let view = project(&st);
        assert!(view.create_visible);
        assert_eq!(view.strategies.len(), 2);
        assert!(view.strategies[0].selected);
        // 5 grouped sections, 11 inputs total (8 text + 3 selects).
        assert_eq!(view.config_groups.len(), 5);
        assert_eq!(
            view.config_groups
                .iter()
                .map(|group| group.fields.len())
                .sum::<usize>(),
            11
        );
        let execution = view
            .config_groups
            .iter()
            .find(|group| group.title == "EXECUTION")
            .unwrap();
        let tf = execution
            .fields
            .iter()
            .find(|f| f.key == "timeframe")
            .unwrap();
        assert_eq!(tf.kind, 1);
        assert_eq!(tf.options.len(), TIMEFRAME_OPTIONS.len());
        assert_eq!(tf.selected, 3);
    }

    #[test]
    fn creation_requires_hypothesis() {
        let mut st = obr_selected();
        assert!(!st.create());
        assert_eq!(st.run, ResearchRun::NoExperiment);
        st.set_hypothesis("opening momentum persists");
        assert!(st.create());
        assert_eq!(st.run, ResearchRun::Draft);
        assert_eq!(project(&st).empty_title, "NOT EXECUTED");
    }

    #[test]
    fn invalid_config_blocks_creation_honestly() {
        let mut st = obr_selected();
        st.set_hypothesis("h");
        assert!(st.edit_field("start", "2024-05-01"));
        assert!(st.edit_field("end", "2024-01-01"));
        assert!(!st.create());
        assert_eq!(st.run, ResearchRun::Invalid);
        assert!(st.create_error.contains("Start date"));
    }

    #[test]
    fn any_config_edit_marks_completed_stale() {
        let mut st = completed_with_fp("fp-live");
        // Align the stored fingerprint with the live form first.
        st.results_fingerprint = Some(st.live_fingerprint());
        st.refresh_staleness();
        assert!(!st.stale);
        assert_eq!(project(&st).state_label, "✓ COMPLETED");
        assert!(st.edit_field("end", "2024-02-01"));
        assert!(st.stale);
        let view = project(&st);
        assert_eq!(view.state_label, "◐ STALE");
        assert!(view.show_results); // prior evidence stays visible, flagged
    }

    #[test]
    fn run_stays_inert_until_bridge_wired() {
        let mut st = obr_selected();
        st.set_hypothesis("h");
        assert!(st.create());
        assert!(!st.start_run());
        assert_eq!(st.run, ResearchRun::Draft);
        st.engine_wired = true;
        st.run_blocked_reason.clear();
        assert!(st.start_run());
        assert_eq!(project(&st).state_label, "● RUNNING…");
        assert!(st.cancel_run());
        assert_eq!(st.run, ResearchRun::Cancelled);
        // Cancelled twice is a no-op, never corrupts.
        assert!(!st.cancel_run());
    }

    #[test]
    fn apply_result_projects_real_values() {
        let mut st = obr_selected();
        st.set_hypothesis("h");
        assert!(st.create());
        st.engine_wired = true;
        assert!(st.start_run());
        st.apply_result(ResearchResults {
            experiment_id: "EXP-9".into(),
            metrics: vec![Metric {
                label: "NET P&L".into(),
                value: "+₹12.00".into(),
                tone: Tone::Positive,
                emphasized: true,
            }],
            signal_total: 6,
            trade_total: 4,
            conclusion: "Evidence supports an in-sample edge.".into(),
            validation_status: "WARNING".into(),
            ..ResearchResults::default()
        });
        let view = project(&st);
        assert!(view.show_results);
        assert_eq!(view.state_label, "✓ COMPLETED");
        assert_eq!(view.metrics[0].value, "+₹12.00");
        assert_eq!(view.signal_count_line, "6 SIGNALS");
        assert_eq!(view.conclusion, "Evidence supports an in-sample edge.");
        assert_eq!(view.validation_status, "WARNING");
        // Completed work compacts the telemetry strip (state-driven heights).
        assert!(!view.log_expanded);
    }

    // Test helper (restores the pre-rename `field("symbols")` reader the
    // assertions below were written against).
    fn st_symbols(state: &ResearchState) -> String {
        state
            .fields
            .iter()
            .find(|f| f.key == "symbols")
            .map(|f| f.value.clone())
            .unwrap_or_default()
    }

    #[test]
    fn host_snapshot_maps_facts_without_inventing_them() {
        let mut st = ResearchState::default();
        let snapshot = serde_json::json!({
            "strategies": [{"name": "OBR", "description": "Opening Breakout Strategy",
                            "version": "d258c55b"}],
            "experiments": [{"id": "EXP-1", "strategy": "OBR", "status": "DRAFT"}],
            "selected_id": "EXP-1",
            "running": false,
            "status": "DRAFT",
            "log": ["Strategy load: OBR", "Data validation passed"],
            "defaults": {"universe": "NIFTY 500", "symbols": "RELIANCE, TCS",
                         "timeframe": "5m", "start": "2024-01-01", "end": "2024-03-31",
                         "side": "BOTH", "capital": "1000000", "slippage_pct": "0.02",
                         "commission_pct": "0.03", "parameters": "",
                         "hypothesis": "", "research_question": "", "expected_effect": ""},
            "bundle": null
        });
        st.apply_host_snapshot(&snapshot);
        assert_eq!(st.selected, Some(0));
        assert_eq!(st.strategies[0].name, "OBR");
        assert_eq!(st.experiments.len(), 1);
        assert_eq!(st.selected_experiment, Some(0));
        assert_eq!(st.run, ResearchRun::Draft);
        assert!(st.engine_wired && st.run_blocked_reason.is_empty());
        assert_eq!(st.timeframe_idx, 2);
        let symbols_of = |state: &ResearchState| {
            state
                .fields
                .iter()
                .find(|f| f.key == "symbols")
                .map(|f| f.value.clone())
                .unwrap_or_default()
        };
        assert_eq!(symbols_of(&st), "RELIANCE, TCS");
        assert_eq!(st.log.len(), 2);
        // User edits survive later default refreshes; unedited fields follow.
        st.edit_field("symbols", "INFY");
        st.apply_host_snapshot(&snapshot);
        assert_eq!(symbols_of(&st), "INFY");
        // A completed bundle projects real results; stale flag is honored.
        let exp = serde_json::json!({
            "experiment_id": "EXP-2", "strategy_id": "OBR", "status": "COMPLETED",
            "executed_at": "2026-09-14T10:00:00", "config_fingerprint": "fp-exec",
            "result_fingerprint": "rf", "result_summary": {"trade_count": 2,
                                                            "signal_count": 3},
        });
        let done = serde_json::json!({
            "experiments": [{"id": "EXP-2", "strategy": "OBR", "status": "COMPLETED"}],
            "selected_id": "EXP-2", "bundle": {"experiment": exp, "signals": [], "trades": []}
        });
        st.apply_host_snapshot(&done);
        assert_eq!(st.run, ResearchRun::Completed);
        assert!(!st.stale);
        let view = project(&st);
        assert!(view.show_results);
        let stale_done = serde_json::json!({
            "experiments": [{"id": "EXP-2", "strategy": "OBR", "status": "COMPLETED"}],
            "selected_id": "EXP-2", "stale": true,
            "bundle": {"experiment": exp, "signals": [], "trades": []}
        });
        st.apply_host_snapshot(&stale_done);
        assert_eq!(st.run, ResearchRun::Stale);
        assert!(project(&st).show_results);
        // Config payload round-trips the live form for service execution.
        let payload = st.host_config_payload();
        assert_eq!(payload["strategy_name"], "OBR");
        assert_eq!(payload["symbols"], "INFY");
        assert_eq!(payload["timeframe"], "5m");
    }

    #[test]
    fn signal_filter_matches_truthfully() {
        let mut st = obr_selected();
        st.results = Some(ResearchResults {
            signals: vec![
                SignalRow {
                    symbol: "RELIANCE".into(),
                    side: "BUY".into(),
                    ..SignalRow::default()
                },
                SignalRow {
                    symbol: "TCS".into(),
                    side: "SELL".into(),
                    ..SignalRow::default()
                },
            ],
            ..ResearchResults::default()
        });
        assert_eq!(st.filtered_signals().len(), 2);
        st.set_signal_filter("reli");
        let rows = st.filtered_signals();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].symbol, "RELIANCE");
    }

    #[test]
    fn snapshot_loader_reads_engine_bundle() {
        let dir = std::env::temp_dir().join("vayren-research-snap-test");
        let _ = std::fs::remove_dir_all(&dir);
        let exp_dir = dir.join("experiments");
        let run_dir = dir.join("runs").join("EXP-T1");
        std::fs::create_dir_all(&exp_dir).unwrap();
        std::fs::create_dir_all(&run_dir).unwrap();
        std::fs::write(
            exp_dir.join("EXP-T1.json"),
            json!({
                "experiment_id": "EXP-T1",
                "strategy_id": "OBR",
                "status": "COMPLETED",
                "executed_at": "2026-09-13T10:00:00",
                "result_fingerprint": "abc123def456789",
                "config_fingerprint": "cfg789",
                "dataset_version": "dv1",
                "result_summary": {
                    "trade_count": 2, "signal_count": 3,
                    "net_pnl": 120.5, "win_rate": 0.5,
                    "profit_factor": 2.0, "max_drawdown_pct": 1.5,
                    "sharpe": 0.8
                },
                "report": {"conclusion": "Evidence supports an in-sample edge."},
                "analysis": {
                    "robustness": [{
                        "test_type": "parameter_sensitivity",
                        "input": {"param": "p", "value": 1.0},
                        "stability": "PASS",
                        "evidence": {"baseline_expectancy": 1.0, "variant_expectancy": 1.1}
                    }],
                    "validation": {
                        "status": "WARNING", "summary": "Marginal",
                        "oos": {"status": "WARNING"},
                        "warnings": ["in-sample only"]
                    }
                }
            })
            .to_string(),
        )
        .unwrap();
        std::fs::write(
            run_dir.join("run.json"),
            json!({
                "signals": [{
                    "time": "2024-01-02 09:15:00", "symbol": "AAA",
                    "timeframe": "5m", "side": "BUY", "price": 100.0,
                    "event": "BUY", "strategy": "OBR", "experiment": "EXP-T1"
                }],
                "trades": [{
                    "entry_time": "2024-01-02 09:15:00",
                    "exit_time": "2024-01-02 10:15:00",
                    "side": "LONG", "quantity": 10.0, "pnl": 120.5,
                    "exit_reason": "SIGNAL", "bars_held": 12
                }]
            })
            .to_string(),
        )
        .unwrap();

        let results = load_snapshot(&exp_dir).expect("engine bundle must load");
        assert_eq!(results.experiment_id, "EXP-T1");
        assert_eq!(results.trade_total, 2);
        assert_eq!(results.signal_total, 3);
        assert_eq!(results.signals.len(), 1);
        assert_eq!(results.trades.len(), 1);
        assert_eq!(results.trades[0].pnl_tone, Tone::Positive);
        assert!(results
            .metrics
            .iter()
            .any(|m| m.label == "NET P&L" && m.value.contains("120.50")));
        assert_eq!(results.validation_status, "WARNING");
        assert_eq!(results.robustness.len(), 1);
        assert!(results.fingerprint_line.contains("abc123def456"));
        assert_eq!(results.conclusion, "Evidence supports an in-sample edge.");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn snapshot_loader_refuses_without_completed() {
        let dir = std::env::temp_dir().join("vayren-research-snap-empty");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        assert!(load_snapshot(&dir).is_err());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn inr_formatting_groups_indian_style() {
        assert_eq!(fmt_inr(185236.4), "+₹1,85,236.40");
        assert_eq!(fmt_inr(-32034.27), "−₹32,034.27");
        assert_eq!(fmt_inr(999.0), "+₹999.00");
    }
}
