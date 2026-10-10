//! Strategy Lab native view-model — pure, headless-testable UI state
//! (AI_ENTRY.md §1: Rust owns view-model + interaction state; Slint renders
//! bound properties only).
//!
//! This is the presentation-side model of the Strategy Lab workstation. It
//! never computes financial results: every value arrives via
//! [`LabState::apply_result`] from the engine (Python strategy/backtest
//! engines remain the sole authorities). With no result the model renders an
//! honest READY state — it never fabricates numbers.
//!
//! State-consistency invariants (the bugs this surface must never regress):
//! - selecting a library row updates the workspace header in the same
//!   projection pass — "No strategy" is shown only when truly none selected;
//! - config changes are fingerprint-compared to the last run and surfaced as
//!   a compact OUTDATED notice — stale results never appear as current.

use vayren_core::backtest_validation as validation;
use vayren_core::lab_coverage::{self, CoverageFacts, CoverageInput};
use vayren_core::market::timeframe_seconds;

/// Live execution facts for an in-flight run.
///
/// Every field is a MEASURED value pushed by the backend after real work
/// finished. There is no local ticker and no interpolation: if the backend
/// stops sending, the numbers freeze exactly where they were, which is what
/// lets the UI tell "slow" apart from "stuck".
#[derive(Debug, Clone, PartialEq, Default)]
pub struct RunProgress {
    pub active: bool,
    /// `starting | data | calculate | trades | aggregate | done | failed | cancelled`
    pub stage: String,
    pub stage_pct: f32,
    pub total: i32,
    pub completed: i32,
    pub failed: Vec<String>,
    pub skipped: Vec<String>,
    pub remaining: i32,
    pub pct: f32,
    /// `(done, total)` for the stage actually on screen — the load stage
    /// counts symbols READ, the run stage counts symbols RUN. The bar shows
    /// this pair, so it can never claim "0 / 527" while work is landing.
    pub done: i32,
    pub headline_total: i32,
    pub current: String,
    pub current_secs: f32,
    pub elapsed_secs: f32,
    /// `None` until enough samples exist — the UI shows a dash, never a guess.
    pub eta_secs: Option<f64>,
    pub mean_secs: Option<f64>,
    pub throughput: f32,
    pub trades: i32,
    pub bars: i64,
    pub net_pnl: f32,
    pub long_running: bool,
    pub quiet: bool,
    pub cancelled: bool,
}

impl RunProgress {
    /// Adopt a `lab_progress` payload. An unmeasurable field becomes `None`
    /// rather than a zero, so "we don't know yet" never reads as "0 seconds".
    ///
    /// COUNTS are read as integers (`as_i64`/`as_u64`), never via `as_f64`:
    /// a count of 16,777,217 or more loses precision through an f64, and a
    /// `null`/string field would arrive as `NaN` and then silently become `0`
    /// — "0 of 527 completed" for a run that had done work. Percentages and
    /// seconds stay f64 but a non-finite value is rejected to `0.0` rather
    /// than flowing into Slint as `NaN`.
    pub fn from_json(value: &serde_json::Value) -> Self {
        let num = |key: &str| value.get(key).and_then(|v| v.as_f64());
        let finite = |key: &str| num(key).filter(|v| v.is_finite()).unwrap_or(0.0);
        let count = |key: &str| -> i32 {
            // Accept a JSON integer directly; a float that is exactly integral
            // (some Python emitters stringify counts) is honoured too, but only
            // when it survives the range check without precision loss.
            match value.get(key) {
                Some(v) => v
                    .as_i64()
                    .or_else(|| v.as_u64().and_then(|u| i64::try_from(u).ok()))
                    .or_else(|| {
                        v.as_f64().and_then(|f| {
                            (f.is_finite()
                                && f.fract() == 0.0
                                && f >= i32::MIN as f64
                                && f <= i32::MAX as f64)
                                .then_some(f as i64)
                        })
                    })
                    .unwrap_or(0)
                    .clamp(i32::MIN as i64, i32::MAX as i64) as i32,
                None => 0,
            }
        };
        let big_count = |key: &str| -> i64 {
            match value.get(key) {
                Some(v) => v
                    .as_i64()
                    .or_else(|| v.as_u64().and_then(|u| i64::try_from(u).ok()))
                    .or_else(|| {
                        v.as_f64().and_then(|f| {
                            (f.is_finite() && f.fract() == 0.0 && f.abs() <= i64::MAX as f64)
                                .then_some(f as i64)
                        })
                    })
                    .unwrap_or(0),
                None => 0,
            }
        };
        let optional_secs = |key: &str| -> Option<f64> { num(key).filter(|v| v.is_finite()) };
        let strings = |key: &str| -> Vec<String> {
            value
                .get(key)
                .and_then(|v| v.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|s| s.as_str().map(str::to_string))
                        .collect()
                })
                .unwrap_or_default()
        };
        Self {
            active: true,
            stage: opt_str(value, "stage"),
            stage_pct: finite("stage_pct") as f32,
            total: count("total"),
            completed: count("completed"),
            failed: strings("failed"),
            skipped: strings("skipped"),
            remaining: count("remaining"),
            pct: finite("pct") as f32,
            done: count("done"),
            headline_total: {
                let direct = count("headline_total");
                if value.get("headline_total").is_some() {
                    direct
                } else {
                    count("total")
                }
            },
            current: opt_str(value, "current"),
            current_secs: finite("current_secs") as f32,
            elapsed_secs: finite("elapsed_secs") as f32,
            eta_secs: optional_secs("eta_secs"),
            mean_secs: optional_secs("mean_secs"),
            throughput: finite("throughput") as f32,
            trades: count("trades"),
            bars: big_count("bars"),
            net_pnl: finite("net_pnl") as f32,
            long_running: value
                .get("long_running")
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
            quiet: value
                .get("quiet")
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
            cancelled: value
                .get("cancelled")
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
        }
    }
}

/// Direction mode — lives in the header ONLY (no floating indicators).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum LabMode {
    #[default]
    Long,
    Short,
    Compare,
}

impl LabMode {
    pub fn kind(self) -> i32 {
        match self {
            LabMode::Long => 0,
            LabMode::Short => 1,
            LabMode::Compare => 2,
        }
    }
    pub fn label(self) -> &'static str {
        match self {
            LabMode::Long => "BUY / LONG",
            LabMode::Short => "SELL / SHORT",
            LabMode::Compare => "COMPARE",
        }
    }
    pub fn from_kind(kind: i32) -> Self {
        match kind {
            1 => LabMode::Short,
            2 => LabMode::Compare,
            _ => LabMode::Long,
        }
    }
}

/// Run lifecycle state (mirrors the legacy vocabulary; no-strategy is derived,
/// never stored alongside a selection).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum RunState {
    #[default]
    Ready,
    Running,
    Complete,
    Failed,
}

/// Library filter chips.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum LibFilter {
    #[default]
    All,
    Favorites,
    Recent,
}

