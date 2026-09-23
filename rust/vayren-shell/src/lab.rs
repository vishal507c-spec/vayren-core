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
}

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
    /// Ranking criterion dropdown (legacy box order echoed verbatim).
    pub rankby_labels: Vec<String>,
    pub rankby_current: i32,
    pub rank_search: String,
    pub rank_desc: bool,
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
    /// The real topbar RUN button text, echoed verbatim.
    pub run_label: String,
    pub equity_summary: String,
    pub drawdown_summary: String,
    /// Market Watchlist universe (single source of truth, backend-owned).
    pub universe_symbols: Vec<String>,
    /// Applied selection echo (backend-owned).
    pub universe_selected: Vec<String>,
    /// Selector panel open + working draft (draft mirrors the echo while
    /// closed; edits stay local until Apply — same precedent as search).
    pub sym_open: bool,
    pub sym_search: String,
    pub sym_draft: Vec<String>,
    /// Reference-layout echoes (view-local until the backend supplies them).
    pub cfg_cost: String,
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
            self.queue_action(format!("settimeframe:{timeframe}"));
        }
    }

    pub fn interaction_capital(&mut self, value: &str) {
        let value = value.trim();
        if !value.is_empty() {
            self.queue_action(format!("capital:{value}"));
        }
    }

    pub fn interaction_dates(&mut self, start: &str, end: &str) {
        self.queue_action(format!("dates:{}:{}", start.trim(), end.trim()));
    }

    pub fn interaction_ranksearch(&mut self, text: &str) {
        self.rank_search = text.to_string();
        self.queue_action(format!("ranksearch:{text}"));
    }

    pub fn interaction_rankby(&mut self, label: &str) {
        // The Slint ComboBox reports the selected label (legacy box order is
        // echoed verbatim, so the position is the legacy box index).
        if let Some(index) = self.rankby_labels.iter().position(|l| l == label) {
            self.queue_action(format!("rankby:{index}"));
        }
    }

    pub fn interaction_ranktoggle(&mut self) {
        self.queue_action("ranktoggle".to_string());
    }

    pub fn interaction_trade_pick(&mut self, index: i32) {
        // legacy click = highlight the row AND focus the trade downstream.
        self.queue_action(format!("tradesel:{index}"));
        self.queue_action(format!("tradefocus:{index}"));
    }

    pub fn interaction_tradefilter(&mut self, text: &str) {
        self.trade_needle = text.to_string();
        self.queue_action(format!("tradefilter:{text}"));
    }

    /// COMPARE side filter is view-local (pure filtering of engine trades,
    /// same precedent as library search — deliberately NOT queued).
    pub fn interaction_cmpside(&mut self, side: i32) {
        if (0..=2).contains(&side) {
            self.compare_side = side;
        }
    }

    /// Reference-layout interactions. Perspectives lens + equity view are
    /// view-local presentation filters (never queued); the cost field and
    /// inspector close go through the existing pending-actions bridge.
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

    pub fn interaction_cost(&mut self, value: &str) {
        let value = value.trim();
        if !value.is_empty() {
            self.cfg_cost = value.to_string();
            self.queue_action(format!("cost:{value}"));
        }
    }

    pub fn interaction_detail_close(&mut self) {
        self.detail = None;
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
        let csv = self
            .sym_draft
            .iter()
            .filter(|s| self.universe_symbols.iter().any(|u| u == *s))
            .cloned()
            .collect::<Vec<_>>()
            .join(",");
        self.sym_open = false;
        self.queue_action(format!("symbols:{csv}"));
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
        }
    }

    /// Drain pending user actions (host forwards them to the backend).
    pub fn drain_actions(&mut self) -> Vec<String> {
        std::mem::take(&mut self.pending_actions)
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
        self.outdated = fp != &self.config.fingerprint();
    }

    /// Engine bridge entry point: attach completed results for the CURRENT
    /// configuration fingerprint.
    pub fn apply_result(&mut self, results: LabResults) {
        self.results_fingerprint = Some(self.config.fingerprint());
        self.results = Some(results);
        self.run = RunState::Complete;
        self.outdated = false;
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

    pub fn results_or_default(&self) -> LabResults {
        self.results.clone().unwrap_or_default()
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
    pub ranking: Vec<RankRow>,
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
    pub trade_needle: String,
    pub trade_symbol: String,
    pub run_label: String,
    pub is_compare: bool,
    pub sym_open: bool,
    pub sym_search: String,
    pub sym_button_line: String,
    pub sym_count_line: String,
    pub sym_visible: Vec<String>,
    pub sym_visible_on: Vec<bool>,
    /// Applied-universe chips (split of `cfg_universe_csv`) — presentation
    /// projection only; Slint strings have no split, so it happens here.
    pub universe_chips: Vec<String>,
    // Reference-layout projection (see project() derivations).
    pub cfg_cost: String,
    pub cfg_cost_warn: bool,
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
}

/// One quick-select preset for the date-range picker. Ranges are day anchors
/// (days since epoch) so the picker highlights/applies without string parsing.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LabPresetData {
    pub label: String,
    pub start_days: i32,
    pub end_days: i32,
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
    if b.len() != 10 || b[4] != '-' || b[7] != '-' || !b.iter().enumerate().all(|(i, c)| i == 4 || i == 7 || c.is_ascii_digit()) {
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

/// Ranking rows displayed before the presentation cap (the label always
/// states the true analyzed count — the cap is a viewport policy, same as
/// the legacy blotter precedent).
pub const RANKING_VIEW_CAP: usize = 50;

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

    // Ranking source: single-mode engine rows, compare-mode board rows.
    let rank_source: &[RankRow] = if is_compare {
        &cmp.ranking
    } else {
        &results.ranking
    };
    let shown_ranking: Vec<RankRow> = if show_results {
        rank_source.iter().take(RANKING_VIEW_CAP).cloned().collect()
    } else {
        Vec::new()
    };
    // legacy scope vocabulary ("3 stocks analyzed"), counted from the echoed
    // universe CSV — the same count the config toolbar shows.
    let universe_count = state
        .cfg_universe_csv
        .split(',')
        .filter(|s| !s.trim().is_empty())
        .count();
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

    // Criterion arrow on the active header (legacy appends ▼/▲ to the sorted
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

    // Trades: single mode shows all; compare mode filters by the side
    // segment (absolute indices survive the filter for focus parity).
    let shown_trades: Vec<TradeRow> = if show_results {
        results
            .trades
            .into_iter()
            .filter(|t| {
                !is_compare
                    || state.compare_side == 0
                    || (state.compare_side == 1 && t.side == "LONG")
                    || (state.compare_side == 2 && t.side == "SHORT")
            })
            .map(|mut t| {
                t.selected = t.abs_index == state.selected_trade && !state.trade_filters_active;
                t
            })
            .collect()
    } else {
        Vec::new()
    };

    let shown_equity: Vec<(f32, f32)> = if show_results {
        results.equity
    } else {
        Vec::new()
    };
    let shown_drawdown: Vec<(f32, f32)> = if show_results {
        results.drawdown
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
        kpis.iter().find(|k| k.label == label).map(|k| (k.value.clone(), k.tone))
    };
    let (result_pnl, result_pnl_tone) = find_kpi("NET P&L").unwrap_or_default();
    let result_return = find_kpi("RETURN").map_or(String::new(), |k| k.0);
    let studio_ref_pf = find_kpi("PF").map_or(String::new(), |k| k.0);
    let result_exec_line = if has_strategy {
        format!(
            "{} · {}",
            strategy.map_or_else(String::new, |s| s.name.clone()).to_uppercase(),
            summary_line
        )
    } else {
        String::new()
    };
    let (stale_exec_line, stale_cur_line) = if state.outdated {
        (
            state.results_fingerprint.clone().unwrap_or_default(),
            state.config.fingerprint(),
        )
    } else {
        (String::new(), String::new())
    };
    let range_line = if state.config.dates.is_empty() {
        String::new()
    } else {
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
        format!(
            "{} is the current working strategy.",
            strategy.map_or_else(String::new, |s| s.name.clone())
        )
    };
    let cfg_cost_warn = matches!(
        state.cfg_cost.trim(),
        "0" | "0.0" | "0.00" | "0.000" | "0.0%" | "0%"
    );

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
        (Some(a), None) if end_raw => (human_from_days(a), "Use a valid calendar date (YYYY-MM-DD).".to_string()),
        (None, Some(b)) if start_raw => (human_from_days(b), "Use a valid calendar date (YYYY-MM-DD).".to_string()),
        (Some(a), None) => (format!("{} \u{2014} …", human_from_days(a)), String::new()),
        (None, Some(b)) => (format!("… \u{2014} {}", human_from_days(b)), String::new()),
        (None, None) if start_raw || end_raw => (
            String::new(),
            "Use a valid calendar date (YYYY-MM-DD).".to_string(),
        ),
        _ => (String::new(), String::new()),
    };
    let dates_human = dates_human.to_string();

    LabView {
        has_strategy,
        name: strategy.map_or_else(|| "No strategy".to_string(), |s| s.name.clone()),        description: strategy.map_or_else(String::new, |s| s.description.clone()),
        tags: strategy.map_or_else(String::new, |s| s.tags.join(" · ")),
        version: strategy.map_or_else(String::new, |s| s.version.clone()),
        modified: strategy.map_or_else(String::new, |s| s.modified.clone()),
        last_backtest: strategy.map_or(String::new(), |s| s.last_backtest.clone()),
        state_label: state_label.to_string(),
        state_tone: state_badge,
        mode: state.mode.kind(),
        outdated: state.outdated,
        run_enabled: state.engine_wired && has_strategy,
        config_summary: summary_line.clone(),
        universe: state.config.universe.clone(),
        timeframe: state.config.timeframe.clone(),
        dates: state.config.dates.clone(),
        capital: state.config.capital.clone(),
        show_results,
        tabs_enabled: show_results,
        tab: state.tab,
        kpis,
        ranking: if is_compare {
            Vec::new()
        } else {
            shown_ranking
        },
        trades: shown_trades,
        equity: shown_equity,
        drawdown: shown_drawdown,
        risk_notes: if show_results {
            results.risk_notes
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
        universe_chips: state
            .cfg_universe_csv
            .split(',')
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect(),
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
        trade_needle: state.trade_needle.clone(),
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
        sym_visible,
        sym_visible_on,
        cfg_cost: state.cfg_cost.clone(),
        cfg_cost_warn: cfg_cost_warn,
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
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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
        // No engine bridge -> RUN disabled, start_run refused.
        assert!(!st.engine_wired);
        assert!(!st.start_run());
        assert!(!project(&st).run_enabled);
        st.engine_wired = true;
        assert!(st.start_run());
        assert!(project(&st).run_enabled);
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
    fn cost_mirrors_locally_and_queues_the_backend_commit() {
        let mut st = state_with_obr();
        st.interaction_cost(" 0.05 ");
        assert_eq!(st.cfg_cost, "0.05");
        assert_eq!(st.pending_actions, vec!["cost:0.05".to_string()]);
        st.interaction_cost("");
        assert_eq!(st.pending_actions.len(), 1); // empty never queued
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
        st.results = Some(results());
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
        // Apply queues backend CSV in draft order; panel closes.
        st.interaction_symsearch("");
        st.interaction_symapply();
        assert!(!st.sym_open);
        assert_eq!(st.pending_actions, vec!["symbols:TCS,INFY".to_string()]);
        // Close discards local edits back to the echo.
        st.interaction_symopen();
        st.interaction_symclear();
        assert!(st.sym_draft.is_empty());
        st.interaction_symclose();
        assert_eq!(st.sym_draft, vec!["RELIANCE".to_string()]);
        // Button line vocabulary.
        st.universe_selected = vec![];
        st.sym_draft = vec![];
        assert_eq!(project(&st).sym_button_line, "NO UNIVERSE");
        st.universe_selected = vec!["A".into(), "B".into()];
        st.sym_draft = st.universe_selected.clone();
        assert_eq!(project(&st).sym_button_line, "A, B");
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
        assert_eq!(year_start_days(mar31), parse_iso_days("2026-01-01").unwrap());
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

/// Indian-grouping money body, e.g. 1234567.8 → "12,34,567.80" (mirrors the
/// legacy `_inr` helper exactly: last group of 3, then groups of 2).
fn inr_body(abs_value: f64) -> String {
    let rounded = (abs_value * 100.0).round() / 100.0;
    let whole = rounded.trunc() as i64;
    let frac = ((rounded - whole as f64) * 100.0).round() as i64;
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
    let abs = value.abs();
    let whole = abs.trunc() as i64;
    let frac = ((abs - whole as f64) * 100.0).round() as i64;
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
    }
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
            state.results_fingerprint = Some(state.config.fingerprint());
            if matches!(state.run, RunState::Running) {
                state.run = RunState::Complete;
            }
        }
    }
    // Editor buffer: adopt the backend echo (typing never triggers a
    // snapshot — the host dedupes identical payloads — so local edits are
    // never clobbered; dirty is derived in `project`).
    let code = opt_str(value, "code");
    if !code.is_empty() || value.get("code").is_some() {
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
        state.cfg_universe_csv = opt_str(cfg, "universe_csv");
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
        state.timeframe_index = state
            .timeframes
            .iter()
            .position(|t| *t == current)
            .map(|i| i as i32)
            .unwrap_or(0);
        state.cfg_dates_start = opt_str(cfg, "dates_start");
        state.cfg_dates_end = opt_str(cfg, "dates_end");
        state.cfg_capital = parse_capital(cfg);
        state.config_error = opt_str(cfg, "config_error");
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
        state.universe_selected = universe
            .get("selected")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|s| s.as_str().map(str::to_string))
                    .filter(|s| !s.is_empty())
                    .collect()
            })
            .unwrap_or_default();
        // Draft tracks the applied echo while the panel is closed, so the
        // selector always opens on the truth (never stale).
        if !state.sym_open {
            state.sym_draft = state.universe_selected.clone();
        }
    }
}