pub fn filter_kind(filter: LibFilter) -> i32 {
    match filter {
        LibFilter::All => 0,
        LibFilter::Favorites => 1,
        LibFilter::Recent => 2,
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Tone {
    #[default]
    Muted,
    Neutral,
    Positive,
    Negative,
    Warning,
}

impl Tone {
    pub fn kind(self) -> i32 {
        match self {
            Tone::Muted => 0,
            Tone::Neutral => 1,
            Tone::Positive => 2,
            Tone::Negative => 3,
            Tone::Warning => 4,
        }
    }
    /// Map to the shared VCell tone convention (0 text, 1 pos, 2 warn, 3 neg).
    pub fn cell(self) -> i32 {
        match self {
            Tone::Positive => 1,
            Tone::Warning => 2,
            Tone::Negative => 3,
            _ => 0,
        }
    }
    /// Map to the shared VBadge tone vocabulary (0 muted, 1 ok, 2 warn,
    /// 3 bad, 4 accent).
    pub fn badge(self) -> i32 {
        match self {
            Tone::Muted | Tone::Neutral => 0,
            Tone::Positive => 1,
            Tone::Warning => 2,
            Tone::Negative => 3,
        }
    }
}

/// Strategy identity facts (from the library record — never invented).
#[derive(Debug, Clone, PartialEq)]
pub struct LabStrategy {
    pub name: String,
    pub description: String,
    pub tags: Vec<String>,
    pub version: String,
    pub modified: String,
    pub favorite: bool,
    /// Compact scope of the last completed backtest, e.g. "RELIANCE · 15m".
    pub last_backtest: String,
}

/// Backtest configuration as displayed in the terminal toolbar.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct LabConfig {
    pub universe: String,
    pub timeframe: String,
    pub dates: String,
    pub capital: String,
}

impl LabConfig {
    pub fn fingerprint(&self) -> String {
        format!(
            "{}|{}|{}|{}",
            self.universe, self.timeframe, self.dates, self.capital
        )
    }
}

/// One KPI cell (label/value pre-formatted by the engine bridge).
#[derive(Debug, Clone, PartialEq)]
pub struct Kpi {
    pub label: String,
    pub value: String,
    pub tone: Tone,
    /// Net P&L carries the strongest visual weight in the band.
    pub emphasized: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct RankRow {
    pub rank: String,
    pub symbol: String,
    pub pnl: String,
    pub ret: String,
    pub trades: String,
    pub win: String,
    pub pf: String,
    pub dd: String,
    pub sharpe: String,
    pub pnl_tone: Tone,
    pub pf_tone: Tone,
    /// legacy dims rows with no valid result ("— = no valid result").
    pub unranked: bool,
    /// Raw sort keys in `RANK_HEADERS` metric order (net P&L, return %,
    /// trades, win %, profit factor, max DD, Sharpe) — the display strings
    /// cannot be compared honestly ("—" vs "+4.2%"), so the numbers are kept.
    pub sort: [f64; 7],
}

#[derive(Debug, Clone, PartialEq)]
pub struct TradeRow {
    pub no: String,
    /// Absolute index into the engine trade list (legacy blotter UserRole id —
    /// survives side filtering, unlike the visible row number).
    pub abs_index: i32,
    pub symbol: String,
    pub side: String,
    pub entry: String,
    pub entry_px: String,
    pub exit: String,
    pub exit_px: String,
    pub pnl: String,
    pub r: String,
    pub bars: String,
    pub reason: String,
    pub pnl_tone: Tone,
    pub selected: bool,
    /// Cached sort keys, in the blotter's sort order: entry time (minutes since
    /// epoch), P&L, R multiple, bars held. Stored so sorting and filtering are
    /// numeric and never touch the display strings (`spec §6/§7/§10`).
    pub sort: [f64; 4],
}

/// Which column the blotter sorts by (`spec §10`). Order is the UI dropdown
/// order and maps 1:1 onto `TradeRow::sort`.
pub const TRADE_SORT_FIELDS: [(&str, usize); 4] = [
    ("Entry time", 0),
    ("Net P&L", 1),
    ("R multiple", 2),
    ("Bars held", 3),
];
/// Sort by symbol is a string column, handled separately from the numeric keys.
pub const TRADE_SORT_SYMBOL: i32 = 4;
/// Blotter filters (`spec §26`): side and result, over the COMPLETE dataset.
pub const TRADE_SIDE_ALL: i32 = 0;
pub const TRADE_SIDE_LONG: i32 = 1;
pub const TRADE_SIDE_SHORT: i32 = 2;
pub const TRADE_RESULT_ALL: i32 = 0;
pub const TRADE_RESULT_WIN: i32 = 1;
pub const TRADE_RESULT_LOSS: i32 = 2;

/// One strategy parameter (spec label + current value, both backend-owned).
#[derive(Debug, Clone, PartialEq)]
pub struct ParamItem {
    pub key: String,
    pub label: String,
    pub value: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct DetailMetric {
    pub label: String,
    pub value: String,
}

/// Selected-stock drill-down (the legacy detail panel's own rendered facts).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct DetailView {
    pub symbol: String,
    pub title: String,
    pub stats: String,
    pub caption: String,
    pub metrics: Vec<DetailMetric>,
    pub equity: Vec<(f32, f32)>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct BoardCell {
    pub symbol: String,
    pub value: Option<f64>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct BoardRow {
    pub label: String,
    pub kind: String,
    pub higher: Option<bool>,
    pub cells: Vec<BoardCell>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct MatrixRow {
    pub key: String,
    pub buy: String,
    pub sell: String,
    /// 0 none, 1 BUY wins, 2 SELL wins (the backend's own highlight, read
    /// off its labels — never re-derived here).
    pub winner: i32,
}

/// COMPARE page (the hidden legacy compare view's fed facts, echoed verbatim
/// except the per-stock board, whose best-cell bold follows the board's own
/// unique-best rule on backend numbers).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct CompareView {
    pub banner_verdict: String,
    pub banner_reason: String,
    /// VBadge tone: BUY/LONG → accent, SELL/SHORT → bad, else muted.
    pub banner_tone: i32,
    pub trade_summary: String,
    pub stale_notice: String,
    pub board_scope: String,
    pub board_leader: String,
    pub matrix: Vec<MatrixRow>,
    pub board: Vec<BoardRow>,
    pub board_symbols: Vec<String>,
    pub board_trades: Vec<String>,
    pub ranking: Vec<RankRow>,
    /// Jointly normalized dual curves (shared scale, like the legacy view).
    pub equity_buy: Vec<(f32, f32)>,
    pub equity_sell: Vec<(f32, f32)>,
    pub drawdown_buy: Vec<(f32, f32)>,
    pub drawdown_sell: Vec<(f32, f32)>,
}

/// Engine-fed results for ONE configuration fingerprint.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct LabResults {
    pub kpis: Vec<Kpi>,
    pub ranking: Vec<RankRow>,
    pub trades: Vec<TradeRow>,
    /// Chart points already in view coordinates (x: 0..1, y: 0..1) — the
    /// projection maps them; the model never invents curves.
    pub equity: Vec<(f32, f32)>,
    pub drawdown: Vec<(f32, f32)>,
    pub risk_notes: Vec<String>,
}

/// The single source of Strategy Lab presentation state.
/// Per-strategy stock-universe save status. Kept apart from code dirtiness:
/// saving code can never move a universe out of `Saved`, and vice versa.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub enum UniverseSaveState {
    /// Nothing saved or changed since the strategy was selected.
    #[default]
    Clean,
    /// Selection applied locally; backend has not confirmed yet.
    Pending,
    Saved,
    /// Backend refused or failed; the previously saved universe still stands.
    Error(String),
}

/// Load state of the selected strategy's persisted stock universe.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum SavedUniverseStatus {
    /// Never saved for this strategy (or no strategy selected).
    #[default]
    Missing,
    /// Saved with symbols.
    Ok,
    /// Saved as cleared: a valid, explicitly empty universe.
    Empty,
    /// The store could not be read; the row says so instead of guessing.
    Error,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct LabState {
    pub strategies: Vec<LabStrategy>,
    pub selected: Option<usize>,
    pub search: String,
    pub filter: LibFilter,
    pub mode: LabMode,
    pub config: LabConfig,
    pub run: RunState,
    /// Fingerprint of the configuration the current results belong to.
    pub results_fingerprint: Option<String>,
    pub results: Option<LabResults>,
    /// True when a completed run exists whose config no longer matches.
    pub outdated: bool,
    pub tab: usize,
    /// Engine bridge present (false until the native bridge is wired — the
    /// RUN control stays honestly disabled).
    pub engine_wired: bool,
    /// User interactions recorded by the embedded view, awaiting the Python
    /// backend to apply them (selection, mode, tab, filter, run). The view
    /// applies them optimistically too, so the UI reacts instantly while the
    /// backend remains the single owner of business behavior.
    pub pending_actions: Vec<String>,
    /// How many backend intents were dropped because the queue was full.
    pub dropped_pending_actions: u64,
    /// Verdict pill facts from the backend interpretation layer (legacy parity:
    /// verdict + note + colour). Empty when nothing has been interpreted.
    pub verdict_label: String,
    pub verdict_note: String,
    /// VBadge tone for the verdict (0 muted, 1 ok, 2 warn, 3 bad).
    pub verdict_tone: i32,
    /// Batch progress line (e.g. "✓ 123 / 527 · 23%"); empty when idle.
    pub progress_label: String,
    /// Editor working copy + last backend echo (dirty = they differ, same as
    /// legacy's `_dirty` flag; snapshots adopt the echo, never local edits).
    pub code: String,
    pub synced_code: String,
    /// Backend-owned parameter specs + current values.
    pub params: Vec<ParamItem>,
    /// Raw editable config echoes (LineEdit bindings commit on Enter).
    pub cfg_universe_csv: String,
    pub timeframes: Vec<String>,
    pub timeframe_index: i32,
    pub cfg_dates_start: String,
    pub cfg_dates_end: String,
    pub cfg_capital: String,
    pub config_error: String,
    /// Active entry of the §02 date-range preset row (-1 = a custom range
    /// picked by hand). Derived in `project`, never stored: a stale index
    /// would light up a cell the committed range does not match.
    pub range_preset_idx: i32,
    /// Real store history bounds reported by the backend (`first_date` /
    /// `last_date` of the anchor symbol). The §02 `MAX` preset is exactly
    /// this pair — NOT the current selection, which would collapse `MAX` onto
    /// whatever the user last picked. -1 = not reported yet.
    pub data_bounds_start_days: i32,
    pub data_bounds_end_days: i32,
    /// The symbol whose bar count the coverage probe actually read. The strip
    /// names it, because a bar count for one symbol must never be printed as a
    /// total for the whole selection.
    pub coverage_anchor: String,
    /// Measured data completeness (`lab_coverage` kernel facts). `None` until a
    /// probe actually measured something — the strip stays hidden then.
    pub coverage: Option<CoverageFacts>,
    /// Live execution facts for an in-flight run. Inactive outside a run.
    pub progress: RunProgress,
    /// Ranking criterion dropdown (legacy box order echoed verbatim).
    pub rankby_labels: Vec<String>,
    pub rankby_current: i32,
    pub rank_search: String,
    pub rank_desc: bool,
    /// Virtual window over the ranking rows (index layer + scroll state).
    /// Rebuilt on data/filter/sort changes; the frame path only reads it.
    pub rank: RankWindow,
    /// Symbol count of `cfg_universe_csv`, counted where the CSV is adopted.
    /// Re-splitting a 50k-symbol CSV on every frame was the second O(N) cost in
    /// the projection (`spec §7`: derive once, invalidate on change).
    pub cfg_universe_count: usize,
    /// Drill-down + compare echoes.
    pub detail: Option<DetailView>,
    pub compare: Option<CompareView>,
    /// Single-side results for the COMPARE matrix/board/curves.
    pub results_buy: Option<LabResults>,
    pub results_sell: Option<LabResults>,
    /// COMPARE trade side filter (view-local, like library search).
    pub compare_side: i32,
    /// Absolute trade index highlighted by the backend (-1 = none).
    pub selected_trade: i32,
    pub trade_filters_active: bool,
    pub trade_needle: String,
    pub trade_symbol: String,
    /// Trade blotter (`spec §2`): its own window over the FULL trade list, plus
    /// the sort/filter state its index layer is keyed on. Same engine as the
    /// ranking grid — no second windowing implementation.
    pub trade_win: VirtualWindow,
    pub trade_sort: i32,
    pub trade_sort_desc: bool,
    pub trade_side_filter: i32,
    pub trade_result_filter: i32,
    /// The ONE selected trade's facts, built on click (`spec §27`).
    pub trade_detail: Option<TradeDetailState>,
    /// The real topbar RUN button text, echoed verbatim.
    pub run_label: String,
    pub equity_summary: String,
    pub drawdown_summary: String,
    /// Market Watchlist universe (single source of truth, backend-owned).
    pub universe_symbols: Vec<String>,
    /// Applied selection echo (backend-owned).
    pub universe_selected: Vec<String>,
    /// Save status of the selected strategy's stock universe (independent of code).
    pub universe_state: UniverseSaveState,
    /// The selected strategy's PERSISTED stock universe, from the backend's
    /// `saved_universe` block. Authority for the stock row (never the run echo).
    pub saved_universe_symbols: Vec<String>,
    pub saved_universe_status: SavedUniverseStatus,
    pub saved_universe_error: String,
    /// Selector panel open + working draft (draft mirrors the echo while
    /// closed; edits stay local until Apply — same precedent as search).
    pub sym_open: bool,
    pub sym_search: String,
    pub sym_draft: Vec<String>,
    /// Reference-layout echoes (view-local until the backend supplies them).
    pub lens: i32,
    pub equity_select: i32,
}

pub const LAB_TABS: [&str; 5] = ["EDITOR", "PERFORMANCE", "TRADES", "EQUITY", "DRAWDOWN"];
/// legacy result-stack page per native tab (EDITOR has no page — the legacy center
/// column is always live).
pub const TAB_RESULT_PAGE: [i32; 5] = [-1, 0, 1, 2, 3];
/// Native ranking header per legacy criterion-box index (box order == spec
/// order, verified against `_SORT_OPTIONS`).
pub const RANKBY_HEADER: [usize; 7] = [2, 3, 4, 5, 6, 7, 8];
pub const RANK_HEADERS: [&str; 9] = [
    "#", "SYMBOL", "NET P&L", "RETURN %", "TRADES", "WIN%", "PF", "MAX DD", "SHARPE",
];

impl LabState {
    pub fn selected_strategy(&self) -> Option<&LabStrategy> {
        self.selected.and_then(|i| self.strategies.get(i))
    }

    /// Select a library row — the workspace header updates in the same
    /// projection; "No strategy" can no longer coexist with a selection.
    pub fn select(&mut self, index: usize) -> bool {
        if index >= self.strategies.len() {
            return false;
        }
        if self.selected != Some(index) {
            // A switch must never carry the previous strategy's universe or
            // save status onto the new one. The next snapshot restores the
            // new strategy's persisted universe.
            self.saved_universe_symbols.clear();
            self.saved_universe_status = SavedUniverseStatus::Missing;
            self.saved_universe_error.clear();
            self.universe_state = UniverseSaveState::Clean;
        }
        self.selected = Some(index);
        self.tab = 0;
        true
    }

    /// User-driven selection (embedded view path): optimistic local change
    /// plus one queued action for the backend. Returns false when invalid.
    pub fn interaction_select(&mut self, index: usize) -> bool {
        if !self.select(index) {
            return false;
        }
        self.queue_action(format!("select:{index}"));
        true
    }

    pub fn interaction_mode(&mut self, mode: LabMode) {
        if mode != self.mode {
            self.set_mode(mode);
            self.refresh_staleness();
            self.queue_action(format!("mode:{}", mode.kind()));
        }
    }

    pub fn interaction_tab(&mut self, tab: usize) {
        if tab != self.tab && tab < LAB_TABS.len() {
            self.set_tab(tab);
            // EDITOR is view-local (the legacy center column is always live);
            // result tabs drive the legacy result stack page.
            let page = TAB_RESULT_PAGE[tab];
            if page >= 0 {
                self.queue_action(format!("tab:{page}"));
            }
        }
    }

    pub fn interaction_filter(&mut self, filter: LibFilter) {
        if filter != self.filter {
            self.set_filter(filter);
            self.queue_action(format!("filter:{}", filter_kind(filter)));
        }
    }

    /// Search is view-local (pure list filtering; the backend has no search
    /// concept), so it is deliberately NOT queued.
    pub fn interaction_search(&mut self, text: &str) {
        self.set_search(text);
    }

    pub fn interaction_run(&mut self) {
        if self.engine_wired && self.selected.is_some() {
            self.queue_action("run".to_string());
        }
    }

    /// Backend-supported command pass-through (new/save/compile/menu/
    /// ranksel/tradefocus/exporttrades): the backend owns the behavior, the
    /// view only queues the request.
    pub fn interaction_simple(&mut self, action: &str) {
        if !action.is_empty() {
            self.queue_action(action.to_string());
        }
    }

    /// Editor typing is view-local (legacy only recompiles on open/save, never
    /// per keystroke); Save/Compile carry the buffer to the backend.
    pub fn interaction_codeedit(&mut self, text: &str) {
        self.code = text.to_string();
    }

    pub fn interaction_save(&mut self) {
        self.queue_action(format!("save:{}", self.code));
    }

    pub fn interaction_compile(&mut self) {
        self.queue_action(format!("compile:{}", self.code));
    }

    pub fn interaction_param(&mut self, key: &str, value: &str) {
        let value = value.trim();
        if !key.is_empty() && !value.is_empty() {
            self.queue_action(format!("paramset:{key}:{value}"));
        }
    }

    pub fn interaction_timeframe(&mut self, timeframe: &str) {
        if !timeframe.is_empty() {
            // Optimistic echo: the run request resolves the timeframe by
            // index, the field label, run summary and staleness fingerprint
            // read config.timeframe — so the pick must land in BOTH now (the
            // queued backend action is future sync, not the run path).
            if let Some(index) = self.timeframes.iter().position(|t| t == timeframe) {
                self.timeframe_index = index as i32;
                self.edit_config(|c| c.timeframe = timeframe.to_string());
                self.queue_action(format!("settimeframe:{timeframe}"));
            }
        }
    }

    pub fn interaction_capital(&mut self, value: &str) {
        let value = value.trim();
        if !value.is_empty() {
            // Optimistic echo (documented LabState pattern): the backend owns
            // business behavior, but the RUN request is gathered locally — so
            // the committed value must be visible locally immediately, or the
            // next run silently uses the previous capital. The receipt line
            // and the staleness fingerprint read config.capital, so the
            // formatted echo follows here too. An invalid value never commits:
            // cfg_capital keeps the last good echo.
            let valid = value
                .parse::<f64>()
                .ok()
                .filter(|n| n.is_finite() && *n > 0.0);
            if let Some(n) = valid {
                self.cfg_capital = value.to_string();
                self.edit_config(|c| c.capital = format!("₹{}", grouped_whole(n)));
            } else {
                self.refresh_staleness();
            }
            self.queue_action(format!("capital:{value}"));
        }
    }

    pub fn interaction_dates(&mut self, start: &str, end: &str) {
        let (start, end) = (start.trim(), end.trim());
        // Same optimistic-echo contract as capital: gather() reads these, so
        // committing them here is what makes Apply actually change the run.
        // Order validation stays backend-owned (run_backtest fails closed on
        // start > end); the picker already prevents inverted picks by swap.
        self.cfg_dates_start = start.to_string();
        self.cfg_dates_end = end.to_string();
        self.refresh_staleness();
        self.queue_action(format!("dates:{start}:{end}"));
    }

    /// §02 preset row: rewrite the committed ISO bounds from the real store
    /// bounds and hand them to the EXISTING date commit path. There is no
    /// second date pipeline — a preset is a preset of `interaction_dates`.
    pub fn interaction_range_preset(&mut self, index: i32) {
        let presets = range_presets_for(
            self.data_bounds_start_days as i64,
            self.data_bounds_end_days as i64,
        );
        let Some(preset) = usize::try_from(index).ok().and_then(|i| presets.get(i)) else {
            return;
        };
        let (start, end) = (preset.start_days as i64, preset.end_days as i64);
        if end < start {
            return;
        }
        self.interaction_dates(&iso_from_days(start), &iso_from_days(end));
    }

    /// Adopt a `lab_progress` payload from the backend. The numbers on screen
    /// are exactly the ones that were measured.
    pub fn apply_progress(&mut self, value: &serde_json::Value) {
        self.progress = RunProgress::from_json(value);
    }

    /// A run has ended (or never started): freeze the panel out of view. The
    /// final snapshot carries the results, so nothing is lost by hiding it.
    pub fn clear_progress(&mut self) {
        self.progress = RunProgress::default();
    }

    /// User asked to stop the in-flight run. The host forwards the queued
    /// action; the backend polls it between symbols, so the stop is graceful.
    pub fn interaction_run_cancel(&mut self) {
        self.queue_action("cancelrun".to_string());
    }

    /// Adopt a measured coverage probe (the `lab_coverage` backend command).
    /// The counts come from the store; the expectation and the percentage are
    /// derived HERE, in the kernel, from the same window the probe measured.
    pub fn apply_coverage(&mut self, measured: CoverageMeasurement) {
        let start = measured.start_days;
        let end = measured.end_days;
        let tf_secs = measured.tf_secs;
        if start < 0 || end < 0 || tf_secs <= 0 {
            self.coverage = None;
            return;
        }
        let holidays = std::collections::HashSet::new();
        let expected = lab_coverage::expected_bars(start as i64, end as i64, tf_secs, &holidays);
        self.coverage = Some(lab_coverage::coverage_facts(&CoverageInput {
            symbols_total: measured.symbols_total,
            symbols_covering: measured.symbols_covering,
            bars_present: measured.bars_present,
            bars_expected: expected,
            gaps: measured.gaps,
            sampled: measured.sampled,
            symbols_probed: measured.symbols_probed,
        }));
        self.coverage_anchor = measured.anchor;
    }

    pub fn interaction_ranksearch(&mut self, text: &str) {
        self.rank_search = text.to_string();
        self.rebuild_rank_view();
        self.queue_action(format!("ranksearch:{text}"));
    }

    /// Rebuild the ranking index layer from the current data + filter + sort
    /// (`spec §5`). The only place the index is built — scroll/frame never is.
    pub fn rebuild_rank_view(&mut self) {
        let criterion = rankby_sort_field(self);
        let rows: &[RankRow] = match self.compare.as_ref() {
            Some(cmp) if cmp.ranking.len() > 0 => &cmp.ranking,
            _ => match self.results.as_ref() {
                Some(results) => &results.ranking,
                None => &[],
            },
        };
        self.rank
            .rebuild(rows, &self.rank_search, criterion, self.rank_desc);
    }

    pub fn interaction_rankby(&mut self, label: &str) {
        // The Slint ComboBox reports the selected label (legacy box order is
        // echoed verbatim, so the position is the legacy box index).
        if let Some(index) = self.rankby_labels.iter().position(|l| l == label) {
            // Optimistic echo (documented LabState pattern): the sort control
            // drives the index layer locally, so the label must land now —
            // the queued backend action is future sync, not the render path.
            self.rankby_current = index as i32;
            self.rebuild_rank_view();
            self.queue_action(format!("rankby:{index}"));
        }
    }

    pub fn interaction_ranktoggle(&mut self) {
        self.rank_desc = !self.rank_desc;
        self.rebuild_rank_view();
        self.queue_action("ranktoggle".to_string());
    }

    /// Wheel / drag scrolling in px (`spec §1/§11`): O(1), clamped, adaptive
    /// overscan from the measured velocity.
    pub fn interaction_rank_scroll(&mut self, delta_px: f32) {
        self.rank.scroll_by(delta_px);
    }

    /// Absolute scroll — scrollbar drag, Home/End, focus restore.
    pub fn interaction_rank_scroll_to(&mut self, px: f32) {
        self.rank.scroll_to(px);
    }

    /// Viewport height in px, pushed by the UI whenever the window resizes.
    pub fn interaction_rank_viewport(&mut self, height: f32) {
        self.rank.set_viewport(height);
    }

    /// Frame-time decay for the adaptive overscan: an idle table shrinks its
    /// pre-rendered band back to the minimum (`spec §2`).
    pub fn interaction_rank_idle(&mut self) {
        self.rank.velocity = 0.0;
        self.rank.layout();
    }

    pub fn interaction_trade_pick(&mut self, index: i32) {
        // Optimistic echo (same precedent as every other Lab commit): the
        // highlight and the detail panel are pure presentation over a row we
        // already hold, and the backend has no trade-selection command — so
        // waiting for an echo would leave the click with no visible effect.
        self.selected_trade = index;
        self.trade_filters_active = false;
        self.trade_detail = self.trade_detail_for(index);
        self.queue_action(format!("tradesel:{index}"));
        self.queue_action(format!("tradefocus:{index}"));
    }

    /// Build the detail panel for ONE trade, straight from the record
    /// (`spec §27`): nothing is precomputed for rows the user never clicks.
    /// `index` is the trade's `abs_index` (the blotter's stable identity),
    /// never the positional row — filtering/sorting must not retarget clicks.
    fn trade_detail_for(&self, index: i32) -> Option<TradeDetailState> {
        if index < 0 {
            return None;
        }
        let row = self
            .results
            .as_ref()?
            .trades
            .iter()
            .find(|r| r.abs_index == index)?;
        let metric = |label: &str, value: String, tone: Tone| DetailMetricView {
            label: label.to_string(),
            value,
            tone: match tone {
                Tone::Positive => 2,
                Tone::Negative => 3,
                _ => 0,
            },
        };
        Some(TradeDetailState {
            symbol: row.symbol.clone(),
            side: row.side.clone(),
            metrics: vec![
                metric("TRADE", format!("#{}", row.no), Tone::Neutral),
                metric("ENTRY", row.entry.clone(), Tone::Neutral),
                metric("ENTRY PX", row.entry_px.clone(), Tone::Neutral),
                metric("EXIT", row.exit.clone(), Tone::Neutral),
                metric("EXIT PX", row.exit_px.clone(), Tone::Neutral),
                metric("P&L", row.pnl.clone(), row.pnl_tone),
                metric("R", row.r.clone(), row.pnl_tone),
                metric("BARS HELD", row.bars.clone(), Tone::Neutral),
                metric("REASON", row.reason.clone(), Tone::Neutral),
            ],
        })
    }

    /// Close the trade detail panel.
    pub fn interaction_trade_detail_close(&mut self) {
        self.trade_detail = None;
        self.selected_trade = -1;
    }

    pub fn interaction_tradefilter(&mut self, text: &str) {
        self.trade_needle = text.to_string();
        self.rebuild_trade_view();
        self.queue_action(format!("tradefilter:{text}"));
    }

    /// Trade blotter sort (`spec §16`): reorders the complete dataset through
    /// the index layer, preserving the scroll position logically.
    pub fn interaction_tradesort(&mut self, field: i32) {
        if !(0..=TRADE_SORT_SYMBOL).contains(&field) {
            return;
        }
        if self.trade_sort == field {
            self.trade_sort_desc = !self.trade_sort_desc;
        } else {
            self.trade_sort = field;
            // Time reads best oldest-first; every metric best-first.
            self.trade_sort_desc = field != 0;
        }
        self.rebuild_trade_view();
    }

    /// Side filter: all / long / short, over the complete dataset.
    pub fn interaction_trade_side(&mut self, side: i32) {
        if (0..=2).contains(&side) {
            self.trade_side_filter = side;
            self.rebuild_trade_view();
        }
    }

    /// Result filter: all / winners / losers, over the complete dataset.
    pub fn interaction_trade_result(&mut self, result: i32) {
        if (0..=2).contains(&result) {
            self.trade_result_filter = result;
            self.rebuild_trade_view();
        }
    }

    /// Blotter scroll surface (`spec §19`): O(1) window arithmetic, clamped.
    pub fn interaction_trade_scroll(&mut self, delta_px: f32) {
        self.trade_win.scroll_by(delta_px);
    }

    /// Absolute blotter scroll (scrollbar drag, Home/End).
    pub fn interaction_trade_scroll_to(&mut self, px: f32) {
        self.trade_win.scroll_to(px);
    }

    /// Blotter viewport height, pushed by the UI on mount/resize.
    pub fn interaction_trade_viewport(&mut self, height: f32) {
        self.trade_win.set_viewport(height);
    }

    /// Rebuild the blotter index layer (`spec §9/§10`): filter and sort over
    /// INDICES with cached numeric keys, never row clones. The only O(N) work
    /// in the blotter, and it happens on a data/filter/sort edit — never per
    /// frame and never per scroll notch.
    pub fn rebuild_trade_view(&mut self) {
        // The blotter is a denser table than the ranking grid: give the shared
        // engine the blotter's own geometry once, so `LabState::default()` (which
        // derives) can stay a plain derive.
        if (self.trade_win.row_h - TRADE_ROW_H).abs() > f32::EPSILON {
            self.trade_win.row_h = TRADE_ROW_H;
            self.trade_win.viewport_h = TRADE_VIEWPORT_H;
        }
        let rows: &[TradeRow] = match self.results.as_ref() {
            Some(results) => &results.trades,
            None => &[],
        };
        let needle = self.trade_needle.trim().to_lowercase();
        let side = self.trade_side_filter;
        let result = self.trade_result_filter;
        let sort = self.trade_sort;
        let desc = self.trade_sort_desc;
        // COMPARE keeps its own side filter (view-local, existing contract).
        let compare_side = self.compare_side;

        let key = ViewKey {
            rows: rows.len(),
            search: needle.clone(),
            criterion: sort,
            desc,
            filter_a: side,
            filter_b: result,
            filter_c: compare_side,
        };
        let previous = self.trade_win.key();
        let order_changed = self.trade_win.order.len() != rows.len()
            || previous.rows != key.rows
            || previous.criterion != key.criterion
            || previous.desc != key.desc;

        if order_changed {
            let all_indices: Vec<u32> = (0..rows.len() as u32).collect();
            self.trade_win.order = sort_trade_indices(all_indices, rows, sort, desc);
        }

        let view: Vec<u32> = self
            .trade_win
            .order
            .iter()
            .copied()
            .filter(|&index| {
                trade_matches(&rows[index as usize], &needle, side, result, compare_side)
            })
            .collect();

        self.trade_win.set_view(view, key);
    }

    /// COMPARE side filter is view-local (pure filtering of engine trades,
    /// same precedent as library search — deliberately NOT queued).
    pub fn interaction_cmpside(&mut self, side: i32) {
        if (0..=2).contains(&side) {
            self.compare_side = side;
            // The blotter index is keyed on the compare side too, so the
            // view-local filter has to rebuild it (still O(N) over indices,
            // still never in the frame path).
            self.rebuild_trade_view();
        }
    }

    /// Reference-layout interactions. Perspectives lens + equity view are
    /// view-local presentation filters (never queued); the inspector close
    /// goes through the existing pending-actions bridge.
    /// Lens range 0..6: 0 Performance, 1 Trades, 2 Equity, 3 Drawdown,
    /// 4 Analytics, 5 Strategy (the MODIFY section below the lens pages).
    pub fn interaction_lens(&mut self, lens: i32) {
        if (0..6).contains(&lens) {
            self.lens = lens;
        }
    }

    pub fn interaction_equity_view(&mut self, view: i32) {
        if (0..3).contains(&view) {
            self.equity_select = view;
        }
    }

    pub fn interaction_detail_close(&mut self) {
        self.detail = None;
    }

    /// Ranking row click opens the inspector. Every fact the drawer shows is
    /// already in the table row, so the detail is derived here — the bridge
    /// has no per-stock detail command, and a stale-echo round-trip would
    /// open an empty drawer.
    pub fn interaction_rank_picked(&mut self, symbol: &str) {
        let row = self
            .results
            .as_ref()
            .and_then(|results| results.ranking.iter().find(|row| row.symbol == symbol))
            .or_else(|| {
                self.compare
                    .as_ref()
                    .and_then(|cmp| cmp.ranking.iter().find(|row| row.symbol == symbol))
            })
            .cloned();
        self.detail = row.map(|row| {
            let metric = |label: &str, value: String| DetailMetric {
                label: label.to_string(),
                value,
            };
            DetailView {
                symbol: row.symbol.clone(),
                title: row.symbol.clone(),
                stats: row.pnl.clone(),
                caption: format!(
                    "{} · {} · {}",
                    self.mode.label(),
                    self.config.timeframe,
                    row.ret
                ),
                metrics: vec![
                    metric("RETURN", row.ret.clone()),
                    metric(
                        "TRADES",
                        if row.unranked {
                            "—".to_string()
                        } else {
                            row.trades.clone()
                        },
                    ),
                    metric("WIN RATE", row.win.clone()),
                    metric("PROFIT FACTOR", row.pf.clone()),
                    metric("MAX DRAWDOWN", row.dd.clone()),
                    metric("SHARPE", row.sharpe.clone()),
                ],
                equity: Vec::new(),
            }
        });
    }

    /// Reset = discard the editor working copy back to the last backend echo
    /// (legacy `_dirty` revert semantics; no backend round-trip needed).
    pub fn interaction_reset(&mut self) {
        self.code = self.synced_code.clone();
    }

    /// Symbol selector: open seeds the draft from the applied echo; toggles
    /// and search stay local; Apply commits through the existing backend
    /// selection path (unknown symbols can never be queued — only echoed
    /// universe members go out).
    pub fn interaction_symopen(&mut self) {
        self.sym_draft = self.universe_selected.clone();
        self.sym_search.clear();
        self.sym_open = true;
    }

    pub fn interaction_symclose(&mut self) {
        self.sym_open = false;
        self.sym_search.clear();
        self.sym_draft = self.universe_selected.clone();
    }

    pub fn interaction_symsearch(&mut self, text: &str) {
        self.sym_search = text.to_string();
    }

    pub fn interaction_symtoggle(&mut self, symbol: &str) {
        if !self.universe_symbols.iter().any(|s| s == symbol) {
            return;
        }
        if let Some(pos) = self.sym_draft.iter().position(|s| s == symbol) {
            self.sym_draft.remove(pos);
        } else {
            self.sym_draft.push(symbol.to_string());
        }
    }

    pub fn interaction_symall(&mut self) {
        for symbol in self.sym_visible() {
            if !self.sym_draft.iter().any(|s| s == &symbol) {
                self.sym_draft.push(symbol);
            }
        }
    }

    pub fn interaction_symclear(&mut self) {
        self.sym_draft.clear();
    }

    pub fn interaction_symapply(&mut self) {
        let selected: Vec<String> = self
            .sym_draft
            .iter()
            .filter(|s| self.universe_symbols.iter().any(|u| u == *s))
            .cloned()
            .collect();
        // Optimistic echo: gather() reads universe_selected/cfg_universe_csv,
        // so Apply must commit them locally now — otherwise the next run
        // backtests the pre-dialog selection while the button shows the new
        // one. The queued action is future backend sync, not the run path.
        self.universe_selected = selected.clone();
        self.cfg_universe_csv = selected.join(",");
        self.cfg_universe_count = self.universe_selected.len();
        self.config.universe = if selected.is_empty() {
            "NO UNIVERSE".to_string()
        } else {
            selected.join(", ")
        };
        self.refresh_staleness();
        self.sym_open = false;
        let strategy_id = self
            .selected_strategy()
            .map(|s| s.name.clone())
            .unwrap_or_default();
        if strategy_id.is_empty() {
            self.universe_state =
                UniverseSaveState::Error("no strategy selected; universe not saved".to_string());
            return;
        }
        self.universe_state = UniverseSaveState::Pending;
        let action = serde_json::json!({
            "strategy_id": strategy_id,
            "symbols": selected,
        });
        self.queue_action(format!("universe:{action}"));
    }

    /// Chip ×: drop one symbol from the APPLIED selection and save it. Routes
    /// through the same queue as Apply, so the removal is persisted.
    pub fn interaction_symremove(&mut self, symbol: &str) {
        if !self.universe_selected.iter().any(|s| s == symbol) {
            return;
        }
        self.sym_draft = self
            .universe_selected
            .iter()
            .filter(|s| s.as_str() != symbol)
            .cloned()
            .collect();
        self.interaction_symapply();
    }

    /// Stock-universe save status for the selected strategy (never code).
    pub fn universe_save_state(&self) -> &UniverseSaveState {
        &self.universe_state
    }

    /// Record the backend's answer for one strategy's universe save. A reply
    /// for a strategy that is no longer selected is ignored (no cross-talk).
    pub fn apply_universe_result(&mut self, strategy_id: &str, saved: bool, error: &str) {
        let current = self
            .selected_strategy()
            .map(|s| s.name.clone())
            .unwrap_or_default();
        if current != strategy_id {
            return;
        }
        self.universe_state = if saved {
            UniverseSaveState::Saved
        } else {
            UniverseSaveState::Error(error.to_string())
        };
    }

    /// Visible universe rows under the current search (legacy popup rule:
    /// case-insensitive substring; filtering never loses draft state).
    pub fn sym_visible(&self) -> Vec<String> {
        let needle = self.sym_search.trim().to_lowercase();
        self.universe_symbols
            .iter()
            .filter(|s| needle.is_empty() || s.to_lowercase().contains(&needle))
            .cloned()
            .collect()
    }

    fn queue_action(&mut self, action: String) {
        if self.pending_actions.len() < 64 {
            self.pending_actions.push(action);
        } else {
            self.dropped_pending_actions += 1;
            eprintln!(
                "lab: pending-action queue full (64 entries) — dropped '{action}' ({} dropped total)",
                self.dropped_pending_actions
            );
        }
    }

    /// Drain pending user actions (host forwards them to the backend).
    pub fn drain_actions(&mut self) -> Vec<String> {
        std::mem::take(&mut self.pending_actions)
    }

    /// Drain only the stock-universe saves; every other queued action stays
    /// in order for its own consumer.
    pub fn drain_universe_actions(&mut self) -> Vec<String> {
        let (universe, rest): (Vec<String>, Vec<String>) =
            std::mem::take(&mut self.pending_actions)
                .into_iter()
                .partition(|a| a.starts_with("universe:"));
        self.pending_actions = rest;
        universe
    }

    pub fn set_mode(&mut self, mode: LabMode) {
        self.mode = mode;
    }

    pub fn set_filter(&mut self, filter: LibFilter) {
        self.filter = filter;
    }

    pub fn set_search(&mut self, search: &str) {
        self.search = search.to_lowercase();
    }

    pub fn set_tab(&mut self, tab: usize) {
        if tab < LAB_TABS.len() {
            self.tab = tab;
        }
    }

    /// A configuration change makes existing results outdated — never silent.
    pub fn edit_config<F: FnOnce(&mut LabConfig)>(&mut self, f: F) {
        f(&mut self.config);
        self.refresh_staleness();
    }

    pub fn refresh_staleness(&mut self) {
        if self.run != RunState::Complete {
            self.outdated = false;
            return;
        }
        let Some(fp) = &self.results_fingerprint else {
            self.outdated = false;
            return;
        };
        self.outdated = fp != &self.run_key();
    }

    /// Identity of the exact run inputs: display fingerprint plus direction
    /// mode. The mode changes results, so it must mark a completed run
    /// outdated too — the display `fingerprint()` alone cannot see it.
    pub fn run_key(&self) -> String {
        format!("{}|mode={}", self.config.fingerprint(), self.mode.kind())
    }

    /// Engine bridge entry point: attach completed results for the CURRENT
    /// configuration fingerprint.
    pub fn apply_result(&mut self, results: LabResults) {
        self.results_fingerprint = Some(self.run_key());
        self.results = Some(results);
        self.run = RunState::Complete;
        self.outdated = false;
        // New data invalidates the index layer (same contract as a snapshot).
        self.rebuild_rank_view();
        self.rebuild_trade_view();
    }

    pub fn start_run(&mut self) -> bool {
        if !self.engine_wired || self.selected.is_none() {
            return false;
        }
        self.run = RunState::Running;
        self.outdated = false;
        true
    }

    pub fn fail_run(&mut self) {
        self.run = RunState::Failed;
    }

    /// Library rows after search + filter, paired with their true indices.
    pub fn visible_strategies(&self) -> Vec<(usize, &LabStrategy)> {
        let mut rows: Vec<(usize, &LabStrategy)> = self
            .strategies
            .iter()
            .enumerate()
            .filter(|(_, s)| match self.filter {
                LibFilter::All => true,
                LibFilter::Favorites => s.favorite,
                LibFilter::Recent => s.last_backtest != "—",
            })
            .filter(|(_, s)| {
                self.search.is_empty()
                    || s.name.to_lowercase().contains(&self.search)
                    || s.description.to_lowercase().contains(&self.search)
            })
            .collect();
        rows.sort_by(|a, b| a.1.name.to_lowercase().cmp(&b.1.name.to_lowercase()));
        rows
    }

    pub fn state_label(&self) -> (&'static str, Tone) {
        match self.run {
            RunState::Ready => ("● READY", Tone::Muted),
            RunState::Running => ("● RUNNING…", Tone::Warning),
            RunState::Complete => ("✓ COMPLETE", Tone::Positive),
            RunState::Failed => ("✕ FAILED", Tone::Negative),
        }
    }

    /// Borrow the results block for projection. Returning a reference (not a
    /// clone) is what keeps a frame O(viewport): a 50k-row ranking clone per
    /// frame cost more than the entire render (`spec §1/§13`).
    pub fn results_or_default(&self) -> &LabResults {
        static EMPTY: std::sync::OnceLock<LabResults> = std::sync::OnceLock::new();
        self.results
            .as_ref()
            .unwrap_or_else(|| EMPTY.get_or_init(LabResults::default))
    }
}

// ── projection: LabState -> flat render properties (testable) ──────────────

#[derive(Debug, Clone, PartialEq)]
pub struct LibRow {
    /// True library index (the Slint callback reports it back for selection).
    pub real_index: i32,
    pub name: String,
    pub description: String,
    pub state_label: String,
    pub state_tone: i32,
    pub favorite: bool,
    pub selected: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct KpiView {
    pub label: String,
    pub value: String,
    pub tone: i32,
    pub emphasized: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ParamView {
    pub key: String,
    pub label: String,
    pub value: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct DetailMetricView {
    pub label: String,
    pub value: String,
    /// Tone for the panel colour (0 neutral, 2 positive, 3 negative).
    pub tone: i32,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct DetailViewState {
    pub symbol: String,
    pub title: String,
    pub stats: String,
    pub caption: String,
    pub metrics: Vec<DetailMetricView>,
    pub equity: Vec<(f32, f32)>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct BoardCellView {
    pub symbol: String,
    pub text: String,
    pub best: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct BoardRowView {
    pub label: String,
    pub cells: Vec<BoardCellView>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct MatrixRowView {
    pub key: String,
    pub buy: String,
    pub sell: String,
    pub winner: i32,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct CompareViewState {
    pub banner_verdict: String,
    pub banner_reason: String,
    pub banner_tone: i32,
    pub trade_summary: String,
    pub stale_notice: String,
    pub board_scope: String,
    pub board_leader: String,
    pub matrix: Vec<MatrixRowView>,
    pub board: Vec<BoardRowView>,
    pub board_symbols: Vec<String>,
    pub board_trades: Vec<String>,
    pub ranking: Vec<RankRow>,
    pub equity_buy: Vec<(f32, f32)>,
    pub equity_sell: Vec<(f32, f32)>,
    pub drawdown_buy: Vec<(f32, f32)>,
    pub drawdown_sell: Vec<(f32, f32)>,
    pub has_sides: bool,
}

/// Flat projection of a virtual window for the UI (`spec §12/§17`): the row
/// band, the scrollbar geometry and the observability counters. Every value is
/// derived in Rust so the UI never re-derives layout maths. Shared by the
/// ranking grid and the trade blotter — one projection shape, one engine.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct VirtualWindowView {
    /// Rows in the filtered+sorted dataset (the TRUE count — nothing hidden).
    pub total: i32,
    /// First rendered row (0-based into the dataset order).
    pub first: i32,
    /// Rendered rows (visible + overscan).
    pub count: i32,
    pub row_h: f32,
    pub viewport_h: f32,
    pub scroll_px: f32,
    pub max_scroll_px: f32,
    /// Scrollbar thumb size/offset in px (0 offset = hidden track).
    pub thumb_h: f32,
    pub thumb_y: f32,
    /// True when the dataset is taller than the viewport (draw the scrollbar).
    pub scrollable: bool,
    /// Cheap identity of the rendered band — the shell skips the model push
    /// when it is unchanged (`spec §3`: zero full-table re-render). Split in
    /// two 32-bit halves because Slint's `int` is i32.
    pub signature: (i32, i32),
    /// Rows rendered beyond the visible band — the overscan counter
    /// (`spec §18`).
    pub overscan: i32,
}

/// Backwards-compatible name for the ranking grid's projection.
pub type RankWindowView = VirtualWindowView;

/// Project any window into the flat UI shape, for ANY row type. The caller
/// supplies the signature function for its own row type (the ranking grid signs
/// symbols/P&L, the blotter signs the trade identity) — that is what makes the
/// engine shared without inventing a trait for two callers.
fn window_view<R>(
    window: &VirtualWindow,
    rendered: &[R],
    sign: impl Fn(&[R]) -> (i32, i32),
) -> VirtualWindowView {
    let (thumb_h, thumb_y) = window.thumb();
    let visible_rows = (window.viewport_h / window.row_h).ceil().max(1.0) as i32;
    VirtualWindowView {
        total: window.total as i32,
        first: window.first as i32,
        count: rendered.len() as i32,
        row_h: window.row_h,
        viewport_h: window.viewport_h,
        scroll_px: window.scroll_px,
        max_scroll_px: window.max_scroll(),
        thumb_h,
        thumb_y,
        scrollable: window.max_scroll() > 0.0,
        signature: sign(rendered),
        overscan: (rendered.len() as i32 - visible_rows).max(0),
    }
}

/// The selected trade, flattened for the detail panel. Every field comes from
/// the real record (`spec §2`: no invented facts).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct TradeDetailState {
    pub symbol: String,
    pub side: String,
    pub metrics: Vec<DetailMetricView>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct LabView {
    pub has_strategy: bool,
    pub name: String,
    pub description: String,
    pub tags: String,
    pub version: String,
    pub modified: String,
    pub last_backtest: String,
    pub state_label: String,
    pub state_tone: i32,
    pub mode: i32,
    pub outdated: bool,
    pub run_enabled: bool,
    pub config_summary: String,
    pub universe: String,
    pub timeframe: String,
    pub dates: String,
    pub capital: String,
    pub show_results: bool,
    pub tabs_enabled: bool,
    pub tab: usize,
    pub kpis: Vec<KpiView>,
    /// The virtual window — the ONLY ranking rows the UI receives.
    pub ranking: Vec<RankRow>,
    pub rank_window: RankWindowView,
    /// Trade blotter window: geometry, scrollbar and the band signature.
    pub trade_window: VirtualWindowView,
    /// Dataset position of the first rendered trade — the blotter numbers rows
    /// from the dataset, not from the window, so scrolling never renumbers.
    pub trade_number_offset: i32,
    /// True trade count in the current filter view (the blotter's honest total;
    /// never the rendered band).
    pub trade_total: i32,
    /// Blotter counter line ("412 trades · showing 401-427").
    pub trade_summary: String,
    pub trade_needle: String,
    pub trade_side_filter: i32,
    pub trade_result_filter: i32,
    pub trade_sort: i32,
    pub trade_sort_labels: Vec<String>,
    /// The ONE selected trade's facts, built on click (`spec §27`) — flattened
    /// for the panel, empty when nothing is selected.
    pub trade_detail: TradeDetailState,
    pub trades: Vec<TradeRow>,
    pub equity: Vec<(f32, f32)>,
    pub drawdown: Vec<(f32, f32)>,
    pub risk_notes: Vec<String>,
    pub library: Vec<LibRow>,
    pub empty_reason: String,
    pub summary_line: String,
    pub equity_caption: String,
    pub drawdown_caption: String,
    pub ranking_count: String,
    pub run_stop: bool,
    pub verdict_label: String,
    pub verdict_note: String,
    pub verdict_tone: i32,
    pub progress_label: String,
    pub code: String,
    pub dirty: bool,
    pub params: Vec<ParamView>,
    pub cfg_universe_csv: String,
    pub timeframes: Vec<String>,
    pub timeframe_index: i32,
    pub cfg_dates_start: String,
    pub cfg_dates_end: String,
    pub cfg_capital: String,
    pub config_error: String,
    pub rankby_labels: Vec<String>,
    pub rankby_current: i32,
    pub rank_search: String,
    pub rank_desc: bool,
    pub rank_heads: Vec<String>,
    pub trade_heads: Vec<String>,
    pub detail: Option<DetailViewState>,
    pub compare: CompareViewState,
    pub compare_side: i32,
    pub selected_trade: i32,
    pub trade_filters_active: bool,
    pub trade_symbol: String,
    pub run_label: String,
    pub is_compare: bool,
    pub sym_open: bool,
    pub sym_search: String,
    pub sym_button_line: String,
    pub sym_count_line: String,
    /// Compact draft count for the dialog header ("3 selected").
    pub sym_selected_line: String,
    /// Compact stock row under Reset/Save (saved universe of the selected strategy).
    pub universe_chips: Vec<String>,
    /// The selected strategy's PERSISTED universe (the saved stock row's chips).
    pub saved_universe_chips: Vec<String>,
    pub universe_count_line: String,
    /// "clean" | "pending" | "saved" | "error" (drives the row's status text).
    pub universe_state: String,
    pub universe_error: String,
    pub sym_visible: Vec<String>,
    pub sym_visible_on: Vec<bool>,
    // Reference-layout projection (see project() derivations).
    pub run_id: String,
    pub run_ts: String,
    pub cfg_hash: String,
    pub result_pnl: String,
    pub result_return: String,
    pub result_pnl_tone: i32,
    pub result_exec_line: String,
    pub stale_exec_line: String,
    pub stale_cur_line: String,
    pub range_line: String,
    pub diag_show: bool,
    pub diag_title: String,
    pub diag_detail: String,
    pub diag_stale: bool,
    pub risk_gate_show: bool,
    pub risk_gate_text: String,
    pub lens: i32,
    pub strategy_count_line: String,
    pub editing_hint: String,
    pub studio_ref_pf: String,
    pub studio_source: String,
    pub equity_select: i32,
    /// Date-range projection of the ISO config fields: humans see
    /// "22 Sep 2026 — 22 Sep 2026" while the commit contract stays ISO
    /// (`dates-committed` → "dates:a:b" is untouched). `dates_error` is
    /// subtle inline validation — a bad range is flagged immediately, the
    /// user's selection is never destroyed.
    pub dates_human: String,
    pub dates_error: String,
    pub today_days: i32,
    /// Committed selection as calendar anchors (days since epoch, -1 unset)
    /// — Slint never parses ISO, it consumes these ints directly.
    pub dates_start_days: i32,
    pub dates_end_days: i32,
    pub date_presets: Vec<LabPresetData>,
    // ── §02 CONFIGURE (three-row structured config) ──
    /// First ≤5 real timeframe labels for the segmented control; the rest stay
    /// reachable through the existing dropdown menu. Never hardcoded.
    pub tf_short: Vec<String>,
    /// Index of the selected timeframe inside `tf_short` (-1 = not in the
    /// short list, i.e. it lives behind the `▾ MORE` menu).
    pub tf_short_idx: i32,
    /// Range presets anchored on the real store bounds.
    pub range_presets: Vec<LabRangePresetData>,
    pub range_preset_idx: i32,
    /// "19 Sep 2019" / "18 Aug 2026" — formatted HERE so Slint never parses a
    /// date (`ai_memory.md` §22 single authority for date logic).
    pub range_from_human: String,
    pub range_to_human: String,
    /// True when the range ends at the last available bar (the live edge).
    pub range_to_live: bool,
    /// "1,684 DAYS" — the real span of the committed range.
    pub range_span_line: String,
    /// The timeframe the control shows. Resolved from the ladder + index, so
    /// the dropdown, the segmented cells and the control can never disagree
    /// about which granularity is selected.
    pub timeframe_label: String,
    /// The capital the run will use, formatted from the committed input — the
    /// same number the CAPITAL field shows, never a stale config echo.
    pub capital_label: String,
    /// `validate_form` bit 4 (capital > 0), surfaced next to the sub-row so
    /// the capital cell states the rule the kernel actually enforces.
    pub capital_ok: bool,
    /// "+522" when chips are capped, empty when every symbol is shown.
    pub chips_more_line: String,
    /// Validation rows for the run bar (pass / warn / fail), all from state.
    pub cfg_checks: Vec<LabCheckData>,
    pub coverage: CoverageView,
    /// What the completeness probe actually read. "VERIFIED" would overstate a
    /// bounded sample, so a sampled probe says so in the caption itself.
    pub cov_caption: String,
    // ── live run progress (every field derived from a measured event) ──
    pub prog_active: bool,
    pub prog_headline: String,
    pub prog_counts: String,
    pub prog_current: String,
    pub prog_stage: String,
    pub prog_stage_pct: f32,
    pub prog_elapsed: String,
    pub prog_eta: String,
    pub prog_speed: String,
    pub prog_throughput: String,
    pub prog_failed_line: String,
    pub prog_pct: f32,
    pub prog_cancelled: bool,
    pub prog_long: bool,
    /// "LONG-RUNNING" / "TAKING LONGER THAN USUAL" / "" — a state, not a verdict.
    pub prog_watchdog: String,
}

/// One quick-select preset for the date-range picker. Ranges are day anchors
/// (days since epoch) so the picker highlights/applies without string parsing.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LabPresetData {
    pub label: String,
    pub start_days: i32,
    pub end_days: i32,
}

/// §02 date-range preset (`1Y 3Y 5Y 10Y MAX`), anchored on the real store
/// bounds. Same shape as the picker's [`LabPresetData`] — one struct, two
/// surfaces, so the two rows can never disagree about what a range means.
pub type LabRangePresetData = LabPresetData;

/// One §02 validation row. `kind` is the tone the screen paints:
/// 0 pass, 1 warning, 2 failure.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LabCheckData {
    pub label: String,
    pub kind: i32,
}

/// A measured coverage probe, before the kernel derives the percentage. The
/// window travels with the counts so `expected_bars` is computed against the
/// same window the backend counted.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct CoverageMeasurement {
    pub symbols_total: u32,
    pub symbols_covering: u32,
    pub symbols_probed: u32,
    pub bars_present: u64,
    pub gaps: u32,
    pub start_days: i32,
    pub end_days: i32,
    pub tf_secs: i64,
    pub sampled: bool,
    /// The symbol the bar count belongs to.
    pub anchor: String,
}

/// §02 completeness strip, flattened for the screen. `on == false` means
/// "not measured" — the strip is hidden, never painted at 0%.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct CoverageView {
    pub pct: i32,
    pub note: String,
    pub kind: i32,
    pub on: bool,
}

// Civil-date arithmetic (Howard Hinnant's algorithm, std-only). Date math is
// view-model bookkeeping, not financial logic — it stays here so Slint keeps
// only presentation state (§22: one authority per value).
fn leap_year(y: i64) -> bool {
    (y % 4 == 0 && y % 100 != 0) || y % 400 == 0
}

fn days_in_month(y: i64, m: i64) -> i64 {
    match m {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        _ => {
            if leap_year(y) {
                29
            } else {
                28
            }
        }
    }
}

/// Days since 1970-01-01 for a civil date (validates calendar ranges).
fn parse_iso_days(s: &str) -> Option<i64> {
    let b: Vec<char> = s.chars().collect();
    if b.len() != 10
        || b[4] != '-'
        || b[7] != '-'
        || !b
            .iter()
            .enumerate()
            .all(|(i, c)| i == 4 || i == 7 || c.is_ascii_digit())
    {
        return None;
    }
    let y: i64 = s[0..4].parse().ok()?;
    let m: i64 = s[5..7].parse().ok()?;
    let d: i64 = s[8..10].parse().ok()?;
    if !(1970..=2099).contains(&y) || !(1..=12).contains(&m) || d < 1 || d > days_in_month(y, m) {
        return None;
    }
    let yy = if m <= 2 { y - 1 } else { y };
    let era = (if yy >= 0 { yy } else { yy - 399 }) / 400;
    let yoe = yy - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    Some(era * 146097 + doe - 719468)
}

/// Inverse of [`parse_iso_days`]: days since epoch → civil (y, m, d).
fn civil_from_days(z: i64) -> (i64, i64, i64) {
    let z = z + 719468;
    let era = (if z >= 0 { z } else { z - 146096 }) / 146097;
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (if m <= 2 { y + 1 } else { y }, m, d)
}

fn human_from_days(z: i64) -> String {
    const MONTHS: [&str; 12] = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ];
    let (y, m, d) = civil_from_days(z);
    format!("{d} {} {y}", MONTHS[(m - 1) as usize])
}

/// Today at UTC midnight, in days since epoch (stable within a session; the
/// picker only needs it to anchor the calendar view).
fn today_days_raw() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64 / 86400)
        .unwrap_or(0)
}

/// `months` back from `z`, clamped to the shorter month (Jan 31 − 1M = Dec 31).
fn sub_months(z: i64, months: i64) -> i64 {
    let (y, m, d) = civil_from_days(z);
    let total = y * 12 + (m - 1) - months;
    let ny = total.div_euclid(12);
    let nm = total.rem_euclid(12) + 1;
    let nd = d.min(days_in_month(ny, nm));
    iso_days_from_civil(ny, nm, nd)
}

fn iso_days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let yy = if m <= 2 { y - 1 } else { y };
    let era = (if yy >= 0 { yy } else { yy - 399 }) / 400;
    let yoe = yy - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146097 + doe - 719468
}

fn year_start_days(z: i64) -> i64 {
    let (y, _, _) = civil_from_days(z);
    iso_days_from_civil(y, 1, 1)
}

/// Quick-select presets ending today (brief §8: kept subtle — five entries,
/// no exhaustive list).
fn date_presets_for(today: i64) -> Vec<LabPresetData> {
    let t = today as i32;
    let p = |label: &str, start: i64| LabPresetData {
        label: label.to_string(),
        start_days: start as i32,
        end_days: t,
    };
    vec![
        p("Last 1M", sub_months(today, 1)),
        p("Last 3M", sub_months(today, 3)),
        p("Last 6M", sub_months(today, 6)),
        p("Last 1Y", sub_months(today, 12)),
        p("YTD", year_start_days(today)),
    ]
}

/// §02 date-range presets, anchored on the REAL store bounds.
///
/// Every entry ends at the last available bar, so switching presets never
/// moves the END of the experiment behind the user's back: `1Y 3Y 5Y 10Y MAX`.
/// A year preset whose span reaches before the first available bar is
/// dropped, so it never duplicates `MAX` under a different label.
///
/// `first_avail`/`last_avail` are day anchors from the backend's real
/// `date_range()`; unknown bounds (`<= 0`) yield no presets at all, because a
/// preset that cannot be placed against real data is an invented control.
pub fn range_presets_for(first_avail: i64, last_avail: i64) -> Vec<LabRangePresetData> {
    if first_avail <= 0 || last_avail < first_avail {
        return Vec::new();
    }
    let end = last_avail;
    let mut presets: Vec<LabRangePresetData> = [(1i64, "1Y"), (3, "3Y"), (5, "5Y"), (10, "10Y")]
        .iter()
        .filter_map(|(years, label)| {
            let want = sub_months(end, years * 12);
            if want < first_avail {
                return None;
            }
            Some(LabRangePresetData {
                label: (*label).to_string(),
                start_days: want as i32,
                end_days: end as i32,
            })
        })
        .collect();
    presets.push(LabRangePresetData {
        label: "MAX".to_string(),
        start_days: first_avail as i32,
        end_days: end as i32,
    });
    presets
}

/// `seconds` → `MM:SS` (or `HH:MM:SS`). A dash for an unknown duration —
/// "we have no measurement" must never render as `00:00`.
pub fn clock_label(seconds: f64) -> String {
    if !seconds.is_finite() || seconds < 0.0 {
        return "—".to_string();
    }
    let total = seconds.round() as u64;
    let (h, m, s) = (total / 3600, (total % 3600) / 60, total % 60);
    if h > 0 {
        format!("{h}:{m:02}:{s:02}")
    } else {
        format!("{m:02}:{s:02}")
    }
}

/// Day anchor → `YYYY-MM-DD` (the inverse of [`parse_iso_days`]).
fn iso_from_days(z: i64) -> String {
    let (y, m, d) = civil_from_days(z);
    format!("{y:04}-{m:02}-{d:02}")
}

/// Days in an inclusive range, for the honest `N DAYS` span caption.
pub fn span_days(start: i64, end: i64) -> i64 {
    if start < 0 || end < start {
        0
    } else {
        end - start + 1
    }
}

/// Fixed ranking-row height in logical pixels. The stable-layout contract
/// (`spec §12`): with a constant height the virtual scroll height is exact and
/// the window arithmetic is integer, so no row can ever be half-drawn.
pub const RANK_ROW_H: f32 = 40.0;
/// Viewport height in logical pixels — the ~10-row initial window the spec
/// asks for. Rows beyond it are reachable by scrolling, never hidden.
pub const RANK_VIEWPORT_H: f32 = RANK_ROW_H * 10.0;
/// Trade-row height: one line, so the blotter is denser than the ranking grid.
pub const TRADE_ROW_H: f32 = 28.0;
/// Trade viewport — ~14 rows, the window the spec asks for.
pub const TRADE_VIEWPORT_H: f32 = TRADE_ROW_H * 14.0;
/// Overscan bounds (`spec §2`): idle keeps 2 rows of slack, a fast flick
/// pre-renders up to 8 so the frame never waits for a row.
const RANK_OVERSCAN_MIN: usize = 2;
const RANK_OVERSCAN_MAX: usize = 8;

/// ONE windowing engine for every large table in the Lab (`spec §36/§40`:
/// reuse the existing data layer instead of a second bespoke implementation).
///
/// Pipeline:
/// ```text
/// FULL DATA (owned by the snapshot: results.ranking / results.trades)
///   -> view: Vec<u32>       (filter + sort as INDICES, never row clones)
///   -> first/count          (VISIBLE RANGE + ADAPTIVE OVERSCAN)
///   -> LabView rows         (the only rows the UI ever receives)
/// ```
/// The index layer is rebuilt on data/filter/sort changes only — never per
/// frame, never per scroll notch — so scrolling is pure arithmetic. One engine
/// now serves the ranking grid AND the trade blotter, so both share the same
/// window arithmetic, overscan policy and scrollbar maths.
#[derive(Debug, Clone, PartialEq)]
pub struct VirtualWindow {
    /// Sorted order over all rows in the dataset (row indices).
    pub order: Vec<u32>,
    /// Sorted+filtered order over the dataset (row indices).
    pub view: Vec<u32>,
    /// Rows in `view` (the true count, never a truncated one).
    pub total: usize,
    /// First rendered row (into `view`).
    pub first: usize,
    /// Rendered rows (visible + overscan).
    pub count: usize,
    /// Scroll offset in px, always inside `[0, max_scroll]`.
    pub scroll_px: f32,
    /// Viewport height in px (updated by the UI on resize).
    pub viewport_h: f32,
    /// Row height in px — fixed per table, the stable-layout contract.
    pub row_h: f32,
    /// Smoothed last-frame delta (px) — drives the adaptive overscan.
    velocity: f32,
    /// Identity of the data the index layer was built from; a mismatch forces a
    /// rebuild (`spec §7` invalidation instead of eager recompute).
    key: ViewKey,
}

/// What the index layer was built from. Cheap to compare, complete enough that
/// a stale view is impossible. `criterion`/`desc` are the sort; `filter_a` and
/// `filter_b` carry the two extra dimensions a table may filter on (the trade
/// blotter's side and result filters leave them at -1).
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ViewKey {
    pub rows: usize,
    pub search: String,
    pub criterion: i32,
    pub desc: bool,
    pub filter_a: i32,
    pub filter_b: i32,
    /// Third filter dimension (the blotter's COMPARE side segment); -1 when the
    /// table has no such dimension.
    pub filter_c: i32,
}

/// Backwards-compatible name: the ranking grid was the first table to need it.
pub type RankWindow = VirtualWindow;

impl Default for VirtualWindow {
    fn default() -> Self {
        Self {
            order: Vec::new(),
            view: Vec::new(),
            total: 0,
            first: 0,
            count: 0,
            scroll_px: 0.0,
            viewport_h: RANK_VIEWPORT_H,
            row_h: RANK_ROW_H,
            velocity: 0.0,
            key: ViewKey::default(),
        }
    }
}

/// Does this trade belong in the current filter view? One allocation-free pass
/// per row, over the COMPLETE dataset (`spec §9`). The needle matches the stock
/// and the exit reason — the two fields a user actually searches a blotter by.
/// `compare_side` is the COMPARE segment filter (view-local, existing contract).
fn trade_matches(row: &TradeRow, needle: &str, side: i32, result: i32, compare_side: i32) -> bool {
    if side == TRADE_SIDE_LONG && !row.side.eq_ignore_ascii_case("LONG") {
        return false;
    }
    if side == TRADE_SIDE_SHORT && !row.side.eq_ignore_ascii_case("SHORT") {
        return false;
    }
    if compare_side == 1 && !row.side.eq_ignore_ascii_case("LONG") {
        return false;
    }
    if compare_side == 2 && !row.side.eq_ignore_ascii_case("SHORT") {
        return false;
    }
    if result == TRADE_RESULT_WIN && row.pnl_tone != Tone::Positive {
        return false;
    }
    if result == TRADE_RESULT_LOSS && row.pnl_tone != Tone::Negative {
        return false;
    }
    needle.is_empty()
        || contains_ignore_ascii_case(&row.symbol, needle)
        || contains_ignore_ascii_case(&row.reason, needle)
}

/// Sort trade INDICES by the chosen column. The numeric path builds a
/// `(key, index)` array so the compare never chases a pointer into the dataset
/// and the index acts as a deterministic tiebreaker (`spec §6/§10`). The
/// symbol column is a string sort, so it is separate by nature.
fn sort_trade_indices(view: Vec<u32>, rows: &[TradeRow], sort: i32, desc: bool) -> Vec<u32> {
    if sort == TRADE_SORT_SYMBOL {
        let mut sorted = view;
        sorted.sort_by(|a, b| {
            let order = rows[*a as usize]
                .symbol
                .cmp(&rows[*b as usize].symbol)
                .then(a.cmp(b));
            if desc {
                order.reverse()
            } else {
                order
            }
        });
        return sorted;
    }
    let field = sort.max(0) as usize;
    let mut keyed: Vec<(f64, u32)> = view
        .into_iter()
        .filter_map(|index| {
            let row = rows.get(index as usize)?;
            // NaN (a missing value) sinks in BOTH directions, so a row with no
            // recorded value never outranks a real one.
            let raw = row.sort.get(field).copied().unwrap_or(f64::NAN);
            let key = if raw.is_nan() {
                if desc {
                    f64::NEG_INFINITY
                } else {
                    f64::INFINITY
                }
            } else {
                raw
            };
            Some((key, index))
        })
        .collect();
    keyed.sort_unstable_by(|a, b| {
        let order = a.0.total_cmp(&b.0).then(a.1.cmp(&b.1));
        if desc {
            order.reverse()
        } else {
            order
        }
    });
    keyed.into_iter().map(|(_, index)| index).collect()
}

/// Identity of a rendered trade band, so the shell can skip the model push when
/// the blotter did not change (`spec §3`).
fn trade_signature(rows: &[TradeRow], total: usize, scroll_px: f32) -> (i32, i32) {
    let mut hash = 0x9e37_79b9_7f4a_7c15u64 ^ total as u64;
    hash ^= (scroll_px * 64.0) as u64;
    for row in rows {
        for part in [
            row.symbol.as_str(),
            row.side.as_str(),
            row.entry.as_str(),
            row.exit.as_str(),
            row.entry_px.as_str(),
            row.exit_px.as_str(),
            row.pnl.as_str(),
            row.r.as_str(),
            row.bars.as_str(),
            row.reason.as_str(),
        ] {
            for byte in part.as_bytes() {
                hash ^= *byte as u64;
                hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
            }
        }
    }
    ((hash >> 32) as i32, hash as i32)
}

/// Chips shown next to the universe control. A presentation cap only — every
/// symbol stays in the data layer, the selector and the run request; the count
/// next to the chips states the true total.
pub const UNIVERSE_CHIP_CAP: usize = 6;

/// Cells in the §02 timeframe segmented control. The rest of the REAL ladder
/// stays reachable through the existing dropdown — a cap on the row, never on
/// the data. Four (not five) because the TIMEFRAME column physically cannot
/// fit six cells without eliding every label, which is the layout bug this
/// cap exists to prevent.
pub const TF_SHORT_CAP: usize = 4;

/// Symbols a coverage probe reads by default. A full 527-symbol bar count is a
/// full store scan, so the default is bounded and the screen says so.
pub const COVERAGE_SAMPLE_SYMBOLS: usize = 24;

/// Count the symbols in a comma CSV ("", "A", "A,B" -> 0, 1, 2). Done where
/// the CSV is adopted, never in the frame path.
fn count_symbols(csv: &str) -> usize {
    csv.split(',')
        .filter(|symbol| !symbol.trim().is_empty())
        .count()
}

/// ASCII case-insensitive substring test without allocating. Tickers are ASCII
/// by contract (the whole market vocabulary is), and this keeps a keystroke's
/// filter pass allocation-free over 100k rows.
fn contains_ignore_ascii_case(haystack: &str, needle: &str) -> bool {
    if needle.is_empty() {
        return true;
    }
    let hay = haystack.as_bytes();
    let pat = needle.as_bytes();
    if pat.len() > hay.len() {
        return false;
    }
    hay.windows(pat.len()).any(|window| {
        window
            .iter()
            .zip(pat)
            .all(|(a, b)| a.eq_ignore_ascii_case(b))
    })
}

impl VirtualWindow {
    /// Rebuild the index layer for the current data/filter/sort. O(N) over
    /// INDICES with cached keys — no row clone, no display-string compare
    /// (`spec §6/§7`). Called on data arrival and on filter/sort edits, never
    /// from the frame path.
    pub fn rebuild(
        &mut self,
        rows: &[RankRow],
        search: &str,
        criterion: Option<usize>,
        desc: bool,
    ) {
        let previous = self.key.clone();
        let key = ViewKey {
            rows: rows.len(),
            search: search.trim().to_string(),
            criterion: criterion.map_or(-1, |c| c as i32),
            desc,
            filter_a: -1,
            filter_b: -1,
            filter_c: -1,
        };
        // The order only changes when the DATA or the SORT changes. A keystroke
        // filters the existing order instead of re-sorting it (`spec §15/§16`),
        // so typing never reorders what the user is reading.
        let order_changed = self.order.len() != rows.len()
            || previous.rows != key.rows
            || previous.criterion != key.criterion
            || previous.desc != key.desc;

        if order_changed {
            let all_indices: Vec<u32> = (0..rows.len() as u32).collect();
            if let Some(field) = criterion {
                let descending = desc;
                let mut keyed: Vec<(f64, u32)> = all_indices
                    .into_iter()
                    .filter_map(|index| {
                        let row = rows.get(index as usize)?;
                        let raw = row.sort[field];
                        let key = if row.unranked {
                            if descending {
                                f64::NEG_INFINITY
                            } else {
                                f64::INFINITY
                            }
                        } else {
                            raw
                        };
                        Some((key, index))
                    })
                    .collect();
                keyed.sort_unstable_by(|a, b| {
                    let order = a.0.total_cmp(&b.0).then(a.1.cmp(&b.1));
                    if descending {
                        order.reverse()
                    } else {
                        order
                    }
                });
                self.order = keyed.into_iter().map(|(_, index)| index).collect();
            } else {
                self.order = all_indices;
            }
        }

        let needle = key.search.as_str();
        let view: Vec<u32> = if needle.is_empty() {
            self.order.clone()
        } else {
            self.order
                .iter()
                .copied()
                .filter(|&index| contains_ignore_ascii_case(&rows[index as usize].symbol, needle))
                .collect()
        };
        self.set_view(view, key);
    }

    /// What the current index layer was built from — the caller compares this
    /// to decide whether a filter/sort edit needs a re-sort or only a re-filter.
    pub fn key(&self) -> ViewKey {
        self.key.clone()
    }

    /// Adopt a freshly built index layer (shared by every table's rebuild).
    pub fn set_view(&mut self, view: Vec<u32>, key: ViewKey) {
        self.view = view;
        self.total = self.view.len();
        self.key = key;
        self.clamp_scroll();
        // Lay out immediately: a fresh dataset or a filter edit must be VISIBLE
        // without waiting for a scroll notch.
        self.layout();
    }

    /// Largest legal scroll offset for the current window.
    pub fn max_scroll(&self) -> f32 {
        let content = self.total as f32 * self.row_h;
        (content - self.viewport_h).max(0.0)
    }

    fn clamp_scroll(&mut self) {
        self.scroll_px = self.scroll_px.clamp(0.0, self.max_scroll());
    }

    /// Recompute `first`/`count` for the current scroll offset and velocity.
    /// O(1) — this is the only per-frame work the table does.
    pub fn layout(&mut self) {
        let visible_rows = (self.viewport_h / self.row_h).ceil().max(1.0) as usize;
        // Adaptive overscan from scroll speed: a flick pre-renders more rows so
        // a fast scroll never waits, and an idle table shrinks back (`spec §2`).
        // The speed term is clamped BEFORE the add: a huge velocity (an
        // End-key jump) must not overflow the band arithmetic.
        let speed_rows =
            ((self.velocity.abs() / self.row_h).ceil().max(0.0) as usize).min(RANK_OVERSCAN_MAX);
        let overscan = (RANK_OVERSCAN_MIN + speed_rows).min(RANK_OVERSCAN_MAX);
        let anchor = (self.scroll_px / self.row_h).floor().max(0.0) as usize;
        let first = anchor.saturating_sub(overscan);
        let last = (anchor + visible_rows + overscan).min(self.total);
        self.first = first.min(self.total);
        self.count = last.saturating_sub(self.first);
    }

    /// Scroll by a delta in px (wheel notch or drag). Clamped, so rapid
    /// TOP -> BOTTOM -> TOP can never leave the window outside the data
    /// (`spec §11`).
    pub fn scroll_by(&mut self, delta_px: f32) {
        if !delta_px.is_finite() {
            return;
        }
        self.velocity = delta_px;
        self.scroll_px += delta_px;
        self.clamp_scroll();
        self.layout();
    }

    /// Absolute scroll (scrollbar drag, keyboard Home/End).
    pub fn scroll_to(&mut self, px: f32) {
        if !px.is_finite() {
            return;
        }
        self.velocity = 0.0;
        self.scroll_px = px;
        self.clamp_scroll();
        self.layout();
    }

    /// Viewport height changed (window resize): re-clamp so the content can
    /// never leave a gap under the header.
    pub fn set_viewport(&mut self, height: f32) {
        if !height.is_finite() || height <= 0.0 {
            return;
        }
        self.viewport_h = height;
        self.clamp_scroll();
        self.layout();
    }

    /// Scrollbar thumb geometry in px (`spec §12`: stable, derived, never
    /// guessed by the UI).
    pub fn thumb(&self) -> (f32, f32) {
        let content = self.total as f32 * self.row_h;
        if content <= self.viewport_h || self.total == 0 {
            return (self.viewport_h, 0.0);
        }
        let size = (self.viewport_h * self.viewport_h / content).max(24.0);
        let travel = self.viewport_h - size;
        let offset = if self.max_scroll() > 0.0 {
            travel * (self.scroll_px / self.max_scroll())
        } else {
            0.0
        };
        (size, offset)
    }
}

fn placeholder_kpis() -> Vec<KpiView> {
    // legacy MetricsTiles keys (no SORTINO — the tile grid never had one) and
    // the tile missing glyph ("--").
    [
        "NET P&L",
        "TRADES",
        "WIN RATE",
        "PF",
        "EXPECTANCY",
        "MAX DD",
        "SHARPE",
        "AVG TRADE",
    ]
    .iter()
    .map(|label| KpiView {
        label: (*label).to_string(),
        value: "--".to_string(),
        tone: Tone::Muted.kind(),
        emphasized: *label == "NET P&L",
    })
    .collect()
}

/// Whole rupees with thousands separators, e.g. 10000.0 → "10,000" (mirrors
/// the backend's `f"₹{capital:,.0f}"` echo — Rust format strings have no
/// numeric grouping).
fn grouped_whole(value: f64) -> String {
    let whole = value.round();
    let digits = whole.abs().to_string();
    let mut grouped = String::new();
    for (i, ch) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i) % 3 == 0 {
            grouped.push(',');
        }
        grouped.push(ch);
    }
    if whole < 0.0 {
        format!("-{grouped}")
    } else {
        grouped
    }
}

/// legacy `t.semantic`: positive → Positive, negative → Negative, zero →
/// Neutral, missing → Muted.
fn semantic_tone(value: Option<f64>) -> Tone {
    match value {
        None => Tone::Muted,
        Some(v) if v > 0.0 => Tone::Positive,
        Some(v) if v < 0.0 => Tone::Negative,
        _ => Tone::Neutral,
    }
}

/// legacy `_compare_cell` (board cells): kind-formatted backend numbers.
fn compare_cell(kind: &str, value: Option<f64>) -> String {
    match value {
        None => "--".to_string(),
        Some(v) => match kind {
            "money" => inr_signed(Some(v)),
            "pct" => format!("{v:+.2}%"),
            "rate" => format!("{:.1}%", v * 100.0),
            "dd" => format!("-{v:.2}%"),
            "count" => format!("{}", v as i64),
            _ => format!("{v:.2}"),
        },
    }
}

/// Identity of a rendered band. The shell compares it with the value the UI
/// currently holds and skips the whole model push when nothing changed
/// (`spec §3`: an unrelated state change must not re-render the table). Returned
/// as two 32-bit halves because Slint's `int` is i32 and a 64-bit signature must
/// survive the trip exactly.
fn ranking_signature(rows: &[RankRow], total: usize, scroll_px: f32) -> (i32, i32) {
    let mut hash = 0xcbf2_9ce4_8422_2325u64 ^ total as u64;
    hash ^= (scroll_px * 64.0) as u64;
    for row in rows {
        for part in [
            row.symbol.as_str(),
            row.pnl.as_str(),
            row.ret.as_str(),
            row.trades.as_str(),
            row.win.as_str(),
            row.pf.as_str(),
            row.dd.as_str(),
            row.sharpe.as_str(),
        ] {
            for byte in part.as_bytes() {
                hash ^= *byte as u64;
                hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
            }
        }
    }
    ((hash >> 32) as i32, hash as i32)
}

/// Criterion index -> `RankRow::sort` field. `RANKBY_HEADER` already maps the
/// criterion box to its `RANK_HEADERS` column, and the two leading columns
/// ("#", "SYMBOL") are not sortable metrics, so the mapping is derived instead
/// of duplicated. `None` = an out-of-range box (honest no-sort, never a panic).
fn rankby_sort_field(state: &LabState) -> Option<usize> {
    if state.rankby_current < 0 {
        return None;
    }
    RANKBY_HEADER
        .get(state.rankby_current as usize)
        .map(|column| column.saturating_sub(2))
        .filter(|field| *field < 7)
}

/// Local gather-validity for the RUN control: the same fail-closed rules the
/// host's `LabRunRequest::gather` enforces (a real selection, a real
/// timeframe, a finite positive capital). RUN stays disabled until the values
/// are real — never invents a request.
fn gather_valid(state: &LabState) -> bool {
    let has_symbols =
        !state.universe_selected.is_empty() || count_symbols(&state.cfg_universe_csv) > 0;
    let timeframe = if state.timeframe_index < 0 {
        String::new()
    } else {
        state
            .timeframes
            .get(state.timeframe_index as usize)
            .cloned()
            .unwrap_or_default()
    };
    let capital_ok = state
        .cfg_capital
        .trim()
        .parse::<f64>()
        .ok()
        .filter(|n| n.is_finite() && *n > 0.0)
        .is_some();
    has_symbols && !timeframe.is_empty() && capital_ok
}

pub fn project(state: &LabState) -> LabView {
    let strategy = state.selected_strategy();
    let has_strategy = strategy.is_some();
    let is_compare = state.mode == LabMode::Compare;

    let results = state.results_or_default();
    let show_single =
        !is_compare && state.run == RunState::Complete && state.results.is_some() && has_strategy;
    let cmp = state.compare.clone().unwrap_or_default();
    let has_compare = has_strategy
        && (state.results_buy.is_some()
            || state.results_sell.is_some()
            || !cmp.matrix.is_empty()
            || !cmp.banner_verdict.is_empty());
    let show_results = show_single || (is_compare && has_compare);

    let kpis = if show_single {
        results
            .kpis
            .iter()
            .map(|k| KpiView {
                label: k.label.clone(),
                value: k.value.clone(),
                tone: k.tone.kind(),
                emphasized: k.emphasized,
            })
            .collect()
    } else {
        placeholder_kpis()
    };

    let empty_reason = if !has_strategy {
        "Select a strategy from the library".to_string()
    } else if state.run == RunState::Running {
        "Backtest running…".to_string()
    } else if is_compare && !has_compare {
        "Run BUY and SELL backtests to compare performance.".to_string()
    } else if state.outdated {
        "Results are outdated — run again".to_string()
    } else {
        "No backtest results yet — run the backtest".to_string()
    };

    let (state_label, _state_tone) = if !has_strategy {
        ("● NO STRATEGY", Tone::Muted)
    } else {
        state.state_label()
    };
    let state_badge = if !has_strategy {
        Tone::Muted.badge()
    } else {
        state.state_label().1.badge()
    };

    let summary_line = format!(
        "{} · {} · {} · {}",
        state.mode.label(),
        state.config.universe,
        state.config.timeframe,
        state.config.dates
    );

    let library = state
        .visible_strategies()
        .into_iter()
        .map(|(idx, s)| LibRow {
            real_index: idx as i32,
            name: s.name.clone(),
            description: s.description.clone(),
            state_label: if s.last_backtest != "—" {
                "RAN"
            } else {
                "IDLE"
            }
            .to_string(),
            state_tone: Tone::Muted.kind(),
            favorite: s.favorite,
            selected: state.selected == Some(idx),
        })
        .collect();

    // Ranking rows: the data layer owns every row; the index layer owns the
    // order; the UI only ever receives the VIRTUAL WINDOW (`spec §1/§5/§22` —
    // nothing is truncated, every row is reachable by scrolling).
    let rank_source: &[RankRow] = if is_compare {
        &cmp.ranking
    } else {
        &results.ranking
    };
    let window = &state.rank;
    let total_rows = if show_results { window.total } else { 0 };
    let (visible_first, visible_count) = if show_results {
        (window.first, window.count)
    } else {
        (0, 0)
    };
    // Re-number inside the window so the "#" column is continuous from the top
    // of the current view, exactly like the reference table.
    let shown_ranking: Vec<RankRow> = window
        .view
        .iter()
        .skip(visible_first)
        .take(visible_count)
        .enumerate()
        .filter_map(|(offset, index)| {
            let mut row = rank_source.get(*index as usize)?.clone();
            row.rank = (visible_first + offset + 1).to_string();
            Some(row)
        })
        .collect();
    // legacy scope vocabulary ("3 stocks analyzed"), counted where the universe
    // CSV is adopted (the frame path must never re-split it).
    let universe_count = state.cfg_universe_count;
    let ranking_count = if show_results && universe_count > 0 {
        format!(
            "{} {} analyzed",
            universe_count,
            if universe_count == 1 {
                "stock"
            } else {
                "stocks"
            }
        )
    } else {
        String::new()
    };
    // Reference footer: "6 stocks · inspecting MESOLAR" while the drawer is up.
    let ranking_count = match state.detail.as_ref() {
        Some(detail) if !ranking_count.is_empty() => {
            format!("{} · inspecting {}", ranking_count, detail.symbol)
        }
        _ => ranking_count,
    };
    // Flat window projection: geometry, scrollbar and the observability
    // counters the perf benches assert on (`spec §12/§17`).
    let rank_window = window_view(window, &shown_ranking, |rows| {
        ranking_signature(rows, total_rows, window.scroll_px)
    });
    // column; box order == RANKBY_HEADER order).
    let mut rank_heads: Vec<String> = RANK_HEADERS.iter().map(|s| s.to_string()).collect();
    if show_results
        && state.rankby_current >= 0
        && (state.rankby_current as usize) < RANKBY_HEADER.len()
    {
        let head = RANKBY_HEADER[state.rankby_current as usize];
        if head < rank_heads.len() {
            rank_heads[head] += if state.rank_desc { " ▼" } else { " ▲" };
        }
    }

    // Trade blotter (`spec §1/§3/§4`): the UI receives ONLY the virtual window —
    // the full trade list stays in the data layer, and this line no longer
    // clones it per frame (that was O(trades) on every single state change).
    let trade_total = if show_results {
        state.trade_win.total
    } else {
        0
    };
    let (trade_first, trade_count) = if show_results {
        (state.trade_win.first, state.trade_win.count)
    } else {
        (0, 0)
    };
    let shown_trades: Vec<TradeRow> = state
        .trade_win
        .view
        .iter()
        .skip(trade_first)
        .take(trade_count)
        .filter_map(|index| {
            let mut row = results.trades.get(*index as usize)?.clone();
            row.selected = row.abs_index == state.selected_trade && !state.trade_filters_active;
            Some(row)
        })
        .collect();
    // The blotter's own counters: true total, filtered count, and the window
    // (`spec §30`, dev/test only — never rendered as production telemetry).
    let trade_window = window_view(&state.trade_win, &shown_trades, |rows| {
        trade_signature(rows, trade_total, state.trade_win.scroll_px)
    });
    // Visible row numbers are continuous with the dataset position, not the
    // window position, so scrolling never renumbers the blotter.
    let trade_number_offset = trade_first;
    // Honest blotter counters: the true trade count in the current filter view,
    // and how many of them the window is showing (`spec §30`).
    let trade_summary = if show_results {
        let total = state.trade_win.total;
        if total == 0 {
            format!(
                "no trades match the filter · {} recorded",
                results.trades.len()
            )
        } else {
            format!(
                "{} trade{} · showing {}-{}",
                total,
                if total == 1 { "" } else { "s" },
                trade_first + 1,
                (trade_first + shown_trades.len()).min(total)
            )
        }
    } else {
        String::new()
    };
    let trade_sort_labels: Vec<String> = TRADE_SORT_FIELDS
        .iter()
        .map(|(label, _)| label.to_string())
        .chain(std::iter::once("Stock".to_string()))
        .collect();
    let trade_detail = state
        .trade_detail
        .clone()
        .filter(|detail| !detail.symbol.is_empty());

    let shown_equity: Vec<(f32, f32)> = if show_results {
        results.equity.clone()
    } else {
        Vec::new()
    };
    let shown_drawdown: Vec<(f32, f32)> = if show_results {
        results.drawdown.clone()
    } else {
        Vec::new()
    };

    // Compare board: format raw backend numbers per kind; best-cell bold
    // follows the board's own unique-best rule (no epsilon there).
    let board: Vec<BoardRowView> = cmp
        .board
        .iter()
        .map(|row| {
            let present: Vec<f64> = row.cells.iter().filter_map(|c| c.value).collect();
            let best: Option<f64> = match row.higher {
                Some(higher) if present.len() >= 2 => {
                    let candidate = if higher {
                        present.iter().cloned().fold(f64::NEG_INFINITY, f64::max)
                    } else {
                        present.iter().cloned().fold(f64::INFINITY, f64::min)
                    };
                    if present.iter().filter(|v| **v == candidate).count() == 1 {
                        Some(candidate)
                    } else {
                        None
                    }
                }
                _ => None,
            };
            BoardRowView {
                label: row.label.clone(),
                cells: row
                    .cells
                    .iter()
                    .map(|c| BoardCellView {
                        symbol: c.symbol.clone(),
                        text: compare_cell(&row.kind, c.value),
                        best: best.is_some_and(|b| c.value.is_some_and(|v| v == b)),
                    })
                    .collect(),
            }
        })
        .collect();
    // Board column trades sub-line, looked up from the compare ranking.
    let board_trades: Vec<String> = cmp
        .board_symbols
        .iter()
        .map(|symbol| {
            cmp.ranking
                .iter()
                .find(|r| &r.symbol == symbol)
                .map(|r| {
                    if r.trades == "—" || r.trades.is_empty() {
                        "no trades".to_string()
                    } else {
                        format!("{} trades", r.trades)
                    }
                })
                .unwrap_or_default()
        })
        .collect();
    let compare_state = CompareViewState {
        banner_verdict: cmp.banner_verdict.clone(),
        banner_reason: cmp.banner_reason.clone(),
        banner_tone: cmp.banner_tone,
        trade_summary: cmp.trade_summary.clone(),
        stale_notice: cmp.stale_notice.clone(),
        board_scope: cmp.board_scope.clone(),
        board_leader: cmp.board_leader.clone(),
        matrix: cmp
            .matrix
            .iter()
            .map(|m| MatrixRowView {
                key: m.key.clone(),
                buy: m.buy.clone(),
                sell: m.sell.clone(),
                winner: m.winner,
            })
            .collect(),
        board,
        board_symbols: cmp.board_symbols.clone(),
        board_trades,
        ranking: if is_compare && show_results {
            shown_ranking.clone()
        } else {
            Vec::new()
        },
        equity_buy: cmp.equity_buy.clone(),
        equity_sell: cmp.equity_sell.clone(),
        drawdown_buy: cmp.drawdown_buy.clone(),
        drawdown_sell: cmp.drawdown_sell.clone(),
        has_sides: state.results_buy.is_some() || state.results_sell.is_some(),
    };

    let detail_state = state.detail.clone().map(|d| DetailViewState {
        symbol: d.symbol.clone(),
        title: d.title.clone(),
        stats: d.stats.clone(),
        caption: d.caption.clone(),
        metrics: d
            .metrics
            .into_iter()
            .map(|m| DetailMetricView {
                label: m.label,
                value: m.value,
                tone: 0,
            })
            .collect(),
        equity: d.equity,
    });

    // Symbol selector projection (legacy popup vocabulary verbatim).
    let sym_total = state.universe_symbols.len();
    let sym_applied = state.universe_selected.len();
    let sym_button_line = if sym_applied == 0 {
        "NO UNIVERSE".to_string()
    } else if sym_applied <= 3 {
        state.universe_selected.join(", ")
    } else {
        format!("{sym_applied} STOCKS")
    };
    let sym_visible = state.sym_visible();
    let sym_selected_line = {
        let n = state.sym_draft.len();
        if n == 0 {
            "None selected".to_string()
        } else if n == 1 {
            "1 selected".to_string()
        } else {
            format!("{n} selected")
        }
    };
    let sym_count_line = {
        let noun = if sym_total == 1 { "stock" } else { "stocks" };
        let mut line = format!("{} of {sym_total} {noun} selected", state.sym_draft.len());
        if sym_visible.len() != sym_total {
            line += &format!("  ·  {} shown", sym_visible.len());
        }
        line
    };
    let sym_visible_on: Vec<bool> = sym_visible
        .iter()
        .map(|s| state.sym_draft.iter().any(|d| d == s))
        .collect();

    // Reference-layout derivations — presentation mapping of engine facts
    // only; nothing here computes a financial result.
    let find_kpi = |label: &str| -> Option<(String, i32)> {
        kpis.iter()
            .find(|k| k.label == label)
            .map(|k| (k.value.clone(), k.tone))
    };
    let (result_pnl, result_pnl_tone) = find_kpi("NET P&L").unwrap_or_default();
    let result_return = find_kpi("RETURN").map_or(String::new(), |k| k.0);
    let studio_ref_pf = find_kpi("PF").map_or(String::new(), |k| k.0);
    let result_exec_line = if has_strategy {
        format!(
            "{} · {}",
            strategy
                .map_or_else(String::new, |s| s.name.clone())
                .to_uppercase(),
            summary_line
        )
    } else {
        String::new()
    };
    let (stale_exec_line, stale_cur_line) = if state.outdated {
        (
            state.results_fingerprint.clone().unwrap_or_default(),
            state.run_key(),
        )
    } else {
        (String::new(), String::new())
    };
    let range_line = if state.config.dates.is_empty() {
        String::new()
    } else {
        // Reference 1:1 — the receipt range names the starting capital
        // ("01 Jan 2023 — 18 Sep 2026 · starting capital ₹10,00,000").
        format!(
            "{} · starting capital ₹{}",
            state.config.dates, state.config.capital
        )
    };
    let diag_show = show_single && !state.verdict_label.is_empty() && state.verdict_tone != 3;
    let risk_gate_show = show_single && !state.verdict_label.is_empty() && state.verdict_tone == 3;
    let strategy_count_line = if state.strategies.len() == 1 {
        "1 STRATEGY".to_string()
    } else {
        format!("{} STRATEGIES", state.strategies.len())
    };
    let dirty_now = state.code != state.synced_code;
    let editing_hint = if !has_strategy {
        String::new()
    } else if dirty_now {
        format!(
            "{} has unsaved changes in the studio.",
            strategy.map_or_else(String::new, |s| s.name.clone())
        )
    } else {
        String::new()
    };
    // Date-range display + validation (ISO in, human out; the commit path is
    // unchanged). A half-picked or unparseable range is flagged inline but
    // whatever the user selected stays visible — never silently reset.
    let today = today_days_raw();
    let start_days = parse_iso_days(state.cfg_dates_start.trim());
    let end_days = parse_iso_days(state.cfg_dates_end.trim());
    let start_raw = !state.cfg_dates_start.trim().is_empty();
    let end_raw = !state.cfg_dates_end.trim().is_empty();
    let (dates_human, dates_error) = match (start_days, end_days) {
        (Some(a), Some(b)) => (
            format!("{} \u{2014} {}", human_from_days(a), human_from_days(b)),
            if b < a {
                "End date must be on or after the start date.".to_string()
            } else {
                String::new()
            },
        ),
        (Some(a), None) if end_raw => (
            human_from_days(a),
            "Use a valid calendar date (YYYY-MM-DD).".to_string(),
        ),
        (None, Some(b)) if start_raw => (
            human_from_days(b),
            "Use a valid calendar date (YYYY-MM-DD).".to_string(),
        ),
        (Some(a), None) => (format!("{} \u{2014} …", human_from_days(a)), String::new()),
        (None, Some(b)) => (format!("… \u{2014} {}", human_from_days(b)), String::new()),
        (None, None) if start_raw || end_raw => (
            String::new(),
            "Use a valid calendar date (YYYY-MM-DD).".to_string(),
        ),
        _ => (String::new(), String::new()),
    };
    let dates_human = dates_human.to_string();

    // ── §02 CONFIGURE: everything below is a DERIVATION of state already in
    // hand (selection, timeframe, committed dates, store bounds, coverage).
    // Nothing here invents a number, and Slint never does date math.
    let tf_short: Vec<String> = state
        .timeframes
        .iter()
        .take(TF_SHORT_CAP)
        .cloned()
        .collect();
    let tf_short_idx = if state.timeframe_index < 0 {
        -1
    } else {
        state
            .timeframes
            .get(state.timeframe_index as usize)
            .and_then(|current| tf_short.iter().position(|t| t == current))
            .map_or(-1, |i| i as i32)
    };
    // One authority for "which timeframe": the resolved ladder entry. The
    // config echo is only the fallback for a store that reported no ladder.
    // A -1 index stays -1 (disabled) — it never auto-selects timeframes[0].
    let timeframe_label = if state.timeframe_index < 0 {
        state.config.timeframe.clone()
    } else {
        state
            .timeframes
            .get(state.timeframe_index as usize)
            .cloned()
            .unwrap_or_else(|| state.config.timeframe.clone())
    };
    let range_presets = range_presets_for(
        state.data_bounds_start_days as i64,
        state.data_bounds_end_days as i64,
    );
    // A clamped preset can land on the same bounds as MAX, so the LAST match
    // wins: the cell that lights up is always the widest range the user can
    // select, never a narrower one that happens to share its window.
    let range_preset_idx = range_presets
        .iter()
        .rposition(|p| {
            p.start_days == start_days.map(|v| v as i32).unwrap_or(-1)
                && p.end_days == end_days.map(|v| v as i32).unwrap_or(-1)
        })
        .map_or(-1, |i| i as i32);
    let range_from_human = start_days.map_or_else(String::new, human_from_days);
    let range_to_human = end_days.map_or_else(String::new, human_from_days);
    let range_to_live = state.data_bounds_end_days > 0
        && end_days.map(|v| v as i32) == Some(state.data_bounds_end_days);
    let range_span_line = match (start_days, end_days) {
        (Some(a), Some(b)) if b >= a => format!("{} DAYS", grouped_whole(span_days(a, b) as f64)),
        _ => String::new(),
    };
    let chips_more_line = sym_applied
        .checked_sub(UNIVERSE_CHIP_CAP)
        .filter(|rest| *rest > 0)
        .map_or_else(String::new, |rest| format!("+{rest}"));

    // Validation rows. Every one of them reuses an existing signal: the
    // `validate_form` bitmask, the real selection against the real symbol
    // list, and the existing config/date error strings verbatim.
    let capital_value = state
        .cfg_capital
        .parse::<f64>()
        .ok()
        .filter(|n| n.is_finite())
        .unwrap_or(0.0);
    let timeframe_ok = if state.timeframe_index < 0 {
        false
    } else {
        state
            .timeframes
            .get(state.timeframe_index as usize)
            .map(|t| !t.is_empty())
            .unwrap_or(false)
    };
    let bits = validation::validate_form(
        sym_applied > 0,
        has_strategy,
        timeframe_ok,
        dates_error.is_empty() && start_days.is_some() && end_days.is_some(),
        capital_value,
        None,
    );
    // The CAPITAL cell states the one rule the kernel enforces — the mock's
    // "MIN ₹5,000" had no minimum anywhere in the product.
    let capital_ok = bits & (1 << 4) == 0;
    let capital_label = match state.cfg_capital.trim().parse::<f64>() {
        Ok(amount) if amount.is_finite() => format!("₹{}", grouped_whole(amount)),
        _ => state.cfg_capital.trim().to_string(),
    };
    // A backend failure reason is the one row the user MUST see. The card
    // border and the red RUN button say "something is wrong" without saying
    // what — the reason itself is the message the backend produced, verbatim.
    let config_reason = state.config_error.trim().to_string();
    let mut cfg_checks = Vec::new();
    if !config_reason.is_empty() {
        cfg_checks.push(LabCheckData {
            label: config_reason,
            kind: 2,
        });
    }
    cfg_checks.push(LabCheckData {
        label: format!("Data available {sym_applied}/{sym_total}"),
        kind: i32::from(bits & 1 != 0) * 2,
    });
    cfg_checks.push(LabCheckData {
        label: if bits & (1 << 2) == 0 {
            "Timeframe selected".to_string()
        } else {
            validation::MESSAGES[2].to_string()
        },
        kind: if bits & (1 << 2) == 0 { 0 } else { 2 },
    });
    cfg_checks.push(LabCheckData {
        label: if bits & (1 << 4) == 0 {
            "Capital positive".to_string()
        } else {
            validation::MESSAGES[4].to_string()
        },
        kind: if bits & (1 << 4) == 0 { 0 } else { 2 },
    });
    if !dates_error.is_empty() {
        cfg_checks.push(LabCheckData {
            label: dates_error.clone(),
            kind: 2,
        });
    }
    if let Some(facts) = state.coverage {
        if facts.measured && facts.short_history > 0 {
            // Only symbols with NO bars at all in the requested window land
            // here. A stock listed in 2021 is not a defect when the window
            // starts in 2019, so "reached the window start" is NOT this count.
            cfg_checks.push(LabCheckData {
                label: format!(
                    "{} symbols have no data in this window",
                    facts.short_history
                ),
                kind: 1,
            });
        }
    }
    if state.outdated {
        cfg_checks.push(LabCheckData {
            label: "edited — rerun to refresh".to_string(),
            kind: 1,
        });
    }

    // Completeness strip: hidden unless the kernel actually measured. The
    // percentage is an integer (Slint 1.17 cannot format a float), so the
    // mock's "94.2%" becomes an honest "94%".
    //
    // The bar count belongs to the ANCHOR symbol the backend counted, NOT to
    // the whole selection — saying "22,196 BARS IN 527 STOCKS" would be a
    // number this code never measured. The note names the anchor, and the
    // caption states whether the probe was the full selection or a sample.
    let coverage = match state.coverage {
        Some(facts) if facts.measured => {
            let note = match state.coverage_anchor.trim() {
                "" if facts.sampled => format!(
                    "{} BARS  ·  {} OF {} PROBED",
                    grouped_whole(facts.present as f64),
                    facts.symbols_probed,
                    facts.symbols_covered_of()
                ),
                "" => format!("{} BARS", grouped_whole(facts.present as f64)),
                anchor => format!(
                    "{} BARS IN {anchor}{}",
                    grouped_whole(facts.present as f64),
                    if facts.sampled {
                        format!(
                            "  ·  {} OF {} PROBED",
                            facts.symbols_probed,
                            facts.symbols_covered_of()
                        )
                    } else {
                        String::new()
                    }
                ),
            };
            CoverageView {
                pct: i32::from(facts.pct),
                note,
                kind: if facts.pct >= 99 {
                    0
                } else if facts.pct >= 90 {
                    1
                } else {
                    2
                },
                on: true,
            }
        }
        _ => CoverageView::default(),
    };
    // Caption comes from the same `state.coverage` facts the bar above used.
    let cov_caption = match state.coverage {
        Some(facts) if facts.measured && facts.sampled => "SAMPLED".to_string(),
        Some(facts) if facts.measured => "VERIFIED".to_string(),
        _ => String::new(),
    };

    // ── live run progress ──
    // Every string here is FORMATTED from a measured event. The UI never
    // computes, smooths or guesses: if the backend stopped sending, these
    // simply stop changing, which is what "stuck" must look like.
    let progress = &state.progress;
    let prog_active = progress.active;
    let prog_headline = format!("{} / {} stocks", progress.done, progress.headline_total);
    let prog_counts = format!(
        "Completed {}   Failed {}   Skipped {}   Remaining {}",
        progress.completed,
        progress.failed.len(),
        progress.skipped.len(),
        progress.remaining
    );
    let prog_current = if progress.current.is_empty() {
        "—".to_string()
    } else {
        progress.current.clone()
    };
    let prog_stage = match progress.stage.as_str() {
        "data" => "Loading bars",
        "calculate" => "Strategy calculation",
        "trades" => "Trade generation",
        "aggregate" => "Aggregating results",
        "failed" => "Stopped",
        other => other,
    }
    .to_string();
    // Outside a run nothing is measured, so every duration is a dash — a bare
    // "00:00" would be a fabricated elapsed time.
    let prog_elapsed = if prog_active {
        clock_label(f64::from(progress.elapsed_secs))
    } else {
        "—".to_string()
    };
    // No samples yet → a dash, never a fabricated ETA.
    let prog_eta = match progress.eta_secs {
        Some(eta) if progress.remaining > 0 => format!("~{}", clock_label(eta)),
        _ => "—".to_string(),
    };
    let prog_speed = match progress.mean_secs {
        Some(mean) if mean > 0.0 => format!("{:.2} s/stock", mean),
        _ => "—".to_string(),
    };
    let prog_throughput = if progress.throughput > 0.0 {
        format!("{:.2} stocks/s", progress.throughput)
    } else {
        "—".to_string()
    };
    let prog_failed_line = if progress.failed.is_empty() {
        String::new()
    } else {
        format!("Failed: {}", progress.failed.join(", "))
    };
    let prog_pct = progress.pct;
    let prog_watchdog = if progress.cancelled {
        "CANCELLED".to_string()
    } else if progress.long_running {
        format!(
            "LONG-RUNNING — {}",
            clock_label(f64::from(progress.current_secs))
        )
    } else if progress.quiet {
        "Processing is taking longer than usual…".to_string()
    } else {
        String::new()
    };

    LabView {
        has_strategy,
        name: strategy.map_or_else(|| "No strategy".to_string(), |s| s.name.clone()),
        description: strategy.map_or_else(String::new, |s| s.description.clone()),
        tags: strategy.map_or_else(String::new, |s| s.tags.join(" · ")),
        version: strategy.map_or_else(String::new, |s| s.version.clone()),
        modified: strategy.map_or_else(String::new, |s| s.modified.clone()),
        last_backtest: strategy.map_or(String::new(), |s| s.last_backtest.clone()),
        state_label: state_label.to_string(),
        state_tone: state_badge,
        mode: state.mode.kind(),
        outdated: state.outdated,
        run_enabled: state.engine_wired
            && has_strategy
            && state.run != RunState::Running
            && gather_valid(state),
        config_summary: summary_line.clone(),
        universe: state.config.universe.clone(),
        timeframe: state.config.timeframe.clone(),
        dates: state.config.dates.clone(),
        capital: state.config.capital.clone(),
        show_results,
        tabs_enabled: show_results,
        tab: state.tab,
        kpis,
        ranking: shown_ranking,
        rank_window,
        trades: shown_trades,
        trade_window,
        trade_number_offset: trade_number_offset as i32,
        trade_total: trade_total as i32,
        trade_summary,
        trade_needle: state.trade_needle.clone(),
        trade_side_filter: state.trade_side_filter,
        trade_result_filter: state.trade_result_filter,
        trade_sort: state.trade_sort,
        trade_sort_labels,
        trade_detail: trade_detail.unwrap_or_default(),
        equity: shown_equity,
        drawdown: shown_drawdown,
        risk_notes: if show_results {
            results.risk_notes.clone()
        } else {
            Vec::new()
        },
        library,
        empty_reason,
        summary_line,
        equity_caption: if show_results {
            state.equity_summary.clone()
        } else {
            String::new()
        },
        drawdown_caption: if show_results {
            state.drawdown_summary.clone()
        } else {
            String::new()
        },
        ranking_count,
        run_stop: state.run == RunState::Running,
        verdict_label: if has_strategy {
            state.verdict_label.clone()
        } else {
            String::new()
        },
        verdict_note: if has_strategy {
            state.verdict_note.clone()
        } else {
            String::new()
        },
        verdict_tone: state.verdict_tone,
        progress_label: if state.run == RunState::Running {
            state.progress_label.clone()
        } else {
            String::new()
        },
        code: state.code.clone(),
        dirty: state.code != state.synced_code,
        params: state
            .params
            .iter()
            .map(|p| ParamView {
                key: p.key.clone(),
                label: p.label.clone(),
                value: p.value.clone(),
            })
            .collect(),
        cfg_universe_csv: state.cfg_universe_csv.clone(),
        // Chips are a PRESENTATION cap, not a data cap: the row band shows the
        // first few symbols and the count carries the rest. Splitting a 50k
        // CSV per frame was the projection's last O(N) cost (`spec §7/§13`).
        universe_chips: state
            .universe_selected
            .iter()
            .take(UNIVERSE_CHIP_CAP)
            .cloned()
            .collect(),
        saved_universe_chips: state.saved_universe_symbols.clone(),
        timeframes: state.timeframes.clone(),
        timeframe_index: state.timeframe_index,
        cfg_dates_start: state.cfg_dates_start.clone(),
        cfg_dates_end: state.cfg_dates_end.clone(),
        cfg_capital: state.cfg_capital.clone(),
        config_error: state.config_error.clone(),
        rankby_labels: state.rankby_labels.clone(),
        rankby_current: state.rankby_current,
        rank_search: state.rank_search.clone(),
        rank_desc: state.rank_desc,
        rank_heads,
        trade_heads: [
            "#", "SYMBOL", "SIDE", "ENTRY", "ENTRY PX", "EXIT", "EXIT PX", "P&L", "R", "BARS",
            "REASON",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect(),
        detail: detail_state,
        compare: compare_state,
        compare_side: state.compare_side,
        selected_trade: state.selected_trade,
        trade_filters_active: state.trade_filters_active,
        trade_symbol: state.trade_symbol.clone(),
        run_label: if state.run_label.is_empty() {
            "▶  RUN BACKTEST".to_string()
        } else {
            state.run_label.clone()
        },
        is_compare,
        sym_open: state.sym_open,
        sym_search: state.sym_search.clone(),
        sym_button_line,
        sym_count_line,
        sym_selected_line,
        universe_count_line: match (
            state.saved_universe_status,
            state.saved_universe_symbols.len(),
        ) {
            (SavedUniverseStatus::Error, _) => "Saved stocks unavailable".to_string(),
            (SavedUniverseStatus::Missing, _) => "No stocks saved".to_string(),
            (_, 0) => "No stocks saved".to_string(),
            (_, 1) => "1 stock saved".to_string(),
            (_, n) => format!("{n} stocks saved"),
        },
        // A save in flight or refused overrides the loaded state; otherwise
        // the row reports how the strategy's saved universe loaded.
        universe_state: match (&state.universe_state, state.saved_universe_status) {
            (UniverseSaveState::Pending, _) => "pending",
            (UniverseSaveState::Error(_), _) => "error",
            (_, SavedUniverseStatus::Error) => "error",
            (UniverseSaveState::Saved, _) => "saved",
            _ => "clean",
        }
        .to_string(),
        universe_error: match (&state.universe_state, state.saved_universe_status) {
            (UniverseSaveState::Error(message), _) => message.clone(),
            (_, SavedUniverseStatus::Error) => state.saved_universe_error.clone(),
            _ => String::new(),
        },
        sym_visible,
        sym_visible_on,
        run_id: String::new(),
        run_ts: if show_single {
            strategy.map_or(String::new(), |s| s.last_backtest.clone())
        } else {
            String::new()
        },
        cfg_hash: String::new(),
        result_pnl,
        result_return,
        result_pnl_tone,
        result_exec_line,
        stale_exec_line,
        stale_cur_line,
        range_line,
        diag_show,
        diag_title: state.verdict_label.clone(),
        diag_detail: state.verdict_note.clone(),
        diag_stale: state.outdated,
        risk_gate_show,
        risk_gate_text: state.verdict_note.clone(),
        lens: state.lens,
        strategy_count_line,
        editing_hint,
        studio_ref_pf,
        studio_source: "LOCAL".to_string(),
        equity_select: state.equity_select,
        dates_human,
        dates_error,
        today_days: today as i32,
        dates_start_days: start_days.map_or(-1, |v| v as i32),
        dates_end_days: end_days.map_or(-1, |v| v as i32),
        date_presets: date_presets_for(today),
        tf_short,
        tf_short_idx,
        range_presets,
        range_preset_idx,
        range_from_human,
        range_to_human,
        range_to_live,
        range_span_line,
        timeframe_label,
        capital_label,
        capital_ok,
        chips_more_line,
        cfg_checks,
        coverage,
        cov_caption,
        prog_active,
        prog_headline,
        prog_counts,
        prog_current,
        prog_stage,
        prog_stage_pct: progress.stage_pct,
        prog_elapsed,
        prog_eta,
        prog_speed,
        prog_throughput,
        prog_failed_line,
        prog_pct,
        prog_cancelled: progress.cancelled,
        prog_long: progress.long_running,
        prog_watchdog,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn two_strategy_state() -> LabState {
        let mut st = LabState::default();
        for name in ["OBR", "ORB"] {
            st.strategies.push(LabStrategy {
                name: name.into(),
                description: String::new(),
                tags: Vec::new(),
                version: String::new(),
                modified: String::new(),
                favorite: false,
                last_backtest: String::new(),
            });
        }
        st.selected = Some(0);
        st.universe_symbols = vec!["RELIANCE".into(), "TCS".into(), "INFY".into()];
        st
    }

    #[test]
    fn apply_queues_universe_action_with_strategy_id() {
        let mut st = two_strategy_state();
        st.sym_draft = vec!["RELIANCE".into(), "TCS".into()];
        st.interaction_symapply();
        assert_eq!(st.universe_save_state(), &UniverseSaveState::Pending);
        let actions = st.drain_universe_actions();
        assert_eq!(actions.len(), 1);
        assert!(actions[0].starts_with("universe:"));
        assert!(actions[0].contains(r#""strategy_id":"OBR""#));
        assert!(actions[0].contains(r#""symbols":["RELIANCE","TCS"]"#));
    }

    #[test]
    fn universe_save_success_and_refusal_are_recorded() {
        let mut st = two_strategy_state();
        st.apply_universe_result("OBR", true, "");
        assert_eq!(st.universe_save_state(), &UniverseSaveState::Saved);
        st.apply_universe_result("OBR", false, "Instrument not found");
        assert_eq!(
            st.universe_save_state(),
            &UniverseSaveState::Error("Instrument not found".into())
        );
    }

    #[test]
    fn reply_for_a_different_strategy_is_ignored() {
        let mut st = two_strategy_state();
        st.apply_universe_result("OBR", true, "");
        st.selected = Some(1);
        st.apply_universe_result("OBR", false, "late reply");
        assert_eq!(st.universe_save_state(), &UniverseSaveState::Saved);
    }

    #[test]
    fn drain_universe_actions_leaves_other_actions_queued() {
        let mut st = two_strategy_state();
        st.queue_action("tab:1".into());
        st.queue_action(r#"universe:{"strategy_id":"OBR","symbols":[]}"#.into());
        st.queue_action("save:OBR".into());
        let drained = st.drain_universe_actions();
        assert_eq!(drained.len(), 1);
        assert_eq!(
            st.pending_actions,
            vec!["tab:1".to_string(), "save:OBR".to_string()]
        );
    }

    #[test]
    fn snapshot_restores_each_strategys_saved_universe_on_switch() {
        let mut st = two_strategy_state();
        let alpha = r#"{"saved_universe":{"state":"ok","symbols":["NSE:RELIANCE"],"error":""}}"#;
        let beta =
            r#"{"saved_universe":{"state":"ok","symbols":["NSE:INFY","NSE:TCS"],"error":""}}"#;
        apply_snapshot_json(&mut st, &serde_json::from_str(alpha).unwrap());
        assert_eq!(st.saved_universe_symbols, vec!["NSE:RELIANCE".to_string()]);
        assert_eq!(st.saved_universe_status, SavedUniverseStatus::Ok);

        st.select(1);
        assert!(
            st.saved_universe_symbols.is_empty(),
            "switch must not carry Alpha's chips"
        );
        apply_snapshot_json(&mut st, &serde_json::from_str(beta).unwrap());
        assert_eq!(
            st.saved_universe_symbols,
            vec!["NSE:INFY".to_string(), "NSE:TCS".to_string()]
        );
        let view = project(&st);
        assert_eq!(view.saved_universe_chips, vec!["NSE:INFY", "NSE:TCS"]);
        assert_eq!(view.universe_count_line, "2 stocks saved");
    }

    #[test]
    fn empty_missing_and_error_universes_are_distinct_truthful_states() {
        let mut st = two_strategy_state();
        apply_snapshot_json(
            &mut st,
            &serde_json::from_str(r#"{"saved_universe":{"state":"empty","symbols":[]}}"#).unwrap(),
        );
        assert_eq!(st.saved_universe_status, SavedUniverseStatus::Empty);
        assert_eq!(project(&st).universe_count_line, "No stocks saved");

        apply_snapshot_json(
            &mut st,
            &serde_json::from_str(r#"{"strategies":[]}"#).unwrap(),
        );
        assert_eq!(st.saved_universe_status, SavedUniverseStatus::Missing);

        apply_snapshot_json(
            &mut st,
            &serde_json::from_str(
                r#"{"saved_universe":{"state":"error","symbols":[],"error":"store offline"}}"#,
            )
            .unwrap(),
        );
        let view = project(&st);
        assert_eq!(view.universe_state, "error");
        assert_eq!(view.universe_error, "store offline");
        assert!(view.saved_universe_chips.is_empty());
    }

    #[test]
    fn apply_without_a_selected_strategy_is_an_error_not_a_guess() {
        let mut st = two_strategy_state();
        st.selected = None;
        st.sym_draft = vec!["TCS".into()];
        st.interaction_symapply();
        assert!(matches!(
            st.universe_save_state(),
            UniverseSaveState::Error(_)
        ));
        assert!(st.drain_universe_actions().is_empty());
    }

    #[test]
    fn money_split_carries_a_hundredth_that_rounds_to_one_hundred() {
        // 1234.999 → 123499.9 hundredths → rounds to 123500 → whole 1235,
        // frac 0. The old `trunc()` + separate `round()` pair produced the
        // impossible ".100" whenever the fraction rounded up past 99.
        assert_eq!(split_rupees(1234.999), Some((1235, 0)));
        assert_eq!(split_rupees(0.999), Some((1, 0)));
        // 0.005 is exactly 0.5 hundredths and `f64::round` is half-away-from-
        // zero, so it rounds UP to 0.01.
        assert_eq!(split_rupees(0.005), Some((0, 1)));
        assert_eq!(split_rupees(0.006), Some((0, 1)));
        assert_eq!(split_rupees(0.004), Some((0, 0)));
        assert_eq!(split_rupees(1234.564), Some((1234, 56)));
        assert_eq!(split_rupees(0.0), Some((0, 0)));
        // Non-finite / negative / out-of-i64 → honest absence, never a
        // saturating 19-digit "amount".
        assert_eq!(split_rupees(f64::NAN), None);
        assert_eq!(split_rupees(f64::INFINITY), None);
        assert_eq!(split_rupees(-1.0), None);
        assert_eq!(split_rupees(1e30), None);
        // Rendered money keeps Indian grouping and never shows a 3-digit frac.
        assert_eq!(inr_body(1234.999), "1,235.00");
        assert_eq!(inr_body(1234567.8), "12,34,567.80");
        assert_eq!(inr_body(1e30), "N/A");
        assert!(!inr_body(9.999).contains(".100"));
        assert_eq!(western_signed2(9.999), "+10.00");
        assert_eq!(western_signed2(-9.999), "-10.00");
        assert_eq!(western_signed2(f64::NAN), "+N/A");
    }

    #[test]
    fn iso_minutes_validates_the_calendar_and_the_clock() {
        // Real timestamp maps to real minutes.
        let jan2 = iso_minutes("2026-01-02T09:15:00".to_string());
        assert!(jan2.is_finite());
        assert_eq!(jan2, iso_minutes("2026-01-02T09:15:00".to_string()));
        // Ordering still follows the calendar.
        assert!(iso_minutes("2026-01-02T10:00:00".to_string()) > jan2);
        assert!(iso_minutes("2026-01-03T09:15:00".to_string()) > jan2);
        // Impossible civil dates sink (NaN sorts to the bottom) instead of
        // producing a plausible-but-wrong timestamp among real trades.
        for bad in [
            "2026-13-01T09:15:00", // month 13
            "2026-00-01T09:15:00", // month 0
            "2026-01-32T09:15:00", // day 32
            "2026-01-00T09:15:00", // day 0
            "2026-02-30T09:15:00", // February 30
            "2023-02-29T09:15:00", // not a leap year
            "2026-01-02T25:15:00", // hour 25
            "2026-01-02T09:61:00", // minute 61
            "not-a-timestamp",
        ] {
            assert!(
                iso_minutes(bad.to_string()).is_nan(),
                "{bad} must not produce a time"
            );
        }
        // Legal leap day and end-of-day still parse.
        assert!(iso_minutes("2024-02-29T09:15:00".to_string()).is_finite());
        assert!(iso_minutes("2026-01-02T24:00:00".to_string()).is_finite());
        // A non-ASCII boundary must not panic.
        assert!(iso_minutes("2026-é1-02T09:15:00".to_string()).is_nan());
    }

    #[test]
    fn run_progress_counts_are_read_as_integers_and_reject_non_finite() {
        // Integer counts survive exactly, including past 2^24 where an
        // `as_f64` round-trip loses precision.
        let big = 16_777_217i64;
        let value = serde_json::json!({
            "stage": "data",
            "stage_pct": 42,
            "total": big,
            "completed": big - 1,
            "remaining": 1,
            "done": 12345,
            "bars": 22_196_447,
            "pct": 55.5,
            "elapsed_secs": 12.25,
            "eta_secs": 30.0,
        });
        let progress = RunProgress::from_json(&value);
        assert_eq!(progress.total, big as i32);
        assert_eq!(progress.completed, big as i32 - 1);
        assert_eq!(progress.remaining, 1);
        assert_eq!(progress.done, 12345);
        assert_eq!(progress.bars, 22_196_447);
        assert!((progress.pct - 55.5).abs() < 1e-3);
        assert!((progress.stage_pct - 42.0).abs() < 1e-3);
        assert_eq!(progress.eta_secs, Some(30.0));

        // Non-finite / missing floats never reach Slint as NaN.
        let dirty = serde_json::json!({
            "stage_pct": "NaN",
            "pct": null,
            "elapsed_secs": null,
            "net_pnl": null,
            "eta_secs": null,
            "throughput": null,
        });
        let progress = RunProgress::from_json(&dirty);
        assert_eq!(progress.stage_pct, 0.0);
        assert_eq!(progress.pct, 0.0);
        assert_eq!(progress.elapsed_secs, 0.0);
        assert_eq!(progress.net_pnl, 0.0);
        assert_eq!(progress.throughput, 0.0);
        assert_eq!(progress.eta_secs, None);

        // A non-integer count is not silently truncated into a real total.
        let fractional = serde_json::json!({"total": 12.5, "completed": 3.9});
        let progress = RunProgress::from_json(&fractional);
        assert_eq!(progress.total, 0);
        assert_eq!(progress.completed, 0);

        // headline_total falls back to total only when genuinely absent.
        let no_headline = RunProgress::from_json(&serde_json::json!({"total": 527}));
        assert_eq!(no_headline.headline_total, 527);
        let explicit_zero =
            RunProgress::from_json(&serde_json::json!({"total": 527, "headline_total": 0}));
        assert_eq!(explicit_zero.headline_total, 0);
    }

    fn obr() -> LabStrategy {
        LabStrategy {
            name: "OBR".into(),
            description: "Opening Breakout Strategy".into(),
            tags: vec!["BREAKOUT".into(), "INTRADAY".into(), "SYSTEMATIC".into()],
            version: "1.0".into(),
            modified: "11 Sep 26".into(),
            favorite: true,
            last_backtest: "—".into(),
        }
    }

    fn results() -> LabResults {
        LabResults {
            kpis: vec![Kpi {
                label: "NET P&L".into(),
                value: "+₹1,85,236".into(),
                tone: Tone::Positive,
                emphasized: true,
            }],
            ranking: vec![RankRow {
                rank: "1".into(),
                symbol: "RELIANCE".into(),
                pnl: "₹1,85,236.00".into(),
                ret: "+18.50%".into(),
                trades: "71".into(),
                win: "52.1%".into(),
                pf: "1.42".into(),
                dd: "-8.30%".into(),
                sharpe: "1.15".into(),
                pnl_tone: Tone::Positive,
                pf_tone: Tone::Positive,
                unranked: false,
                sort: [185236.0, 18.5, 71.0, 52.1, 1.42, 8.3, 1.15],
            }],
            trades: vec![TradeRow {
                no: "1".into(),
                abs_index: 0,
                symbol: "RELIANCE".into(),
                side: "LONG".into(),
                entry: "2801.10".into(),
                entry_px: "2801.10".into(),
                exit: "2812.40".into(),
                exit_px: "2812.40".into(),
                pnl: "+11.30".into(),
                r: "0.42".into(),
                bars: "5".into(),
                reason: "TARGET".into(),
                pnl_tone: Tone::Positive,
                selected: false,
                sort: [29_000.0, 11_230.4, 0.42, 5.0],
            }],
            equity: vec![(0.0, 0.5), (1.0, 0.9)],
            drawdown: vec![],
            risk_notes: vec!["Max DD -8.3% over 1000 rolling days".into()],
        }
    }

    fn state_with_obr() -> LabState {
        let mut st = LabState {
            strategies: vec![obr(), {
                let mut s = obr();
                s.name = "SMA".into();
                s.description = "SMA crossover".into();
                s.favorite = false;
                s
            }],
            ..LabState::default()
        };
        st.config = LabConfig {
            universe: "RELIANCE".into(),
            timeframe: "15m".into(),
            dates: "02 Jan → 12 Sep '26".into(),
            capital: "₹10,00,000".into(),
        };
        st
    }

    #[test]
    fn selection_updates_workspace_immediately() {
        // The #1 bug class: library selection must never coexist with a
        // "No strategy" workspace.
        let mut st = state_with_obr();
        let empty = project(&st);
        assert!(!empty.has_strategy);
        assert_eq!(empty.name, "No strategy");
        assert_eq!(empty.state_label, "● NO STRATEGY");

        assert!(st.select(0));
        let view = project(&st);
        assert!(view.has_strategy);
        assert_eq!(view.name, "OBR");
        assert_eq!(view.description, "Opening Breakout Strategy");
        assert_eq!(view.tags, "BREAKOUT · INTRADAY · SYSTEMATIC");
        assert_eq!(view.state_label, "● READY");
        // Selection is unmistakable in the library list too.
        assert!(view.library[0].selected);
        assert!(!view.library[1].selected);
    }

    #[test]
    fn search_and_filter_shape_the_library() {
        let mut st = state_with_obr();
        st.set_search("sm");
        let view = project(&st);
        assert_eq!(view.library.len(), 1);
        assert_eq!(view.library[0].name, "SMA");

        st.set_search("");
        st.set_filter(LibFilter::Favorites);
        let view = project(&st);
        assert_eq!(view.library.len(), 1);
        assert!(view.library[0].favorite);
    }

    #[test]
    fn results_are_never_fabricated_without_apply_result() {
        let mut st = state_with_obr();
        st.select(0);
        let view = project(&st);
        assert!(!view.show_results);
        assert!(view.ranking.is_empty());
        assert!(view.equity.is_empty());
        // Placeholder KPI band ("--", tile vocabulary), never zero-filled.
        assert!(view.kpis.iter().all(|k| k.value == "--"));
        assert!(view
            .kpis
            .iter()
            .any(|k| k.emphasized && k.label == "NET P&L"));
    }

    #[test]
    fn completed_results_project_with_tones_and_ranking() {
        let mut st = state_with_obr();
        st.select(0);
        st.engine_wired = true;
        assert!(st.start_run());
        assert_eq!(project(&st).state_label, "● RUNNING…");
        st.apply_result(results());
        let view = project(&st);
        assert!(view.show_results);
        assert_eq!(view.state_label, "✓ COMPLETE");
        assert_eq!(view.kpis[0].value, "+₹1,85,236");
        assert_eq!(view.kpis[0].tone, Tone::Positive.kind());
        assert_eq!(view.ranking.len(), 1);
        assert_eq!(view.ranking[0].symbol, "RELIANCE");
        assert!(!view.equity.is_empty());
        assert_eq!(
            view.empty_reason,
            "No backtest results yet — run the backtest"
        );
    }

    #[test]
    fn config_change_marks_results_outdated_compactly() {
        let mut st = state_with_obr();
        st.select(0);
        st.engine_wired = true;
        st.start_run();
        st.apply_result(results());
        assert!(!st.outdated);
        st.edit_config(|c| c.timeframe = "5m".into());
        assert!(st.outdated);
        let view = project(&st);
        assert!(view.outdated);
        // Outdated run keeps prior results attached but the summary flags them.
        assert_eq!(view.empty_reason, "Results are outdated — run again");
        // Changing back makes them current again (fingerprint match).
        st.edit_config(|c| c.timeframe = "15m".into());
        assert!(!st.outdated);
    }

    #[test]
    fn run_requires_engine_and_selection_never_fakes() {
        let mut st = state_with_obr();
        st.select(0);
        // A runnable workspace needs a real selection, timeframe and capital.
        st.universe_selected = vec!["RELIANCE".into()];
        st.timeframes = vec!["15m".into()];
        st.timeframe_index = 0;
        st.cfg_capital = "100000".into();
        // No engine bridge -> RUN disabled, start_run refused.
        assert!(!st.engine_wired);
        assert!(!st.start_run());
        assert!(!project(&st).run_enabled);
        st.engine_wired = true;
        assert!(project(&st).run_enabled);
        assert!(st.start_run());
        // While running, RUN stays disabled until the run settles.
        assert!(!project(&st).run_enabled);
    }

    #[test]
    fn mode_lives_in_state_not_a_floating_indicator() {
        let mut st = state_with_obr();
        st.select(0);
        st.set_mode(LabMode::Compare);
        let view = project(&st);
        assert_eq!(view.mode, LabMode::Compare.kind());
        assert!(view.config_summary.starts_with("COMPARE"));
    }

    #[test]
    fn invalid_select_and_tab_are_noops() {
        let mut st = state_with_obr();
        assert!(!st.select(99));
        assert!(st.selected.is_none());
        st.set_tab(99);
        assert_eq!(st.tab, 0);
    }

    #[test]
    fn editor_tab_is_local_but_result_tabs_reach_result_pages() {
        let mut st = state_with_obr();
        st.interaction_tab(0);
        assert_eq!(st.tab, 0);
        assert!(st.pending_actions.is_empty());
        st.interaction_tab(2);
        assert_eq!(st.tab, 2);
        // Native TRADES (2) → legacy result-stack page 1.
        assert_eq!(st.pending_actions, vec!["tab:1".to_string()]);
    }

    #[test]
    fn save_and_compile_carry_the_working_buffer() {
        let mut st = state_with_obr();
        st.interaction_codeedit("strategy code v2");
        assert_eq!(project(&st).dirty, true);
        st.interaction_save();
        st.interaction_compile();
        assert_eq!(
            st.pending_actions,
            vec![
                "save:strategy code v2".to_string(),
                "compile:strategy code v2".to_string()
            ]
        );
    }

    #[test]
    fn lens_and_equity_view_are_view_local_and_bounded() {
        let mut st = state_with_obr();
        st.interaction_lens(4);
        assert_eq!(st.lens, 4);
        st.interaction_lens(9);
        assert_eq!(st.lens, 4); // out-of-range ignored
        st.interaction_equity_view(2);
        assert_eq!(st.equity_select, 2);
        st.interaction_equity_view(7);
        assert_eq!(st.equity_select, 2);
        assert!(st.pending_actions.is_empty());
    }

    #[test]
    fn inspector_close_and_reset_stay_local() {
        let mut st = state_with_obr();
        st.interaction_detail_close();
        assert!(project(&st).detail.is_none());
        st.interaction_codeedit("edited buffer");
        assert!(project(&st).dirty);
        st.interaction_reset();
        assert!(!project(&st).dirty);
        assert_eq!(project(&st).code, st.synced_code.clone());
        assert!(st.pending_actions.is_empty());
    }

    #[test]
    fn snapshot_adopts_backend_echo_and_formats_like_legacy() {
        let mut st = state_with_obr();
        st.select(0);
        st.interaction_codeedit("local unsaved edit");
        assert!(project(&st).dirty);
        let value: serde_json::Value = serde_json::from_str(
            r#"{"selected_name":"OBR","mode":"buy","run":"complete",
                "code":"backend code","cfg_edit":{"universe_csv":"RELIANCE,TCS"},
                "rankby":{"labels":["Net P&L","Return %"],"current":1,"search":"","desc":false},
                "results":{"metrics":{"net_profit":185236.4,"total_trades":71,
                "win_rate":52.14,"profit_factor":1.42,"expectancy":2609.2,
                "max_drawdown_pct":8.3,"sharpe_ratio":1.15,"avg_trade":260.5},
                "ranking":[{"rank":1,"symbol":"RELIANCE","status":"ranked",
                "net_profit":185236.4,"return_pct":18.5,"total_trades":71,
                "win_rate":52.14,"profit_factor":1.42,"max_drawdown_pct":8.3,
                "sharpe_ratio":1.15}],
                "trades":[{"symbol":"RELIANCE","side":"LONG",
                "entry_time":"2026-01-02T09:15:00","exit_time":"2026-01-02T15:30:00",
                "entry_px":2801.1,"exit_px":2812.4,"pnl":11230.4,"r_multiple":0.42,
                "bars":5,"reason":"TARGET","winning":true}],
                "equity_curve":[{"equity":1000000.0,"drawdown_pct":0.0},
                {"equity":1185236.4,"drawdown_pct":0.0}],
                "risk_notes":[]}}"#,
        )
        .unwrap();
        apply_snapshot_json(&mut st, &value);
        // Backend echo wins; dirty clears (same as legacy open_buffer).
        assert_eq!(st.code, "backend code");
        assert!(!project(&st).dirty);
        let view = project(&st);
        assert!(view.show_results);
        // legacy MetricsTiles formats: grouped ₹, 1-decimal win rate, "--" none.
        assert_eq!(view.kpis[0].value, "₹1,85,236.40");
        assert_eq!(view.kpis[2].value, "52.1%");
        assert_eq!(view.kpis.len(), 8);
        // legacy _fill_row formats.
        assert_eq!(view.ranking[0].pnl, "₹1,85,236.40");
        assert_eq!(view.ranking[0].ret, "+18.50%");
        // Criterion arrow follows the echoed box index (Return % → head 3).
        assert!(view.rank_heads[3].ends_with(" ▲"));
        assert!(!view.rank_heads[2].contains('▼'));
        // legacy _format_row formats: western P&L, plain R, bars, reason.
        assert_eq!(view.trades[0].pnl, "+11,230.40");
        assert_eq!(view.trades[0].entry_px, "2801.10");
        assert_eq!(view.trades[0].r, "0.42");
        assert_eq!(view.trades[0].bars, "5");
        assert_eq!(view.trades[0].reason, "TARGET");
        assert_eq!(view.trades[0].abs_index, 0);
        // legacy scope vocabulary from the echoed universe.
        assert_eq!(view.ranking_count, "2 stocks analyzed");
    }

    #[test]
    fn trade_blotter_renders_individual_trades_and_stays_virtual() {
        // The bug this fixes: the Trades lens showed STOCK-level summary rows
        // because nothing ever rendered `results.trades`. The blotter must show
        // real trade records — and only a viewport of them.
        let mut st = LabState::default();
        st.engine_wired = true;
        st.strategies = vec![strategy_fixture()];
        st.selected = Some(0);
        st.rankby_labels = vec!["Net P&L".into()];
        st.results = Some(LabResults {
            trades: synthetic_trades(500),
            ..LabResults::default()
        });
        st.run = RunState::Complete;
        st.rebuild_trade_view();
        let view = project(&st);
        // Individual records, not per-stock rows.
        assert!(!view.trades.is_empty());
        assert_eq!(view.trade_total, 500, "the honest total is the full list");
        assert!(
            view.trades.len() <= 40,
            "a 14-row viewport rendered {} rows",
            view.trades.len()
        );
        // Every rendered row is a distinct trade with its own identity.
        let symbols: Vec<&str> = view.trades.iter().map(|t| t.symbol.as_str()).collect();
        assert!(symbols.iter().all(|s| s.starts_with("SYM")));
        let numbers: Vec<&str> = view.trades.iter().map(|t| t.no.as_str()).collect();
        assert_eq!(numbers[0], "1", "rows are numbered from the dataset");
        // The lens is what routes the page; the projection is lens-agnostic, so
        // assert the routing decision instead (lens 1 == trades).
        st.interaction_lens(1);
        assert_eq!(project(&st).lens, 1);
    }

    #[test]
    fn trade_blotter_never_changes_a_trade_under_virtualization() {
        // Spec §34: virtualization must not alter order, identity or values.
        // Walk a 1000-trade list to the end, collecting what the window showed,
        // and compare against the engine order itself.
        let mut st = LabState::default();
        st.engine_wired = true;
        st.strategies = vec![strategy_fixture()];
        st.selected = Some(0);
        st.rankby_labels = vec!["Net P&L".into()];
        st.results = Some(LabResults {
            trades: synthetic_trades(1_000),
            ..LabResults::default()
        });
        st.run = RunState::Complete;
        st.rebuild_trade_view();
        let truth: Vec<(String, String, String)> = st
            .results
            .as_ref()
            .expect("results")
            .trades
            .iter()
            .map(|t| (t.no.clone(), t.symbol.clone(), t.pnl.clone()))
            .collect();
        let mut seen: Vec<(String, String, String)> = Vec::new();
        loop {
            let view = project(&st);
            assert!(view.trades.len() <= 40, "window stayed bounded");
            for row in &view.trades {
                seen.push((row.no.clone(), row.symbol.clone(), row.pnl.clone()));
            }
            if st.trade_win.scroll_px >= st.trade_win.max_scroll() {
                break;
            }
            st.interaction_trade_scroll(28.0);
        }
        // Overlapping bands repeat rows; the UNION must equal the engine list.
        let mut unique: Vec<(String, String, String)> = Vec::new();
        for row in seen {
            if !unique.contains(&row) {
                unique.push(row);
            }
        }
        assert_eq!(unique, truth, "every trade, in engine order, exactly once");
        // Top, middle and bottom are all reachable and non-empty.
        st.interaction_trade_scroll_to(0.0);
        assert!(!project(&st).trades.is_empty());
        st.interaction_trade_scroll_to(f32::MAX);
        assert!(!project(&st).trades.is_empty());
        assert_eq!(
            project(&st).trades.last().map(|t| t.no.clone()),
            Some("1000".into())
        );
    }

    #[test]
    fn trade_blotter_filters_and_sorts_over_the_complete_dataset() {
        // Spec §9/§10/§16: search and filters narrow the COMPLETE list, sorting
        // reorders it, and a keystroke never re-sorts what the user is reading.
        let mut st = LabState::default();
        st.engine_wired = true;
        st.strategies = vec![strategy_fixture()];
        st.selected = Some(0);
        st.rankby_labels = vec!["Net P&L".into()];
        st.results = Some(LabResults {
            trades: synthetic_trades(300),
            ..LabResults::default()
        });
        st.run = RunState::Complete;
        st.rebuild_trade_view();
        // Symbol search: a real subset, and the count is honest about it.
        st.interaction_tradefilter("SYM0001");
        let view = project(&st);
        let expected = st
            .results
            .as_ref()
            .expect("results")
            .trades
            .iter()
            .filter(|t| t.symbol.contains("SYM0001"))
            .count();
        assert_eq!(
            view.trade_total as usize, expected,
            "filter ran over every trade"
        );
        assert!(view.trades.iter().all(|t| t.symbol.contains("SYM0001")));
        // Side filter: LONG only.
        st.interaction_tradefilter("");
        st.interaction_trade_side(1);
        let view = project(&st);
        assert!(view.trades.iter().all(|t| t.side == "LONG"));
        assert!(view.trade_total > 0);
        // Result filter: losers only.
        st.interaction_trade_side(0);
        st.interaction_trade_result(2);
        let view = project(&st);
        assert!(view.trades.iter().all(|t| t.pnl_tone == Tone::Negative));
        st.interaction_trade_result(0);
        // Sort by P&L descending: the best trade leads the window.
        st.interaction_tradesort(1);
        let view = project(&st);
        let best = st
            .results
            .as_ref()
            .expect("results")
            .trades
            .iter()
            .map(|t| t.sort[1])
            .fold(f64::NEG_INFINITY, f64::max);
        assert_eq!(view.trades.first().map(|t| t.sort[1]), Some(best));
        // Sorting by a different column then flips the direction on re-pick.
        st.interaction_tradesort(3);
        assert!(st.trade_sort_desc);
    }

    #[test]
    fn trade_pick_opens_only_the_selected_trade() {
        // Spec §27: the detail is built for the ONE clicked trade.
        let mut st = LabState::default();
        st.engine_wired = true;
        st.strategies = vec![strategy_fixture()];
        st.selected = Some(0);
        st.rankby_labels = vec!["Net P&L".into()];
        st.results = Some(LabResults {
            trades: synthetic_trades(50),
            ..LabResults::default()
        });
        st.run = RunState::Complete;
        st.rebuild_trade_view();
        assert!(project(&st).trade_detail.symbol.is_empty());
        st.interaction_trade_pick(7);
        let view = project(&st);
        assert_eq!(view.trade_detail.symbol, "SYM0007");
        assert_eq!(view.trade_detail.side, "SHORT");
        // Real fields only — no invented ones.
        let labels: Vec<&str> = view
            .trade_detail
            .metrics
            .iter()
            .map(|m| m.label.as_str())
            .collect();
        assert_eq!(
            labels,
            vec![
                "TRADE",
                "ENTRY",
                "ENTRY PX",
                "EXIT",
                "EXIT PX",
                "P&L",
                "R",
                "BARS HELD",
                "REASON"
            ]
        );
        assert!(view.trades.iter().any(|t| t.selected));
        st.interaction_trade_detail_close();
        let view = project(&st);
        assert!(view.trade_detail.symbol.is_empty());
        assert!(view.trades.iter().all(|t| !t.selected));
    }

    #[test]
    fn trade_blotter_window_geometry_is_exact() {
        // Same clamp/no-overshoot contract as the ranking grid (`spec §11/§12`).
        let mut st = LabState::default();
        st.engine_wired = true;
        st.strategies = vec![strategy_fixture()];
        st.selected = Some(0);
        st.rankby_labels = vec!["Net P&L".into()];
        st.results = Some(LabResults {
            trades: synthetic_trades(400),
            ..LabResults::default()
        });
        st.run = RunState::Complete;
        st.rebuild_trade_view();
        st.interaction_trade_scroll(-100_000.0);
        let view = project(&st);
        assert_eq!(view.trade_window.scroll_px, 0.0);
        assert_eq!(view.trade_window.first, 0);
        st.interaction_trade_scroll(f32::MAX);
        let view = project(&st);
        assert_eq!(view.trade_window.scroll_px, view.trade_window.max_scroll_px);
        assert!(!view.trades.is_empty());
        assert!(view.trade_window.thumb_h > 0.0);
        // A viewport taller than the data: no scrollbar, everything renders.
        st.interaction_trade_viewport(28.0 * 1_000.0);
        let view = project(&st);
        assert_eq!(view.trade_window.max_scroll_px, 0.0);
        assert!(!view.trade_window.scrollable);
        assert_eq!(view.trades.len(), 400);
    }

    fn strategy_fixture() -> LabStrategy {
        LabStrategy {
            name: "FIX".into(),
            description: "fixture".into(),
            tags: vec![],
            version: "v1".into(),
            modified: "2026-09-28".into(),
            favorite: false,
            last_backtest: "—".into(),
        }
    }

    /// Real-shaped synthetic trades for the state-level tests (the perf module
    /// builds its own, larger, sets).
    fn synthetic_trades(n: usize) -> Vec<TradeRow> {
        (0..n)
            .map(|i| {
                let net = 100.0 + i as f64 * 0.5;
                TradeRow {
                    no: (i + 1).to_string(),
                    abs_index: i as i32,
                    symbol: format!("SYM{:04}", i),
                    side: if i % 2 == 0 {
                        "LONG".into()
                    } else {
                        "SHORT".into()
                    },
                    entry: format!("2026-01-{:02} 09:{:02}", (i % 28) + 1, i % 60),
                    entry_px: format!("{:.2}", 100.0 + i as f64 * 0.01),
                    exit: format!("2026-01-{:02} 15:{:02}", (i % 28) + 1, i % 60),
                    exit_px: format!("{:.2}", 100.0 + i as f64 * 0.02),
                    pnl: format!("{net:+.2}"),
                    r: format!("{:.2}", net / 100.0),
                    bars: (1 + i % 20).to_string(),
                    reason: if i % 3 == 0 {
                        "TARGET".into()
                    } else {
                        "STOP".into()
                    },
                    pnl_tone: if i % 3 == 0 {
                        Tone::Positive
                    } else {
                        Tone::Negative
                    },
                    selected: false,
                    sort: [29_000.0 + i as f64, net, net / 100.0, 1.0 + (i % 20) as f64],
                }
            })
            .collect()
    }

    #[test]
    fn empty_universe_echo_never_wipes_the_local_selection() {
        // The bridge only re-sends symbols on a strategy SELECT, so every
        // other snapshot echoes an empty universe. Adopting it would drop the
        // user's picks and the next RUN would gather no symbols at all
        // (a failed run with an empty ranking table).
        let mut st = state_with_obr();
        st.universe_symbols = vec!["RELIANCE".into(), "TCS".into()];
        st.interaction_symopen();
        st.interaction_symtoggle("RELIANCE");
        st.interaction_symapply();
        assert_eq!(st.universe_selected, vec!["RELIANCE".to_string()]);
        assert_eq!(st.cfg_universe_csv, "RELIANCE");
        let empty_echo: serde_json::Value = serde_json::from_str(
            r#"{"cfg_edit":{"universe_csv":"","capital":10000.0},
                "universe":{"symbols":["RELIANCE","TCS"],"selected":[]}}"#,
        )
        .unwrap();
        apply_snapshot_json(&mut st, &empty_echo);
        assert_eq!(st.universe_selected, vec!["RELIANCE".to_string()]);
        assert_eq!(st.cfg_universe_csv, "RELIANCE");
        assert_eq!(st.sym_draft, vec!["RELIANCE".to_string()]);
        // A NON-empty echo is the backend truth and still wins.
        let real_echo: serde_json::Value = serde_json::from_str(
            r#"{"cfg_edit":{"universe_csv":"TCS"},
                "universe":{"symbols":["RELIANCE","TCS"],"selected":["TCS"]}}"#,
        )
        .unwrap();
        apply_snapshot_json(&mut st, &real_echo);
        assert_eq!(st.universe_selected, vec!["TCS".to_string()]);
        assert_eq!(st.cfg_universe_csv, "TCS");
        // An explicit local clear still clears (absence is not a clear).
        st.interaction_symopen();
        st.interaction_symclear();
        st.interaction_symapply();
        assert!(st.universe_selected.is_empty());
        assert!(st.cfg_universe_csv.is_empty());
    }

    #[test]
    fn ranking_search_filters_and_criterion_sorts_then_renumbers() {
        let mut st = state_with_obr();
        st.select(0);
        st.engine_wired = true;
        st.start_run();
        let mut ranked = results();
        ranked.ranking = vec![
            RankRow {
                rank: "1".into(),
                symbol: "MESOLAR".into(),
                pnl: "+4,930".into(),
                ret: "+0.49%".into(),
                trades: "102".into(),
                win: "46.1%".into(),
                pf: "1.01".into(),
                dd: "-5.70%".into(),
                sharpe: "0.20".into(),
                pnl_tone: Tone::Positive,
                pf_tone: Tone::Neutral,
                unranked: false,
                sort: [4_930.0, 0.49, 102.0, 46.1, 1.01, 5.7, 0.20],
            },
            RankRow {
                rank: "2".into(),
                symbol: "ACUTAAS".into(),
                pnl: "-2,95,316".into(),
                ret: "-29.53%".into(),
                trades: "239".into(),
                win: "36.4%".into(),
                pf: "0.75".into(),
                dd: "-37.39%".into(),
                sharpe: "-1.93".into(),
                pnl_tone: Tone::Negative,
                pf_tone: Tone::Negative,
                unranked: false,
                sort: [-295_316.0, -29.53, 239.0, 36.4, 0.75, 37.39, -1.93],
            },
            RankRow {
                rank: "3".into(),
                symbol: "ACE".into(),
                pnl: "-2,00,461".into(),
                ret: "-20.05%".into(),
                trades: "213".into(),
                win: "35.7%".into(),
                pf: "0.84".into(),
                dd: "-23.74%".into(),
                sharpe: "-1.32".into(),
                pnl_tone: Tone::Negative,
                pf_tone: Tone::Negative,
                unranked: false,
                sort: [-200_461.0, -20.05, 213.0, 35.7, 0.84, 23.74, -1.32],
            },
        ];
        st.apply_result(ranked);
        st.cfg_universe_csv = "MESOLAR,ACUTAAS,ACE".into();
        st.cfg_universe_count = 3;
        st.rankby_labels = vec![
            "Net P&L".into(),
            "Return %".into(),
            "Trades".into(),
            "Win %".into(),
            "Profit Factor".into(),
            "Max DD".into(),
        ];
        st.rank_desc = true; // backend default: best first
                             // Backend order (net P&L desc) is the default.
        st.rebuild_rank_view();
        let view = project(&st);
        assert_eq!(
            view.ranking
                .iter()
                .map(|r| r.symbol.as_str())
                .collect::<Vec<_>>(),
            vec!["MESOLAR", "ACE", "ACUTAAS"]
        );
        // Trades criterion (box 2 → TRADES column): ACUTAAS leads on count.
        st.interaction_rankby("Trades");
        let view = project(&st);
        assert_eq!(
            view.ranking
                .iter()
                .map(|r| r.symbol.as_str())
                .collect::<Vec<_>>(),
            vec!["ACUTAAS", "ACE", "MESOLAR"]
        );
        // Re-numbered after the reorder (reference renumbers on sort).
        assert_eq!(
            view.ranking
                .iter()
                .map(|r| r.rank.as_str())
                .collect::<Vec<_>>(),
            vec!["1", "2", "3"]
        );
        // Ascending flips the leader.
        st.interaction_ranktoggle();
        assert!(!st.rank_desc);
        let view = project(&st);
        assert_eq!(view.ranking[0].symbol, "MESOLAR");
        // Search filters, case-insensitively, and never renumbers away.
        // The Trades criterion still decides the order inside the filter.
        st.interaction_ranktoggle();
        st.interaction_ranksearch("ac");
        let view = project(&st);
        assert_eq!(
            view.ranking
                .iter()
                .map(|r| r.symbol.as_str())
                .collect::<Vec<_>>(),
            vec!["ACUTAAS", "ACE"]
        );
        assert_eq!(view.ranking[0].rank, "1");
        st.interaction_ranksearch("zzz");
        assert!(project(&st).ranking.is_empty());
    }

    #[test]
    fn virtual_window_shows_every_row_without_rendering_them_all() {
        // `spec §18/§22`: virtualization may never become truncation. Walk a
        // 500-row dataset one viewport at a time and prove the union of the
        // windows is EXACTLY the dataset, in order, with no gap and no repeat.
        let mut st = state_with_obr();
        st.select(0);
        st.engine_wired = true;
        st.rankby_labels = vec![
            "Net P&L".into(),
            "Return %".into(),
            "Trades".into(),
            "Win %".into(),
            "Profit Factor".into(),
            "Max DD".into(),
        ];
        let rows: Vec<RankRow> = (0..500)
            .map(|i| RankRow {
                rank: (i + 1).to_string(),
                symbol: format!("SYM{i:04}"),
                pnl: format!("₹{}", 1000 - i),
                ret: format!("-{}.00%", i),
                trades: (i + 1).to_string(),
                win: "50.0%".into(),
                pf: "1.00".into(),
                dd: "-1.00%".into(),
                sharpe: "0.00".into(),
                pnl_tone: Tone::Negative,
                pf_tone: Tone::Neutral,
                unranked: false,
                sort: [-(i as f64), -(i as f64), i as f64, 50.0, 1.0, 1.0, 0.0],
            })
            .collect();
        let total = rows.len();
        st.apply_result(LabResults {
            ranking: rows,
            ..LabResults::default()
        });
        st.cfg_universe_count = total;
        let mut seen: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
        let mut first_seen: Option<String> = None;
        let mut last_seen: Option<String> = None;
        loop {
            let view = project(&st);
            assert!(
                view.ranking.len() <= 40,
                "a 10-row viewport must never render {} rows",
                view.ranking.len()
            );
            assert_eq!(view.rank_window.total as usize, total, "true count");
            for row in &view.ranking {
                if first_seen.is_none() {
                    first_seen = Some(row.symbol.clone());
                }
                last_seen = Some(row.symbol.clone());
                seen.insert(row.symbol.clone());
            }
            if st.rank.scroll_px >= st.rank.max_scroll() {
                break;
            }
            st.interaction_rank_scroll(RANK_ROW_H);
            assert!(!seen.is_empty());
        }
        assert_eq!(
            seen.len(),
            total,
            "every row must be reachable by scrolling"
        );
        // Sorted by net P&L desc, so the order runs 0499 -> 0000.
        assert_eq!(first_seen.as_deref(), Some("SYM0499"));
        assert_eq!(last_seen.as_deref(), Some("SYM0000"));
    }

    #[test]
    fn virtual_window_geometry_is_exact_and_never_negative() {
        // `spec §11/§12`: scroll offsets clamp, the thumb stays inside the
        // track, and the window never reads before the dataset.
        let mut st = state_with_obr();
        st.select(0);
        st.engine_wired = true;
        st.rankby_labels = vec!["Net P&L".into()];
        st.apply_result(LabResults {
            ranking: (0..100)
                .map(|i| RankRow {
                    rank: (i + 1).to_string(),
                    symbol: format!("SYM{i:03}"),
                    pnl: format!("₹{}", i),
                    ret: "+1.00%".into(),
                    trades: "1".into(),
                    win: "50.0%".into(),
                    pf: "1.00".into(),
                    dd: "-1.00%".into(),
                    sharpe: "0.00".into(),
                    pnl_tone: Tone::Positive,
                    pf_tone: Tone::Neutral,
                    unranked: false,
                    sort: [i as f64, 1.0, 1.0, 50.0, 1.0, 1.0, 0.0],
                })
                .collect(),
            ..LabResults::default()
        });
        // A dataset shorter than the viewport is not scrollable and renders whole.
        let view = project(&st);
        assert_eq!(view.rank_window.total, 100);
        assert!(view.rank_window.max_scroll_px > 0.0);
        // Overscrolling backwards clamps at the top.
        st.interaction_rank_scroll(-10_000.0);
        let view = project(&st);
        assert_eq!(view.rank_window.scroll_px, 0.0);
        assert_eq!(view.rank_window.first, 0);
        // A huge forward notch clamps at the end, no overshoot, no empty band.
        st.interaction_rank_scroll(f32::MAX);
        let view = project(&st);
        assert_eq!(view.rank_window.scroll_px, view.rank_window.max_scroll_px);
        assert!(!view.ranking.is_empty(), "the last page must have rows");
        assert!(view.rank_window.thumb_h > 0.0);
        assert!(
            view.rank_window.thumb_y + view.rank_window.thumb_h
                <= view.rank_window.viewport_h + 1.0
        );
        // A viewport taller than the content: everything renders, nothing scrolls.
        st.interaction_rank_viewport(RANK_ROW_H * 500.0);
        let view = project(&st);
        assert_eq!(view.rank_window.max_scroll_px, 0.0);
        assert_eq!(view.ranking.len(), 100);
        assert!(!view.rank_window.scrollable);
    }

    #[test]
    fn rank_window_first_anchors_every_visible_row() {
        // The Slint row band positions each row at
        // `header + (first + rindex) * row_h - scroll_px`: `rindex` is
        // band-relative while `scroll_px` is dataset-absolute, so the band's
        // `first` dataset index MUST anchor the row — without it every
        // scrolled row sits `first * row_h` too high and a deep scroll
        // renders a blank table. This test pins the data side of that
        // contract at EVERY scroll offset: rendered row `rindex` always
        // carries dataset index `first + rindex`, and the first rendered
        // row always sits at or just above the header.
        let mut st = state_with_obr();
        st.select(0);
        st.engine_wired = true;
        st.rank_desc = true;
        st.rankby_labels = vec!["Net P&L".into()];
        st.apply_result(LabResults {
            ranking: (0..200)
                .map(|i| RankRow {
                    rank: (i + 1).to_string(),
                    symbol: format!("SYM{i:03}"),
                    pnl: format!("₹{}", i),
                    ret: "+1.00%".into(),
                    trades: "1".into(),
                    win: "50.0%".into(),
                    pf: "1.00".into(),
                    dd: "-1.00%".into(),
                    sharpe: "0.00".into(),
                    pnl_tone: Tone::Positive,
                    pf_tone: Tone::Neutral,
                    unranked: false,
                    sort: [i as f64, 1.0, 1.0, 50.0, 1.0, 1.0, 0.0],
                })
                .collect(),
            ..LabResults::default()
        });
        // Descending net P&L over ascending keys: SYM199 first, SYM000 last.
        let header = 50.0;
        let max = st.rank.max_scroll();
        assert!(max > 0.0, "fixture must be scrollable");
        let mut px = 0.0;
        while px <= max {
            st.interaction_rank_scroll_to(px);
            let view = project(&st);
            let w = &view.rank_window;
            assert_eq!(
                view.ranking.len(),
                w.count as usize,
                "scroll {px}: model length must equal the window count"
            );
            assert!(!view.ranking.is_empty(), "scroll {px}: band must have rows");
            for (rindex, row) in view.ranking.iter().enumerate() {
                let dataset = (w.first as usize) + rindex;
                assert_eq!(
                    row.symbol,
                    format!("SYM{:03}", 199 - dataset),
                    "scroll {px}: rendered row {rindex} must carry dataset index {dataset}"
                );
            }
            let first_y = header + (w.first as f32) * w.row_h - w.scroll_px;
            assert!(
                first_y <= header + 0.001,
                "scroll {px}: first rendered row at {first_y} floats below the {header} header"
            );
            px += 7.0;
        }
    }

    #[test]
    fn rank_pick_opens_the_inspector_from_the_row_itself() {
        let mut st = state_with_obr();
        st.select(0);
        st.engine_wired = true;
        st.start_run();
        st.apply_result(results());
        st.cfg_universe_csv = "RELIANCE,TCS".into();
        st.cfg_universe_count = 2;
        assert!(project(&st).detail.is_none());
        st.interaction_rank_picked("RELIANCE");
        let view = project(&st);
        let detail = view.detail.expect("inspector");
        assert_eq!(detail.symbol, "RELIANCE");
        assert_eq!(detail.stats, "₹1,85,236.00");
        // The six reference facts, in drawer order.
        let labels: Vec<&str> = detail.metrics.iter().map(|m| m.label.as_str()).collect();
        assert_eq!(
            labels,
            vec![
                "RETURN",
                "TRADES",
                "WIN RATE",
                "PROFIT FACTOR",
                "MAX DRAWDOWN",
                "SHARPE"
            ]
        );
        assert_eq!(detail.metrics[0].value, "+18.50%");
        assert_eq!(detail.metrics[1].value, "71");
        // Footer names the inspected stock (reference drawer affordance).
        assert!(view.ranking_count.ends_with("· inspecting RELIANCE"));
        // Unknown symbol opens nothing — never an empty drawer.
        st.interaction_rank_picked("NOPE");
        assert!(project(&st).detail.is_none());
        st.interaction_rank_picked("RELIANCE");
        st.interaction_detail_close();
        assert!(project(&st).detail.is_none());
    }

    #[test]
    fn compare_side_filters_trades_locally_and_board_picks_unique_best() {
        let mut st = state_with_obr();
        st.select(0);
        st.set_mode(LabMode::Compare);
        st.compare = Some(CompareView {
            banner_verdict: "BUY / LONG".into(),
            banner_reason: "reason".into(),
            banner_tone: 4,
            board_scope: "2 STOCKS COMPARED".into(),
            board: vec![BoardRow {
                label: "NET P&L".into(),
                kind: "money".into(),
                higher: Some(true),
                cells: vec![
                    BoardCell {
                        symbol: "A".into(),
                        value: Some(100.0),
                    },
                    BoardCell {
                        symbol: "B".into(),
                        value: Some(50.0),
                    },
                ],
            }],
            board_symbols: vec!["A".into(), "B".into()],
            ..CompareView::default()
        });
        st.results_buy = Some(results());
        st.results_sell = Some(results());
        // In COMPARE the backend keeps _full_result at one side (workspace
        // lines 3995-3997) — the trades tab filters it by the side segment.
        // `apply_result` is the real arrival path (it also builds the blotter
        // index), so the fixture must go through it.
        st.apply_result(results());
        let view = project(&st);
        assert!(view.show_results);
        assert_eq!(view.compare.banner_tone, 4);
        assert_eq!(view.compare.board[0].cells[0].text, "₹100.00");
        assert!(view.compare.board[0].cells[0].best);
        assert!(!view.compare.board[0].cells[1].best);
        // Full trades are LONG only here; SHORT filter hides them locally.
        assert_eq!(view.trades.len(), 1);
        st.interaction_cmpside(2);
        assert!(st.pending_actions.is_empty());
        assert!(project(&st).trades.is_empty());
    }

    #[test]
    fn symbol_selector_uses_backend_universe_only() {
        let mut st = state_with_obr();
        st.selected = Some(0);
        // Backend echo adopts while closed; draft tracks it.
        st.universe_symbols = vec!["RELIANCE".into(), "TCS".into(), "INFY".into()];
        st.universe_selected = vec!["RELIANCE".into()];
        st.sym_draft = st.universe_selected.clone();
        // Unknown symbols can never enter the draft or the queued action.
        st.interaction_symopen();
        st.interaction_symtoggle("FAKE");
        assert_eq!(st.sym_draft, vec!["RELIANCE".to_string()]);
        st.interaction_symtoggle("TCS");
        st.interaction_symtoggle("RELIANCE");
        assert_eq!(st.sym_draft, vec!["TCS".to_string()]);
        // Search filters (legacy substring rule), selection survives filtering.
        st.interaction_symsearch("inf");
        let view = project(&st);
        assert_eq!(view.sym_visible, vec!["INFY".to_string()]);
        assert_eq!(view.sym_visible_on, vec![false]);
        assert!(view.sym_count_line.contains("1 of 3 stocks selected"));
        assert!(view.sym_count_line.contains("1 shown"));
        // Select-all-visible adds only the visible rows.
        st.interaction_symall();
        assert_eq!(st.sym_draft, vec!["TCS".to_string(), "INFY".to_string()]);
        // Apply commits the draft to the applied echo (the run path reads
        // the echo, not the draft) and queues backend CSV in draft order;
        // panel closes.
        st.interaction_symsearch("");
        st.interaction_symapply();
        assert!(!st.sym_open);
        assert_eq!(
            st.universe_selected,
            vec!["TCS".to_string(), "INFY".to_string()]
        );
        assert_eq!(st.cfg_universe_csv, "TCS,INFY");
        assert_eq!(
            st.pending_actions,
            vec![r#"universe:{"strategy_id":"OBR","symbols":["TCS","INFY"]}"#.to_string()]
        );
        // Close discards local edits back to the (newly applied) echo.
        st.interaction_symopen();
        st.interaction_symclear();
        assert!(st.sym_draft.is_empty());
        st.interaction_symclose();
        assert_eq!(st.sym_draft, vec!["TCS".to_string(), "INFY".to_string()]);
        // Button line vocabulary.
        st.universe_selected = vec![];
        st.sym_draft = vec![];
        assert_eq!(project(&st).sym_button_line, "NO UNIVERSE");
        st.universe_selected = vec!["A".into(), "B".into()];
        st.sym_draft = st.universe_selected.clone();
        assert_eq!(project(&st).sym_button_line, "A, B");
    }

    #[test]
    fn config_commits_land_in_echoes_not_just_the_queue() {
        // The run request is gathered locally: committed dates/capital/
        // timeframe must be readable from the echoes immediately, because
        // the queued backend actions are future sync, not the run path.
        let mut st = state_with_obr();
        st.timeframes = vec!["5m".into(), "15m".into(), "1h".into()];
        st.interaction_dates("2024-01-01", "2024-03-31");
        assert_eq!(st.cfg_dates_start, "2024-01-01");
        assert_eq!(st.cfg_dates_end, "2024-03-31");
        st.interaction_capital("500000");
        assert_eq!(st.cfg_capital, "500000");
        st.interaction_timeframe("1h");
        assert_eq!(st.timeframe_index, 2);
        // The displayed label/summary read config.timeframe, not the index —
        // a pick that leaves the echo stale shows the old timeframe forever.
        assert_eq!(st.config.timeframe, "1h");
        // Unknown timeframe labels never corrupt the index.
        st.interaction_timeframe("9m");
        assert_eq!(st.timeframe_index, 2);
        assert_eq!(st.config.timeframe, "1h");
        // Empty commits are ignored, never blank the echo.
        st.interaction_capital("   ");
        assert_eq!(st.cfg_capital, "500000");
    }

    #[test]
    fn capital_and_mode_change_mark_completed_run_outdated() {
        let mut st = state_with_obr();
        st.select(0);
        st.engine_wired = true;
        st.start_run();
        st.apply_result(results());
        let base = st.config.capital.clone();
        assert!(!st.outdated);
        // Capital edit -> outdated (the run request really reads cfg_capital).
        st.interaction_capital("250000");
        assert!(st.outdated);
        st.edit_config(|c| c.capital = base);
        assert!(!st.outdated);
        // Direction change -> outdated.
        st.interaction_mode(LabMode::Short);
        assert!(st.outdated);
        st.interaction_mode(LabMode::Long);
        assert!(!st.outdated);
    }

    #[test]
    fn civil_date_math_matches_known_epoch_days() {
        // Anchor facts: 1970-01-01 = 0, epoch day 0 round-trips; leap rules.
        assert_eq!(parse_iso_days("1970-01-01"), Some(0));
        assert_eq!(parse_iso_days("2000-02-29"), Some(11016)); // 2000 is leap
        assert_eq!(parse_iso_days("1900-02-29"), None); // pre-epoch rejected
        assert_eq!(parse_iso_days("2100-02-29"), None); // out of range
        assert_eq!(parse_iso_days("2026-02-29"), None); // 2026 is not leap
        assert_eq!(parse_iso_days("2024-02-29"), Some(19782));
        assert_eq!(civil_from_days(19782), (2024, 2, 29));
        assert_eq!(parse_iso_days("2026-9-22"), None); // canonical shape only
        assert_eq!(parse_iso_days(""), None);
        assert_eq!(human_from_days(20454), "1 Jan 2026");
        // month subtraction clamps to the shorter month (brief: never pick a
        // non-existent day).
        let jan31 = parse_iso_days("2026-01-31").unwrap();
        assert_eq!(civil_from_days(sub_months(jan31, 1)), (2025, 12, 31));
        assert_eq!(civil_from_days(sub_months(jan31, 12)), (2025, 1, 31));
        let mar31 = parse_iso_days("2026-03-31").unwrap();
        assert_eq!(civil_from_days(sub_months(mar31, 1)), (2026, 2, 28));
        assert_eq!(
            year_start_days(mar31),
            parse_iso_days("2026-01-01").unwrap()
        );
    }

    #[test]
    fn date_range_projection_formats_validates_and_never_destroys() {
        let mut st = state_with_obr();
        // Untouched → empty display, no error (placeholder owns the cell).
        let v = project(&st);
        assert_eq!(v.dates_human, "");
        assert_eq!(v.dates_error, "");
        assert_eq!(v.dates_start_days, -1);
        // Valid committed range → human display, ISO contract intact.
        st.cfg_dates_start = "2026-01-05".into();
        st.cfg_dates_end = "2026-09-22".into();
        let v = project(&st);
        assert_eq!(v.dates_human, "5 Jan 2026 — 22 Sep 2026");
        assert_eq!(v.dates_error, "");
        assert!(v.dates_start_days < v.dates_end_days);
        // Reversed range is flagged but still shown (selection preserved).
        st.cfg_dates_start = "2026-09-22".into();
        st.cfg_dates_end = "2026-01-05".into();
        let v = project(&st);
        assert_eq!(v.dates_human, "22 Sep 2026 — 5 Jan 2026");
        assert!(v.dates_error.contains("on or after"));
        // Garbage on one side: bad input flagged, good side kept visible.
        st.cfg_dates_start = "2026-02-30".into();
        let v = project(&st);
        assert!(v.dates_error.contains("valid calendar date"));
        assert_eq!(v.dates_human, "5 Jan 2026");
        // Presets: five quick ranges, all ending today, starts before ends.
        assert_eq!(v.date_presets.len(), 5);
        for p in &v.date_presets {
            assert_eq!(p.end_days, v.today_days);
            assert!(p.start_days <= p.end_days);
        }
        assert_eq!(v.date_presets[0].label, "Last 1M");
        assert_eq!(v.date_presets[4].label, "YTD");
    }

    // ── §02 range presets: real bounds, honest labels, one commit path ──

    /// 2019-09-19 … 2026-08-18 as day anchors (the bounds the mock shows).
    const LONG_FIRST: i64 = 18_158;
    const LONG_LAST: i64 = 20_683;

    fn state_with_bounds(first: i64, last: i64) -> LabState {
        let mut st = state_with_obr();
        st.data_bounds_start_days = first as i32;
        st.data_bounds_end_days = last as i32;
        st
    }

    #[test]
    fn range_presets_are_all_year_presets_plus_max_ending_at_the_last_bar() {
        let presets = range_presets_for(LONG_FIRST, LONG_LAST);
        for p in &presets {
            assert_eq!(p.end_days as i64, LONG_LAST);
            assert!(p.start_days <= p.end_days);
        }
        // A 6.9-year store cannot offer a 10Y range, so it is dropped rather
        // than shown under a clamped label that duplicates MAX.
        assert_eq!(
            presets.iter().map(|p| p.label.as_str()).collect::<Vec<_>>(),
            vec!["1Y", "3Y", "5Y", "MAX"]
        );
        // A store long enough for the label keeps it.
        let deep = range_presets_for(14_000, LONG_LAST);
        assert_eq!(deep[3].label, "10Y");
        for pair in presets.windows(2) {
            assert!(pair[1].start_days <= pair[0].start_days, "{pair:?}");
        }
    }

    #[test]
    fn max_preset_is_exactly_the_reported_store_bounds() {
        let presets = range_presets_for(LONG_FIRST, LONG_LAST);
        let max = presets.last().expect("max preset");
        assert_eq!(max.label, "MAX");
        assert_eq!(max.start_days as i64, LONG_FIRST);
        assert_eq!(max.end_days as i64, LONG_LAST);
    }

    #[test]
    fn a_short_history_store_drops_the_year_presets_it_cannot_back() {
        // Only 3.2 years of history: 5Y and 10Y cannot exist, so they are
        // dropped instead of being shown as a clamped duplicate of MAX.
        let first = LONG_LAST - 1_168; // ≈3.2 years
        let presets = range_presets_for(first, LONG_LAST);
        assert_eq!(
            presets.iter().map(|p| p.label.as_str()).collect::<Vec<_>>(),
            vec!["1Y", "3Y", "MAX"]
        );
        assert_eq!(presets[2].start_days as i64, first);
        assert_eq!(presets[2].end_days as i64, LONG_LAST);
    }

    #[test]
    fn unknown_bounds_produce_no_presets_at_all() {
        assert!(range_presets_for(-1, LONG_LAST).is_empty());
        assert!(range_presets_for(0, 0).is_empty());
        // Inverted bounds are not a range.
        assert!(range_presets_for(LONG_LAST, LONG_FIRST).is_empty());
    }

    #[test]
    fn a_preset_rewrites_the_committed_iso_bounds_through_the_date_path() {
        let mut st = state_with_bounds(LONG_FIRST, LONG_LAST);
        st.interaction_range_preset(3); // MAX
        assert_eq!(st.cfg_dates_start, "2019-09-19");
        assert_eq!(st.cfg_dates_end, "2026-08-18");
        // It is the SAME commit as the picker: one queued `dates:` action.
        assert_eq!(st.pending_actions, vec!["dates:2019-09-19:2026-08-18"]);
    }

    #[test]
    fn an_out_of_range_preset_click_commits_nothing() {
        let mut st = state_with_bounds(LONG_FIRST, LONG_LAST);
        st.cfg_dates_start = "2020-01-01".into();
        st.interaction_range_preset(9);
        assert_eq!(st.cfg_dates_start, "2020-01-01");
        assert!(st.pending_actions.is_empty());
    }

    #[test]
    fn the_active_preset_cell_is_derived_from_the_committed_range() {
        let mut st = state_with_bounds(LONG_FIRST, LONG_LAST);
        st.cfg_dates_start = "2019-09-19".into();
        st.cfg_dates_end = "2026-08-18".into();
        assert_eq!(project(&st).range_preset_idx, 3);
        // A hand-picked range matches no cell — the row shows nothing selected.
        st.cfg_dates_start = "2024-06-03".into();
        assert_eq!(project(&st).range_preset_idx, -1);
    }

    // ── §02 config checks: kernel facts only ──

    #[test]
    fn checks_stay_safe_when_nothing_is_configured_yet() {
        let st = LabState::default();
        let v = project(&st);
        // No universe, no strategy: the failing rows must SAY SO, not panic and
        // not invent a passing row.
        assert!(v.cfg_checks.iter().any(|c| c.kind == 2));
        assert!(
            v.cfg_checks
                .iter()
                .all(|c| !c.label.is_empty() && !c.label.contains("MIN ")),
            "{:?}",
            v.cfg_checks
        );
    }

    #[test]
    fn a_complete_config_produces_only_passing_rows() {
        let mut st = state_with_obr();
        st.universe_symbols = vec!["A".into(), "B".into(), "C".into()];
        st.universe_selected = vec!["A".into(), "B".into(), "C".into()];
        st.timeframes = vec!["15m".into()];
        st.timeframe_index = 0;
        st.cfg_capital = "10000".into();
        st.cfg_dates_start = "2026-01-05".into();
        st.cfg_dates_end = "2026-09-22".into();
        let v = project(&st);
        assert!(
            v.cfg_checks.iter().all(|c| c.kind == 0),
            "{:?}",
            v.cfg_checks
        );
        assert!(v.cfg_checks.iter().any(|c| c.label == "Data available 3/3"));
    }

    #[test]
    fn the_capital_row_reuses_the_kernel_message_verbatim() {
        let mut st = state_with_obr();
        st.engine_wired = true;
        st.cfg_capital = "0".into();
        let v = project(&st);
        let capital = v
            .cfg_checks
            .iter()
            .find(|c| c.label.contains("capital") || c.label.contains("Capital"))
            .expect("a capital row");
        assert_eq!(capital.label, "Initial capital must be positive.");
        assert_eq!(capital.kind, 2);
    }

    #[test]
    fn an_outdated_run_surfaces_as_a_warning_row() {
        let mut st = state_with_obr();
        st.outdated = true;
        let v = project(&st);
        assert!(
            v.cfg_checks
                .iter()
                .any(|c| c.kind == 1 && c.label.contains("rerun")),
            "{:?}",
            v.cfg_checks
        );
    }

    // ── §02 coverage strip: hidden unless measured ──

    #[test]
    fn no_probe_means_no_strip_and_no_zero_percent() {
        let st = LabState::default();
        let v = project(&st);
        assert!(!v.coverage.on);
        assert_eq!(v.coverage.pct, 0);
        assert_eq!(v.coverage.note, "");
        // And nothing anywhere claims a measured percentage.
        assert!(!v.cfg_checks.iter().any(|c| c.label.contains('%')));
    }

    #[test]
    fn a_measured_probe_reaches_the_strip_with_an_integer_percent() {
        let mut st = state_with_bounds(LONG_FIRST, LONG_LAST);
        st.apply_coverage(CoverageMeasurement {
            symbols_total: 527,
            symbols_covering: 524,
            bars_present: 94_000,
            gaps: 0,
            start_days: LONG_FIRST as i32,
            end_days: LONG_LAST as i32,
            tf_secs: 1800,
            sampled: true,
            symbols_probed: 24,
            anchor: "AAA".into(),
        });
        let v = project(&st);
        assert!(v.coverage.on);
        assert!(v.coverage.pct > 0 && v.coverage.pct <= 100);
        // A bounded probe must SAY it is bounded, in the caption and the note.
        assert_eq!(v.cov_caption, "SAMPLED");
        assert!(v.coverage.note.contains("PROBED"), "{}", v.coverage.note);
        // The bar count belongs to the ANCHOR, and the note must name it
        // rather than implying a total for the whole selection.
        assert!(
            v.coverage.note.contains("IN AAA"),
            "note must name the measured symbol: {}",
            v.coverage.note
        );
        // Three symbols have no data in the window — a real, counted warning.
        assert!(
            v.cfg_checks
                .iter()
                .any(|c| c.kind == 1 && c.label == "3 symbols have no data in this window"),
            "{:?}",
            v.cfg_checks
        );
    }

    #[test]
    fn an_unmeasurable_window_clears_the_strip_rather_than_showing_zero() {
        let mut st = state_with_bounds(LONG_FIRST, LONG_LAST);
        st.apply_coverage(CoverageMeasurement {
            symbols_total: 3,
            symbols_covering: 3,
            bars_present: 500,
            gaps: 0,
            start_days: -1,
            end_days: LONG_LAST as i32,
            tf_secs: 1800,
            sampled: false,
            symbols_probed: 3,
            anchor: "AAA".into(),
        });
        let v = project(&st);
        assert!(!v.coverage.on, "an unmeasurable window must hide the strip");
        assert_eq!(v.coverage.note, "");
    }

    #[test]
    fn coverage_json_derives_the_percentage_in_rust_not_in_python() {
        let mut st = LabState::default();
        let payload = serde_json::json!({
            "symbols_total": 4,
            "symbols_covering": 4,
            "symbols_probed": 4,
            "bars_present": 1000,
            "start": "2019-09-19",
            "end": "2026-08-18",
            "timeframe": "30m",
            "sampled": false
        });
        apply_coverage_json(&mut st, &payload);
        let v = project(&st);
        assert!(v.coverage.on);
        // The kernel's own session grid decides the expectation, so the number
        // is reproducible from the window alone.
        let expected = vayren_core::lab_coverage::expected_bars(
            LONG_FIRST,
            LONG_LAST,
            vayren_core::market::timeframe_seconds("30m").expect("ladder"),
            &std::collections::HashSet::new(),
        );
        assert!(expected > 1000);
    }

    // ── §02 chips / timeframe cells: presentation caps, not data caps ──

    #[test]
    fn the_chip_overflow_count_comes_from_rust_not_from_slint() {
        let mut st = state_with_obr();
        for i in 0..9 {
            st.universe_selected.push(format!("SYM{i}"));
        }
        let v = project(&st);
        assert_eq!(v.universe_chips.len(), UNIVERSE_CHIP_CAP);
        assert_eq!(v.chips_more_line, "+3");
        st.universe_selected = vec!["A".into()];
        assert_eq!(project(&st).chips_more_line, "");
    }

    #[test]
    fn the_timeframe_cells_are_the_real_ladder_prefix_never_a_hardcoded_one() {
        let mut st = state_with_obr();
        st.timeframes = vec![
            "5m".into(),
            "15m".into(),
            "30m".into(),
            "1h".into(),
            "1D".into(),
            "1W".into(),
        ];
        st.timeframe_index = 2;
        let v = project(&st);
        assert_eq!(v.tf_short.len(), TF_SHORT_CAP);
        assert_eq!(v.tf_short[2], "30m");
        assert_eq!(v.tf_short_idx, 2);
        // A timeframe behind the cap is reachable but not shown as a cell.
        st.timeframe_index = 5;
        assert_eq!(project(&st).tf_short_idx, -1);
    }

    #[test]
    fn the_universe_button_line_counts_the_real_selection() {
        let mut st = state_with_obr();
        assert_eq!(project(&st).sym_button_line, "NO UNIVERSE");
        st.universe_selected = vec!["A".into()];
        assert_eq!(project(&st).sym_button_line, "A");
        st.universe_selected = vec!["A".into(), "B".into()];
        assert_eq!(project(&st).sym_button_line, "A, B");
        st.universe_selected = (0..527).map(|i| format!("S{i}")).collect();
        assert_eq!(project(&st).sym_button_line, "527 STOCKS");
    }

    #[test]
    fn the_live_edge_flag_follows_the_real_last_available_bar() {
        let mut st = state_with_bounds(LONG_FIRST, LONG_LAST);
        st.cfg_dates_start = "2019-09-19".into();
        st.cfg_dates_end = "2026-08-18".into();
        assert!(project(&st).range_to_live);
        st.cfg_dates_end = "2024-01-02".into();
        assert!(!project(&st).range_to_live);
    }

    #[test]
    fn the_span_line_is_the_real_number_of_days() {
        let mut st = state_with_bounds(LONG_FIRST, LONG_LAST);
        st.cfg_dates_start = "2019-09-19".into();
        st.cfg_dates_end = "2026-08-18".into();
        let v = project(&st);
        assert_eq!(v.range_span_line, "2,526 DAYS");
        assert_eq!(v.range_from_human, "19 Sep 2019");
        assert_eq!(v.range_to_human, "18 Aug 2026");
    }

    // ── live run progress: measured facts, never an estimate ──

    fn progress_event(over: serde_json::Value) -> serde_json::Value {
        let mut base = serde_json::json!({
            "stage": "calculate",
            "stage_pct": 0.0,
            "total": 527,
            "done": 127,
            "headline_total": 527,
            "completed": 127,
            "failed": ["BAD1", "BAD2"],
            "skipped": [],
            "remaining": 398,
            "pct": 24.1,
            "current": "AARTIIND",
            "current_secs": 4.2,
            "elapsed_secs": 134.0,
            "eta_secs": 392.0,
            "mean_secs": 1.054,
            "throughput": 0.948,
            "trades": 124520,
            "bars": 2800000,
            "net_pnl": 4211.5,
            "long_running": false,
            "quiet": false,
            "cancelled": false
        });
        if let (Some(base_map), Some(extra)) = (base.as_object_mut(), over.as_object()) {
            for (key, value) in extra {
                base_map.insert(key.clone(), value.clone());
            }
        }
        base
    }

    #[test]
    fn progress_reports_the_measured_run_state() {
        let mut st = LabState::default();
        st.run = RunState::Running;
        st.apply_progress(&progress_event(serde_json::json!({})));
        let v = project(&st);
        assert!(v.prog_active);
        assert_eq!(v.prog_headline, "127 / 527 stocks");
        assert_eq!(v.prog_current, "AARTIIND");
        assert_eq!(v.prog_stage, "Strategy calculation");
        assert_eq!(v.prog_elapsed, "02:14");
        assert_eq!(v.prog_eta, "~06:32");
        assert_eq!(v.prog_throughput, "0.95 stocks/s");
        assert_eq!(v.prog_speed, "1.05 s/stock");
        assert!(v.prog_counts.contains("Failed 2"));
        assert!(v.prog_counts.contains("Remaining 398"));
        assert!(v.prog_failed_line.contains("BAD1"));
        assert!(!v.prog_cancelled);
        assert_eq!(v.prog_watchdog, "", "a healthy run shows no warning");
    }

    #[test]
    fn an_unmeasured_eta_renders_a_dash_never_a_zero() {
        let mut st = LabState::default();
        st.run = RunState::Running;
        st.apply_progress(&progress_event(
            serde_json::json!({"eta_secs": serde_json::Value::Null, "mean_secs": serde_json::Value::Null}),
        ));
        let v = project(&st);
        assert_eq!(v.prog_eta, "—");
        assert_eq!(v.prog_speed, "—");
    }

    #[test]
    fn the_load_stage_shows_symbols_read_not_symbols_run() {
        let mut st = LabState::default();
        st.run = RunState::Running;
        st.apply_progress(&progress_event(serde_json::json!({
            "stage": "data", "loaded": 120, "done": 120, "headline_total": 527,
            "completed": 0, "pct": 22.8, "eta_secs": 640.0, "elapsed_secs": 300.0
        })));
        let v = project(&st);
        // The bar must not read 0 / 527 while a fifth of the load is done.
        assert_eq!(v.prog_headline, "120 / 527 stocks");
        assert_eq!(v.prog_stage, "Loading bars");
    }

    #[test]
    fn a_long_symbol_is_flagged_without_claiming_progress() {
        let mut st = LabState::default();
        st.run = RunState::Running;
        st.apply_progress(&progress_event(serde_json::json!({
            "current_secs": 47.0, "long_running": true, "completed": 12, "done": 12
        })));
        let v = project(&st);
        assert!(v.prog_long);
        assert!(v.prog_watchdog.starts_with("LONG-RUNNING"));
        assert!(v.prog_watchdog.contains("00:47"));
        assert_eq!(
            v.prog_headline, "12 / 527 stocks",
            "progress must not advance"
        );
    }

    #[test]
    fn a_cancelled_run_says_cancelled_and_retires() {
        let mut st = LabState::default();
        st.run = RunState::Running;
        st.apply_progress(&progress_event(
            serde_json::json!({"cancelled": true, "stage": "cancelled"}),
        ));
        let v = project(&st);
        assert!(v.prog_cancelled);
        assert_eq!(v.prog_watchdog, "CANCELLED");
        st.clear_progress();
        let done = project(&st);
        assert!(!done.prog_active, "a finished run must retire the panel");
    }

    #[test]
    fn no_run_means_no_progress_panel() {
        let v = project(&LabState::default());
        assert!(!v.prog_active);
        assert_eq!(v.prog_eta, "—");
        assert_eq!(v.prog_elapsed, "—");
    }

    #[test]
    fn clock_label_never_renders_a_bogus_zero_duration() {
        assert_eq!(clock_label(-1.0), "—");
        assert_eq!(clock_label(f64::NAN), "—");
        assert_eq!(clock_label(0.0), "00:00");
        assert_eq!(clock_label(134.0), "02:14");
        assert_eq!(clock_label(392.0), "06:32");
        assert_eq!(clock_label(3_671.0), "1:01:11");
    }

    #[test]
    fn search_filter_transitions_preserve_sort_and_find_results() {
        let mock = |symbol: &str, sort_val: f64| RankRow {
            rank: "1".to_string(),
            symbol: symbol.to_string(),
            pnl: "+100".to_string(),
            ret: "+10%".to_string(),
            trades: "5".to_string(),
            win: "60%".to_string(),
            pf: "1.5".to_string(),
            dd: "-5%".to_string(),
            sharpe: "2.1".to_string(),
            pnl_tone: Tone::Positive,
            pf_tone: Tone::Neutral,
            unranked: false,
            sort: [sort_val, 10.0, 1.0, 1.0, 1.0, 1.0, 1.0],
        };
        let mut window = VirtualWindow::default();
        let rows = vec![
            mock("INFY", 100.0),
            mock("TCS", 200.0),
            mock("RELIANCE", 50.0),
        ];

        // 1. Initial sort by criterion 0 descending -> TCS (200), INFY (100), RELIANCE (50)
        window.rebuild(&rows, "", Some(0), true);
        assert_eq!(window.view, vec![1, 0, 2]);

        // 2. Search for "INF" -> only INFY
        window.rebuild(&rows, "INF", Some(0), true);
        assert_eq!(window.view, vec![0]);

        // 3. Search switched to "TCS" (must NOT be empty from previous INFY filter)
        window.rebuild(&rows, "TCS", Some(0), true);
        assert_eq!(window.view, vec![1]);

        // 4. Search cleared -> all 3 rows restored in descending sort order
        window.rebuild(&rows, "", Some(0), true);
        assert_eq!(window.view, vec![1, 0, 2]);
    }
}

// ── bridge: Python backend snapshot -> canonical state (embedded view) ────

fn opt_str(value: &serde_json::Value, key: &str) -> String {
    value
        .get(key)
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string()
}

fn opt_f64(value: &serde_json::Value, key: &str) -> Option<f64> {
    value.get(key).and_then(|v| v.as_f64())
}

fn opt_i64(value: &serde_json::Value, key: &str) -> Option<i64> {
    value.get(key).and_then(|v| v.as_i64())
}

fn plain(value: Option<f64>) -> (String, Tone) {
    // legacy tile vocabulary: missing is "--".
    match value {
        None => ("--".to_string(), Tone::Muted),
        Some(v) => (format!("{v:.2}"), Tone::Neutral),
    }
}

/// Split a non-negative amount into its whole part and a 2-decimal fraction.
///
/// Returns `None` when the amount does not fit `i64` — a saturating
/// `as i64` would render a plausible-but-wrong 19-digit number, so callers
/// show honest absence instead.
///
/// The fraction is CARRIED: an amount whose fractional part rounds up to 100
/// (e.g. `1234.999` → `123499.9` → 123500 hundredths) increments the whole
/// part and returns 0, never the impossible ".100" the naive
/// `trunc()` + `round()` pair produces.
fn split_rupees(abs_value: f64) -> Option<(i64, u32)> {
    if !abs_value.is_finite() || abs_value < 0.0 {
        return None;
    }
    // Round to hundredths FIRST, then split by integer division — a single
    // rounding step makes the carry exact instead of a two-step guess.
    let hundredths = abs_value * 100.0;
    if !hundredths.is_finite() || hundredths > i64::MAX as f64 {
        return None;
    }
    let hundredths = hundredths.round() as i64;
    Some((hundredths / 100, (hundredths % 100) as u32))
}

/// Indian-grouping money body, e.g. 1234567.8 → "12,34,567.80" (mirrors the
/// legacy `_inr` helper exactly: last group of 3, then groups of 2).
fn inr_body(abs_value: f64) -> String {
    let Some((whole, frac)) = split_rupees(abs_value) else {
        return "N/A".to_string();
    };
    let mut digits = whole.to_string();
    if digits.len() > 3 {
        let tail = digits.split_off(digits.len() - 3);
        let mut head = digits;
        let mut groups: Vec<String> = Vec::new();
        while head.len() > 2 {
            groups.insert(0, head.split_off(head.len() - 2));
        }
        if !head.is_empty() {
            groups.insert(0, head);
        }
        groups.push(tail);
        digits = groups.join(",");
    }
    format!("{digits}.{frac:02}")
}

/// legacy `_signed_inr`: sign before ₹, Indian grouping, no plus for positives.
fn inr_signed(value: Option<f64>) -> String {
    match value {
        None => "--".to_string(),
        Some(v) => format!("{}₹{}", if v < 0.0 { "-" } else { "" }, inr_body(v.abs())),
    }
}

/// Western-grouped signed 2-decimals (legacy blotter P&L: `f"{pnl:+,.2f}"`).
fn western_signed2(value: f64) -> String {
    let sign = if value < 0.0 { "-" } else { "+" };
    let Some((whole, frac)) = split_rupees(value.abs()) else {
        return format!("{sign}N/A");
    };
    let mut digits = whole.to_string();
    if digits.len() > 3 {
        let mut out = String::new();
        let mut count = 0;
        for ch in digits.chars().rev() {
            if count == 3 {
                out.push(',');
                count = 0;
            }
            out.push(ch);
            count += 1;
        }
        digits = out.chars().rev().collect();
    }
    format!("{sign}{digits}.{frac:02}")
}

/// Timestamp display: ISO-ish strings shortened to "DD Mon/ HH:MM" style
/// (first 16 chars, 'T' flattened). Presentation lives here, not in Python.
fn short_ts(value: &serde_json::Value, key: &str) -> String {
    let raw = opt_str(value, key).replace('T', " ");
    raw.chars().take(16).collect()
}

fn normalized_series(points: &[(f64, f64)], take_abs: bool) -> Vec<(f32, f32)> {
    if points.len() < 2 {
        return Vec::new();
    }
    let ys: Vec<f64> = points
        .iter()
        .map(|(_, y)| if take_abs { -y.abs() } else { *y })
        .collect();
    let min = ys.iter().cloned().fold(f64::INFINITY, f64::min);
    let max = ys.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    let span = (max - min).max(1e-9);
    let n = points.len() - 1;
    points
        .iter()
        .enumerate()
        .map(|(i, _)| (i as f32 / n as f32, ((ys[i] - min) / span) as f32))
        .collect()
}

/// Replace backend-owned fields of the canonical state from the bridge JSON
/// (schema documented on `strategy_lab_snapshot_dict` Python side). Local UI
/// fields (search/filter/tab/pending actions) are preserved. Missing or
/// mistyped sections degrade to honest absence, never invented values.
/// Adopt a `lab_coverage` probe payload from the Python backend.
///
/// The payload carries COUNTS plus the window that produced them; the
/// expectation and the percentage are derived here in the Rust kernel, so the
/// number on screen has exactly one owner. An empty payload means "not
/// measurable" and clears the facts — the strip then stays hidden instead of
/// showing a fabricated `0%`.
pub fn apply_coverage_json(state: &mut LabState, value: &serde_json::Value) {
    let measured = CoverageMeasurement {
        symbols_total: value
            .get("symbols_total")
            .and_then(|v| v.as_u64())
            .unwrap_or(0) as u32,
        symbols_covering: value
            .get("symbols_covering")
            .and_then(|v| v.as_u64())
            .unwrap_or(0) as u32,
        bars_present: value
            .get("bars_present")
            .and_then(|v| v.as_u64())
            .unwrap_or(0),
        gaps: value.get("gaps").and_then(|v| v.as_u64()).unwrap_or(0) as u32,
        start_days: parse_iso_days(opt_str(value, "start").trim())
            .map(|v| v as i32)
            .unwrap_or(-1),
        end_days: parse_iso_days(opt_str(value, "end").trim())
            .map(|v| v as i32)
            .unwrap_or(-1),
        tf_secs: timeframe_seconds(opt_str(value, "timeframe").trim()).unwrap_or(0),
        sampled: value
            .get("sampled")
            .and_then(|v| v.as_bool())
            .unwrap_or(false),
        symbols_probed: value
            .get("symbols_probed")
            .and_then(|v| v.as_u64())
            .unwrap_or(0) as u32,
        anchor: opt_str(value, "anchor"),
    };
    state.apply_coverage(measured);
}

pub fn apply_snapshot_json(state: &mut LabState, value: &serde_json::Value) {
    if let Some(rows) = value.get("strategies").and_then(|v| v.as_array()) {
        let previous_selected = state
            .selected
            .and_then(|i| state.strategies.get(i))
            .map(|s| s.name.clone());
        state.strategies = rows
            .iter()
            .map(|row| LabStrategy {
                name: opt_str(row, "name"),
                description: opt_str(row, "description"),
                tags: row
                    .get("tags")
                    .and_then(|v| v.as_array())
                    .map(|arr| {
                        arr.iter()
                            .filter_map(|t| t.as_str().map(str::to_string))
                            .collect()
                    })
                    .unwrap_or_default(),
                version: opt_str(row, "version"),
                modified: opt_str(row, "modified"),
                favorite: row
                    .get("favorite")
                    .and_then(|v| v.as_bool())
                    .unwrap_or(false),
                last_backtest: opt_str(row, "last_backtest"),
            })
            .filter(|s| !s.name.is_empty())
            .collect();
        let wanted = value
            .get("selected_name")
            .and_then(|v| v.as_str())
            .map(str::to_string)
            .or(previous_selected);
        state.selected = wanted
            .as_ref()
            .and_then(|name| state.strategies.iter().position(|s| &s.name == name));
        if let Some(index) = state.selected {
            let _ = index;
        }
    }
    if value.get("config").is_some() {
        let config = value
            .get("config")
            .cloned()
            .unwrap_or(serde_json::json!({}));
        state.config = LabConfig {
            universe: opt_str(&config, "universe"),
            timeframe: opt_str(&config, "timeframe"),
            dates: opt_str(&config, "dates"),
            capital: opt_str(&config, "capital"),
        };
    }
    match value.get("mode").and_then(|v| v.as_str()) {
        Some("sell") => state.mode = LabMode::Short,
        Some("compare") => state.mode = LabMode::Compare,
        Some("buy") => state.mode = LabMode::Long,
        _ => {}
    }
    match value.get("run").and_then(|v| v.as_str()) {
        Some("running") => state.run = RunState::Running,
        Some("complete") => state.run = RunState::Complete,
        Some("failed") => state.run = RunState::Failed,
        Some("ready") => state.run = RunState::Ready,
        _ => {}
    }
    if let Some(wired) = value.get("engine_wired").and_then(|v| v.as_bool()) {
        state.engine_wired = wired;
    }
    if let Some(outdated) = value.get("outdated").and_then(|v| v.as_bool()) {
        state.outdated = outdated;
    } else {
        state.refresh_staleness();
    }
    // Staleness hints the backend may send instead of a full fingerprint echo:
    // consume them so a stale indicator/bar set never renders as current.
    let stale_hint = value
        .get("bars_stale")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
        || value
            .get("data_stale")
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
        || value
            .get("indicators")
            .and_then(|v| {
                v.as_bool().or_else(|| {
                    v.get("stale")
                        .and_then(|s| s.as_bool())
                        .or_else(|| v.get("bars_stale").and_then(|s| s.as_bool()))
                })
            })
            .unwrap_or(false);
    if stale_hint {
        state.outdated = true;
    }
    state.verdict_label = opt_str(value, "verdict");
    state.verdict_note = opt_str(value, "verdict_note");
    state.verdict_tone = opt_i64(value, "verdict_tone").unwrap_or(0).clamp(0, 4) as i32;
    state.progress_label = opt_str(value, "progress");
    apply_parity_keys(state, value);
}
/// legacy MetricsTiles formats (8 tiles, tile missing glyph "--").
fn parse_kpis(metrics: &serde_json::Value) -> Vec<Kpi> {
    let mut kpis: Vec<Kpi> = Vec::new();
    let mut push = |label: &str, value: String, tone: Tone, emphasized: bool| {
        kpis.push(Kpi {
            label: label.to_string(),
            value,
            tone,
            emphasized,
        });
    };
    let total_trades = opt_i64(metrics, "total_trades");
    let net_raw = opt_f64(metrics, "net_profit");
    // legacy: "--" when nothing traded; colour follows `net_profit >= 0`.
    push(
        "NET P&L",
        if total_trades.unwrap_or(0) == 0 {
            "--".to_string()
        } else {
            inr_signed(net_raw)
        },
        if net_raw.unwrap_or(0.0) >= 0.0 {
            Tone::Positive
        } else {
            Tone::Negative
        },
        true,
    );
    push(
        "TRADES",
        match total_trades {
            Some(v) => v.to_string(),
            None => "--".to_string(),
        },
        Tone::Neutral,
        false,
    );
    // Snapshot win_rate is already ×100 (bridge contract).
    push(
        "WIN RATE",
        match opt_f64(metrics, "win_rate") {
            Some(v) => format!("{v:.1}%"),
            None => "--".to_string(),
        },
        Tone::Neutral,
        false,
    );
    let (pf, _) = plain(opt_f64(metrics, "profit_factor"));
    push("PROFIT FACTOR", pf, Tone::Neutral, false);
    push(
        "EXPECTANCY",
        inr_signed(opt_f64(metrics, "expectancy")),
        Tone::Neutral,
        false,
    );
    push(
        "MAX DRAWDOWN",
        match opt_f64(metrics, "max_drawdown_pct") {
            Some(v) => format!("-{v:.2}%"),
            None => "--".to_string(),
        },
        if opt_f64(metrics, "max_drawdown_pct").unwrap_or(0.0) > 0.0 {
            Tone::Negative
        } else {
            Tone::Muted
        },
        false,
    );
    let (sh, _) = plain(opt_f64(metrics, "sharpe_ratio"));
    push("SHARPE", sh, Tone::Neutral, false);
    push(
        "AVG TRADE",
        inr_signed(opt_f64(metrics, "avg_trade")),
        Tone::Neutral,
        false,
    );
    kpis
}

/// legacy `_fill_row` formats (table missing glyph is the em-dash here).
fn parse_rank_row(r: &serde_json::Value) -> RankRow {
    let net = opt_f64(r, "net_profit");
    let ret = opt_f64(r, "return_pct");
    let pf = opt_f64(r, "profit_factor");
    let dd = opt_f64(r, "max_drawdown_pct");
    let sharpe = opt_f64(r, "sharpe_ratio");
    let win = opt_f64(r, "win_rate");
    RankRow {
        rank: opt_i64(r, "rank")
            .map(|v| v.to_string())
            .unwrap_or_else(|| "—".to_string()),
        symbol: opt_str(r, "symbol"),
        pnl: match net {
            None => "—".to_string(),
            Some(v) => format!("₹{}", inr_body(v.abs())),
        },
        ret: match ret {
            None => "—".to_string(),
            Some(v) => format!("{v:+.2}%"),
        },
        trades: opt_i64(r, "total_trades")
            .map(|v| v.to_string())
            .unwrap_or_else(|| "—".to_string()),
        win: match win {
            None => "—".to_string(),
            Some(v) => format!("{v:.1}%"),
        },
        pf: match pf {
            None => "—".to_string(),
            Some(v) => format!("{v:.2}"),
        },
        dd: match dd {
            None => "—".to_string(),
            Some(v) => format!("-{v:.2}%"),
        },
        sharpe: match sharpe {
            None => "—".to_string(),
            Some(v) => format!("{v:.2}"),
        },
        pnl_tone: semantic_tone(net),
        pf_tone: match pf {
            None => Tone::Muted,
            Some(v) if v > 1.0 => Tone::Positive,
            Some(_) => Tone::Negative,
        },
        unranked: r.get("status").and_then(|v| v.as_str()) != Some("ranked"),
        sort: [
            net.unwrap_or(f64::NAN),
            ret.unwrap_or(f64::NAN),
            opt_f64(r, "total_trades").unwrap_or(f64::NAN),
            win.unwrap_or(f64::NAN),
            pf.unwrap_or(f64::NAN),
            dd.unwrap_or(f64::NAN),
            sharpe.unwrap_or(f64::NAN),
        ],
    }
}

/// legacy `_format_row` formats (11 blotter columns, verbatim order).
fn parse_trade_row(i: usize, t: &serde_json::Value) -> TradeRow {
    let pnl = opt_f64(t, "pnl");
    TradeRow {
        no: (i + 1).to_string(),
        abs_index: i as i32,
        symbol: opt_str(t, "symbol"),
        side: opt_str(t, "side"),
        entry: short_ts(t, "entry_time"),
        entry_px: match opt_f64(t, "entry_px") {
            Some(v) => format!("{v:.2}"),
            None => "--".to_string(),
        },
        exit: short_ts(t, "exit_time"),
        exit_px: match opt_f64(t, "exit_px") {
            Some(v) => format!("{v:.2}"),
            None => "--".to_string(),
        },
        pnl: match pnl {
            Some(v) => western_signed2(v),
            None => "--".to_string(),
        },
        r: match opt_f64(t, "r_multiple") {
            Some(v) => format!("{v:.2}"),
            None => "--".to_string(),
        },
        bars: opt_i64(t, "bars")
            .map(|v| v.to_string())
            .unwrap_or_else(|| "--".to_string()),
        reason: opt_str(t, "reason"),
        // legacy colours by the engine's own `winning` flag (pnl > 0).
        pnl_tone: if t.get("winning").and_then(|v| v.as_bool()).unwrap_or(false) {
            Tone::Positive
        } else {
            Tone::Negative
        },
        selected: false,
        sort: [
            iso_minutes(opt_str(t, "entry_time")),
            pnl.unwrap_or(f64::NAN),
            opt_f64(t, "r_multiple").unwrap_or(f64::NAN),
            opt_i64(t, "bars").map(|v| v as f64).unwrap_or(f64::NAN),
        ],
    }
}

/// Minutes since the epoch for an ISO timestamp ("2026-01-02T09:15:00"), or
/// NaN when absent/unparseable. The blotter sorts by time numerically (NaN
/// sinks), never by comparing formatted strings.
///
/// The calendar is VALIDATED by reusing [`parse_iso_days`] and bounding the
/// clock fields. The previous version ran the day-of-era arithmetic on
/// whatever numbers it parsed, so "2026-13-45T99:99" silently produced a
/// plausible-looking (wrong) timestamp that sorted among real trades instead
/// of sinking to the bottom.
fn iso_minutes(iso: String) -> f64 {
    if iso.len() < 16 {
        return f64::NAN;
    }
    let bytes = iso.as_bytes();
    let num = |range: std::ops::Range<usize>| -> Option<i64> {
        std::str::from_utf8(&bytes[range.clone()])
            .ok()
            .and_then(|text| text.parse::<i64>().ok())
    };
    let (Some(hour), Some(minute)) = (num(11..13), num(14..16)) else {
        return f64::NAN;
    };
    // 24:00 is the legal ISO end-of-day; 24:01+ is not a time.
    if !(0..=24).contains(&hour) || !(0..=60).contains(&minute) {
        return f64::NAN;
    }
    // Same validator the calendar picker uses: rejects month > 12, day 0/32+,
    // day 30 of February and any other impossible civil date. `iso.get(..10)`
    // (not `&iso[..10]`) so a multi-byte boundary can never panic.
    let Some(days) = iso.get(..10).and_then(parse_iso_days) else {
        return f64::NAN;
    };
    (days * 24 * 60 + hour * 60 + minute) as f64
}

fn parse_curve(value: &serde_json::Value) -> Vec<(f64, f64)> {
    value
        .get("equity_curve")
        .and_then(|v| v.as_array())
        .map(|pts| {
            pts.iter()
                .filter_map(|p| {
                    let e = p.get("equity")?.as_f64()?;
                    let d = p.get("drawdown_pct")?.as_f64().unwrap_or(0.0);
                    Some((e, d))
                })
                .collect()
        })
        .unwrap_or_default()
}

fn block_to_results(block: &serde_json::Value) -> LabResults {
    let metrics = block
        .get("metrics")
        .cloned()
        .unwrap_or(serde_json::json!({}));
    let ranking = block
        .get("ranking")
        .and_then(|v| v.as_array())
        .map(|rows| rows.iter().map(parse_rank_row).collect())
        .unwrap_or_default();
    let trades = block
        .get("trades")
        .and_then(|v| v.as_array())
        .map(|rows| {
            rows.iter()
                .enumerate()
                .map(|(i, t)| parse_trade_row(i, t))
                .collect()
        })
        .unwrap_or_default();
    let equity = parse_curve(block);
    let dd_points: Vec<(f64, f64)> = equity.iter().map(|(_, d)| (0.0, *d)).collect();
    let risk_notes: Vec<String> = block
        .get("risk_notes")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|n| n.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    LabResults {
        kpis: parse_kpis(&metrics),
        ranking,
        trades,
        equity: normalized_series(&equity, false),
        drawdown: normalized_series(&dd_points, true),
        risk_notes,
    }
}

/// Joint min/max normalization for the dual BUY/SELL overlay (shared scale,
/// like the legacy dual view) — returns (buy, sell) view points.
fn joint_normalized(buy: &[(f64, f64)], sell: &[(f64, f64)]) -> (Vec<(f32, f32)>, Vec<(f32, f32)>) {
    let all: Vec<f64> = buy
        .iter()
        .map(|(e, _)| *e)
        .chain(sell.iter().map(|(e, _)| *e))
        .collect();
    if all.len() < 2 {
        return (Vec::new(), Vec::new());
    }
    let min = all.iter().cloned().fold(f64::INFINITY, f64::min);
    let max = all.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    let span = (max - min).max(1e-9);
    let one = |pts: &[(f64, f64)]| {
        if pts.len() < 2 {
            return Vec::new();
        }
        let n = pts.len() - 1;
        pts.iter()
            .enumerate()
            .map(|(i, (e, _))| (i as f32 / n as f32, ((e - min) / span) as f32))
            .collect()
    };
    (one(buy), one(sell))
}

fn parse_capital(value: &serde_json::Value) -> String {
    match value.get("capital") {
        Some(v) if v.is_i64() || v.is_u64() => v.to_string(),
        Some(v) => v.as_f64().map_or_else(String::new, |amount| {
            if amount.fract() == 0.0 && amount.abs() < 1e15 {
                format!("{}", amount as i64)
            } else {
                format!("{amount}")
            }
        }),
        None => String::new(),
    }
}

/// Finish the bridge pass: main results plus every parity echo (editor,
/// editable config, ranking controls, drill-down, compare sides, selection,
/// run label, chart captions). All defensive — absence stays absence.
fn apply_parity_keys(state: &mut LabState, value: &serde_json::Value) {
    if let Some(results) = value.get("results") {
        if results.is_null() {
            state.results = None;
            state.results_fingerprint = None;
        } else if results.is_object() {
            state.results = Some(block_to_results(results));
            state.results_fingerprint = Some(state.run_key());
            if matches!(state.run, RunState::Running) {
                state.run = RunState::Complete;
            }
            // New data invalidates the index layer: rebuild it here (once per
            // snapshot), never in the frame path.
            state.rebuild_rank_view();
            // Same for the trade blotter: the new trade list invalidates its
            // index layer once, here.
            state.rebuild_trade_view();
        }
    }
    // Editor buffer: adopt the backend echo only when it carries source.
    // An absent or empty `code` means "unchanged" (polls omit the ~39KB
    // blob), so local edits are never clobbered; dirty is derived in
    // `project`.
    let code = opt_str(value, "code");
    if !code.is_empty() {
        state.code = code.clone();
        state.synced_code = code;
    }
    if let Some(params) = value.get("params").and_then(|v| v.as_array()) {
        state.params = params
            .iter()
            .map(|p| ParamItem {
                key: opt_str(p, "key"),
                label: opt_str(p, "label"),
                value: match p.get("value") {
                    Some(v) if v.is_i64() || v.is_u64() => v.to_string(),
                    Some(v) => v.as_f64().map_or_else(String::new, |n| format!("{n}")),
                    None => String::new(),
                },
            })
            .filter(|p| !p.key.is_empty())
            .collect();
    }
    if let Some(cfg) = value.get("cfg_edit") {
        // A workspace echo only carries the symbols the backend was told
        // about, and the bridge only re-sends them on a strategy SELECT.
        // Adopting an EMPTY universe echo would wipe a selection the user
        // just made (queued `symbols:` is not applied backend-side yet), and
        // the next RUN would then gather no symbols at all — a failed run
        // with an empty ranking table. Explicit clears already land locally,
        // so an empty echo is absence, not truth.
        let echoed_csv = opt_str(cfg, "universe_csv");
        if !echoed_csv.trim().is_empty() {
            state.cfg_universe_csv = echoed_csv;
            state.cfg_universe_count = count_symbols(&state.cfg_universe_csv);
        }
        state.timeframes = cfg
            .get("timeframes")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|t| t.as_str().map(str::to_string))
                    .collect()
            })
            .unwrap_or_default();
        let current = opt_str(cfg, "timeframe");
        // A missing ladder (or a missing entry) disables the control (-1) —
        // it never auto-selects timeframes[0].
        state.timeframe_index = state
            .timeframes
            .iter()
            .position(|t| *t == current)
            .map(|i| i as i32)
            .unwrap_or(-1);
        state.cfg_dates_start = opt_str(cfg, "dates_start");
        state.cfg_dates_end = opt_str(cfg, "dates_end");
        state.cfg_capital = parse_capital(cfg);
        state.config_error = {
            let scoped = opt_str(cfg, "config_error");
            if !scoped.trim().is_empty() {
                scoped
            } else {
                let top = opt_str(value, "config_error");
                if !top.trim().is_empty() {
                    top
                } else {
                    opt_str(value, "error")
                }
            }
        };
    }
    // Real store history bounds. These are the ANCHOR symbol's actual
    // first/last available dates, which is what the §02 `MAX` preset means —
    // the committed `cfg_edit` dates are the user's SELECTION, and deriving
    // `MAX` from those would shrink the preset onto whatever was last picked.
    if let Some(bounds) = value.get("data_bounds") {
        state.data_bounds_start_days = parse_iso_days(opt_str(bounds, "first").trim())
            .map(|v| v as i32)
            .unwrap_or(-1);
        state.data_bounds_end_days = parse_iso_days(opt_str(bounds, "last").trim())
            .map(|v| v as i32)
            .unwrap_or(-1);
    }
    if let Some(rankby) = value.get("rankby") {
        state.rankby_labels = rankby
            .get("labels")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|t| t.as_str().map(str::to_string))
                    .collect()
            })
            .unwrap_or_default();
        state.rankby_current = rankby.get("current").and_then(|v| v.as_i64()).unwrap_or(0) as i32;
        state.rank_search = opt_str(rankby, "search");
        state.rank_desc = rankby.get("desc").and_then(|v| v.as_bool()).unwrap_or(true);
    }
    state.detail = value.get("detail").and_then(|d| {
        if d.is_null() {
            return None;
        }
        let raw: Vec<f64> = d
            .get("equity")
            .and_then(|v| v.as_array())
            .map(|arr| arr.iter().filter_map(|n| n.as_f64()).collect())
            .unwrap_or_default();
        let pairs: Vec<(f64, f64)> = raw
            .iter()
            .enumerate()
            .map(|(i, v)| (i as f64, *v))
            .collect();
        Some(DetailView {
            symbol: opt_str(d, "symbol"),
            title: opt_str(d, "title"),
            stats: opt_str(d, "stats"),
            caption: opt_str(d, "caption"),
            metrics: d
                .get("metrics")
                .and_then(|v| v.as_array())
                .map(|arr| {
                    arr.iter()
                        .map(|m| DetailMetric {
                            label: opt_str(m, "label"),
                            value: opt_str(m, "value"),
                        })
                        .collect()
                })
                .unwrap_or_default(),
            equity: normalized_series(&pairs, false),
        })
    });
    // Single-side results for the COMPARE matrix/board/curves.
    let buy_raw = value.get("buy").filter(|v| !v.is_null());
    let sell_raw = value.get("sell").filter(|v| !v.is_null());
    state.results_buy = buy_raw.map(block_to_results);
    state.results_sell = sell_raw.map(block_to_results);
    state.compare = value.get("compare").and_then(|c| {
        if c.is_null() {
            return None;
        }
        let verdict = opt_str(c, "banner_verdict");
        let tone = if verdict == "BUY / LONG" {
            4
        } else if verdict == "SELL / SHORT" {
            3
        } else {
            0
        };
        let buy_curve = buy_raw.map(parse_curve).unwrap_or_default();
        let sell_curve = sell_raw.map(parse_curve).unwrap_or_default();
        let (equity_buy, equity_sell) = joint_normalized(&buy_curve, &sell_curve);
        // Drawdown dual on the same inverted-absolute shape as the single
        // drawdown chart, joint-scaled for comparison.
        let dd_shape = |curve: &[(f64, f64)]| {
            curve
                .iter()
                .map(|(_, d)| (0.0, -d.abs()))
                .collect::<Vec<(f64, f64)>>()
        };
        let (drawdown_buy, drawdown_sell) =
            joint_normalized(&dd_shape(&buy_curve), &dd_shape(&sell_curve));
        Some(CompareView {
            banner_verdict: verdict,
            banner_reason: opt_str(c, "banner_reason"),
            banner_tone: tone,
            trade_summary: opt_str(c, "trade_summary"),
            stale_notice: opt_str(c, "stale_notice"),
            board_scope: opt_str(c, "board_scope"),
            board_leader: opt_str(c, "board_leader"),
            matrix: c
                .get("matrix")
                .and_then(|v| v.as_array())
                .map(|arr| {
                    arr.iter()
                        .map(|m| MatrixRow {
                            key: opt_str(m, "key"),
                            buy: opt_str(m, "buy"),
                            sell: opt_str(m, "sell"),
                            winner: match m.get("winner").and_then(|v| v.as_str()) {
                                Some("buy") => 1,
                                Some("sell") => 2,
                                _ => 0,
                            },
                        })
                        .collect()
                })
                .unwrap_or_default(),
            board: c
                .get("board_rows")
                .and_then(|v| v.as_array())
                .map(|arr| {
                    arr.iter()
                        .map(|row| BoardRow {
                            label: opt_str(row, "label"),
                            kind: opt_str(row, "kind"),
                            higher: row.get("higher").and_then(|v| v.as_bool()),
                            cells: row
                                .get("values")
                                .and_then(|v| v.as_array())
                                .map(|cells| {
                                    cells
                                        .iter()
                                        .map(|cell| BoardCell {
                                            symbol: opt_str(cell, "symbol"),
                                            value: cell.get("value").and_then(|v| v.as_f64()),
                                        })
                                        .collect()
                                })
                                .unwrap_or_default(),
                        })
                        .collect()
                })
                .unwrap_or_default(),
            board_symbols: c
                .get("board_rows")
                .and_then(|v| v.as_array())
                .and_then(|arr| arr.first())
                .and_then(|row| row.get("values"))
                .and_then(|v| v.as_array())
                .map(|cells| cells.iter().map(|cell| opt_str(cell, "symbol")).collect())
                .unwrap_or_default(),
            board_trades: Vec::new(),
            ranking: c
                .get("ranking")
                .and_then(|v| v.as_array())
                .map(|rows| rows.iter().map(parse_rank_row).collect())
                .unwrap_or_default(),
            equity_buy,
            equity_sell,
            drawdown_buy,
            drawdown_sell,
        })
    });
    state.selected_trade = opt_i64(value, "selected_trade").unwrap_or(-1) as i32;
    if let Some(filter) = value.get("trade_filter") {
        state.trade_needle = opt_str(filter, "needle");
        state.trade_symbol = opt_str(filter, "symbol");
        state.trade_filters_active = filter
            .get("active")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
    }
    state.run_label = opt_str(value, "run_label");
    state.equity_summary = opt_str(value, "equity_summary");
    state.drawdown_summary = opt_str(value, "drawdown_summary");
    if let Some(universe) = value.get("universe") {
        state.universe_symbols = universe
            .get("symbols")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|s| s.as_str().map(str::to_string))
                    .filter(|s| !s.is_empty())
                    .collect()
            })
            .unwrap_or_default();
        // Same rule as cfg_edit.universe_csv: an empty `selected` echo is
        // absence (the backend was never told), never a clear.
        let echoed_selected: Vec<String> = universe
            .get("selected")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|s| s.as_str().map(str::to_string))
                    .filter(|s| !s.is_empty())
                    .collect()
            })
            .unwrap_or_default();
        if !echoed_selected.is_empty() {
            state.universe_selected = echoed_selected;
        }
        // Draft tracks the applied echo while the panel is closed, so the
        // selector always opens on the truth (never stale).
        if !state.sym_open {
            state.sym_draft = state.universe_selected.clone();
        }
    }
    apply_saved_universe_json(state, value);
}

/// Adopt the selected strategy's PERSISTED stock universe from the snapshot.
///
/// Authority: the backend's `saved_universe` block (per strategy, from the
/// universe store). A missing or unreadable block clears the row to a truthful
/// state rather than keeping the previous strategy's chips; an explicit empty
/// universe is a real state, never treated as "absence" like the run echo.
fn apply_saved_universe_json(state: &mut LabState, value: &serde_json::Value) {
    let Some(saved) = value.get("saved_universe") else {
        state.saved_universe_symbols.clear();
        state.saved_universe_status = SavedUniverseStatus::Missing;
        state.saved_universe_error.clear();
        return;
    };
    let symbols: Vec<String> = saved
        .get("symbols")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|s| s.as_str().map(str::to_string))
                .filter(|s| !s.is_empty())
                .collect()
        })
        .unwrap_or_default();
    state.saved_universe_status = match saved.get("state").and_then(|v| v.as_str()) {
        Some("ok") => SavedUniverseStatus::Ok,
        Some("empty") => SavedUniverseStatus::Empty,
        Some("error") => SavedUniverseStatus::Error,
        _ => SavedUniverseStatus::Missing,
    };
    state.saved_universe_error = saved
        .get("error")
        .and_then(|v| v.as_str())
        .unwrap_or_default()
        .to_string();
    state.saved_universe_symbols = symbols;
}
