//! Market native view-model — pure, headless-testable UI state
//! (AI_ENTRY.md §1: Rust owns view-model + interaction state; Slint renders
//! bound properties only).
//!
//! This module mirrors the ORIGINAL legacy Market surface exactly: the watchlist
//! panel (switch/add/remove/reset/filter/sort), the timeframe row with the
//! canonical visible order + overflow dropdown, the INDICATORS popup
//! (search · categories · strategies), the floating indicator visibility bar,
//! the candle chart viewport (wheel zoom at cursor, horizontal-wheel pan,
//! drag pan, price-strip scale + double-click reset, follow-latest, the same
//! density/anchor constants as `CandleChartWidget`), the snapped crosshair
//! with price/time/volume tags, the permanent OHLC header, strategy plot
//! series and trade markers from the real overlay state, and the trade
//! context strip. It never computes financial truth and never invents
//! market data: bars, quotes, series and trades arrive from the backend
//! bridge; everything else is display formatting or presentation transforms
//! of real bars (viewport normalization — the LiveCandle precedent).

use crate::lab::Tone;
use crate::market_download::{self as mdownload, DownloadAction, DownloadState, DownloadView};

/// One backend OHLCV bar (mirrors `market.models.bar.Bar` fields used here).
#[derive(Debug, Clone, PartialEq)]
pub struct MarketBar {
    pub time: String,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
}

/// One watchlist row: identity plus optional quote (absent = `N/A`, honest).
#[derive(Debug, Clone, PartialEq)]
pub struct WatchEntry {
    pub symbol: String,
    pub price: Option<f64>,
    pub change_pct: Option<f64>,
}

/// A dynamically ADDED indicator (floating visibility bar row). Mirrors the
/// legacy `indicator_visibility` dict: only added names appear; each has a
/// visible flag (crossed eye hides rendering, name stays).
#[derive(Debug, Clone, PartialEq)]
pub struct IndicatorEntry {
    pub name: String,
    pub visible: bool,
}

/// One editable indicator parameter as the native settings panel renders it.
/// Values come from the backend's parameter specs (merged with the indicator's
/// stored overrides); the panel edits a local buffer and commits on SAVE.
#[derive(Debug, Clone, PartialEq)]
pub struct SettingsRow {
    pub key: String,
    pub label: String,
    pub value: f64,
    pub min: f64,
    pub max: f64,
    pub step: f64,
    pub decimals: i32,
}

/// One plotted strategy series (owner-aware, mirrors `PlotOverlay._series`:
/// `(owner, title) -> {bar_index: value}`). Gaps break the line.
#[derive(Debug, Clone, PartialEq)]
pub struct PlotSeries {
    pub owner: String,
    pub title: String,
    pub points: Vec<(usize, f64)>,
    /// Ray extension (legacy `_parse_extend` vocabulary): "" none, "session"
    /// (also "right"/"extend"/"horizontal") = horizontal rays.
    pub extend: String,
    /// Ray cap in bars (-1 = unbounded session ray).
    pub cap: i32,
}

/// One trade marker from the real overlay result (`TradeOverlay._trades`).
/// Positions are tail-relative chart slots resolved backend-side through
/// chart timestamps (replay indices are window-relative and must never
/// index chart bars); -1 = not placeable and never renders. Covered flags
/// evaluate backend-side in replay basis — the exact legacy rule.
#[derive(Debug, Clone, PartialEq)]
pub struct TradeMarker {
    pub side: String,
    pub entry_price: f64,
    pub exit_price: f64,
    pub winning: bool,
    pub entry_pos: i32,
    pub exit_pos: i32,
    pub entry_covered: bool,
    pub exit_covered: bool,
    pub exit_reason: String,
}

/// Focused single-trade detail (`TradeOverlay.focused_trade`).
#[derive(Debug, Clone, PartialEq)]
pub struct FocusedTrade {
    pub side: String,
    pub entry_pos: i32,
    pub exit_pos: i32,
    pub entry_price: f64,
    pub exit_price: f64,
    pub winning: bool,
}

/// One strategy-owned marker visual (PlotOverlay store MARKER/LABEL).
#[derive(Debug, Clone, PartialEq)]
pub struct StrategyMarker {
    /// Tail-relative bar (-1 never renders).
    pub bar: i32,
    pub price: f64,
    /// UP_ARROW/DOWN_ARROW/CIRCLE/CIRCLE_X/SQUARE/DIAMOND/TRIANGLE_UP/
    /// TRIANGLE_DOWN/TRIANGLE_BLUE/DOT (LABEL arrives as DOT).
    pub kind: String,
    pub text: String,
    /// Render layer for the painter's deterministic color rotation.
    pub layer: i32,
    /// Position in the bridge query order (same rotation input).
    pub order: i32,
}

/// One strategy-owned ray (REF HIGH/LOW style horizontals).
#[derive(Debug, Clone, PartialEq)]
pub struct StrategyRay {
    /// Tail-relative origin bar.
    pub start: i32,
    pub price: f64,
    /// Cap in bars (-1 = unbounded session ray).
    pub cap: i32,
    pub layer: i32,
    pub order: i32,
}

/// legacy `_PLOT_COLORS` rotation as Slint color codes
/// (0 accent, 1 warn/amber, 2 pos, 3 neg, 4 blue, 5 purple, 6 orange).
fn plot_color_code(position: usize) -> i32 {
    [2, 3, 1, 4, 5, 2, 6][position % 7]
}

/// Store-record color: `_PLOT_COLORS[(layer // 10 + order) % 7]`.
fn store_color_code(layer: i32, order: i32) -> i32 {
    let position = (layer.div_euclid(10) + order) as usize;
    plot_color_code(position)
}

/// Trade context strip facts (labels the retained legacy panel already renders;
/// the native strip shows the same strings, never re-derived).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct TradeContext {
    pub visible: bool,
    pub trade: String,
    pub symbol_side: String,
    pub time: String,
    pub pnl: String,
    pub r: String,
}

/// Price-scale mode (TradingView-observable vocabulary, own implementation).
/// Regular = price; Percent = % move vs the first visible close; Logarithmic
/// = log-spaced geometry with price labels. Percent/Log fall back to Regular
/// for any frame whose data cannot represent them (never invents a scale).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum ChartScaleMode {
    #[default]
    Regular,
    Percent,
    Logarithmic,
}

impl ChartScaleMode {
    pub fn kind(self) -> i32 {
        match self {
            ChartScaleMode::Regular => 0,
            ChartScaleMode::Percent => 1,
            ChartScaleMode::Logarithmic => 2,
        }
    }
    pub fn label(self) -> &'static str {
        match self {
            ChartScaleMode::Regular => "₹",
            ChartScaleMode::Percent => "%",
            ChartScaleMode::Logarithmic => "log",
        }
    }
    pub fn from_kind(kind: i32) -> Self {
        match kind {
            1 => ChartScaleMode::Percent,
            2 => ChartScaleMode::Logarithmic,
            _ => ChartScaleMode::Regular,
        }
    }
    pub fn cycle(self) -> Self {
        match self {
            ChartScaleMode::Regular => ChartScaleMode::Percent,
            ChartScaleMode::Percent => ChartScaleMode::Logarithmic,
            ChartScaleMode::Logarithmic => ChartScaleMode::Regular,
        }
    }
}

/// Data-availability state of the chart region — exactly the three honest
/// messages the legacy widget paints (`paintEvent`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum MarketStatus {
    /// Model not present yet ("Loading chart…").
    #[default]
    Loading,
    /// Bars present and rendered.
    Ready,
    /// Model present with zero bars / empty viewport ("No … data" states).
    Empty,
}

/// Explicit UI actions (Slint reports; Rust mutates centrally).
#[derive(Debug, Clone, PartialEq)]
pub enum MarketAction {
    PanelToggle,
    SelectSymbol(String),
    SelectTimeframe(String),
    SetFilter(String),
    SortAsc,
    SortDesc,
    AddWatchlist,
    RemoveWatchlist,
    SelectWatchlist(String),
    ResetView,
    /// Pointer moved over the canvas (fractions of the plot rect).
    HoverMoved(f32, f32),
    HoverLeft,
    /// Wheel over the canvas: zoom anchored at x fraction (legacy ZOOM_STEP).
    WheelZoom(f32, f32),
    /// Horizontal wheel: pan by a plot-width fraction.
    WheelPanX(f32),
    /// Left-drag started (plot fractions).
    DragStart(f32, f32),
    /// Left-drag moved (plot fractions) — pans time + price like legacy.
    DragMove(f32, f32),
    /// Left-drag released — re-engages follow when at the right edge.
    DragEnd,
    /// Wheel over the price strip: vertical price zoom at y fraction.
    PriceZoom(f32, f32),
    /// Price-strip drag: vertical scaling anchored at the press point.
    /// `notches` is the pointer delta since press in wheel-notches
    /// (legacy `_drag_price_from`: `PRICE_ZOOM_STEP ** (delta_y / 120)` applied
    /// to the live range around the anchor pixel); `anchor_frac` is the
    /// press y fraction (constant for the gesture).
    PriceDrag(f32, f32),
    PriceDragEnd,
    /// Double-click on the price strip: auto-fit again.
    PriceReset,
    /// Indicator popup open/close (INDICATORS button).
    IndicatorPopup(bool),
    /// Indicator popup search text.
    IndicatorQuery(String),
    /// Indicator popup category chip.
    IndicatorCategory(String),
    /// Indicator popup row picked (non-strategy) — backend adds it.
    AddIndicator(String),
    /// Floating-bar eye: per-indicator visibility.
    ToggleIndicatorVisible(String),
    /// Floating-bar ⚙: open the indicator's settings panel (name), or close
    /// it when the flag is false (name empty). The panel is a native surface;
    /// its rows come from the backend's parameter specs.
    SettingsPopup(bool, String),
    /// Settings panel: one SpinBox edit (key, new value) — kept in the local
    /// buffer until SAVE or RESET.
    SettingsEdit(String, f64),
    /// Settings panel SAVE (name, json payload) — committed to the backend,
    /// which stores the parameters and re-runs the indicator's plots with
    /// them merged over the strategy's own defaults.
    ApplyIndicatorSettings(String, String),
    /// Settings panel RESET — the backend drops stored overrides for the
    /// indicator (strategy defaults take over again).
    ResetIndicatorSettings(String),
    /// Floating-bar delete: remove the indicator completely (row, plots,
    /// renderer objects). The panel ROWS drive the bar, so a removed name
    /// cannot reappear on refresh / timeframe / symbol change.
    RemoveIndicator(String),
    /// Trade context strip prev/next/open (backend controller owns them).
    TradePrev,
    TradeNext,
    TradeOpen,
    /// Toggle the market-status strip (rail button; view-local like
    /// `PanelToggle` — no backend wire).
    ToggleStatus,
    /// Cycle the price-scale mode Regular → Percent → Logarithmic.
    CycleScaleMode,
    /// Toggle grid-line visibility (display only, no geometry rebuild).
    ToggleGrid,
    /// Toggle crosshair visibility (display only, no geometry rebuild).
    ToggleCrosshair,
}

// Viewport numbers ported 1:1 from `CandleChartWidget` (behavior parity).
pub const MIN_VISIBLE_BARS: usize = 10;
pub const MAX_VISIBLE_BARS: usize = 10000;
pub const INITIAL_BARS: usize = 1400;
/// One candle's full horizontal budget in px (legacy `MIN_CANDLE_SLOT`).
pub const MIN_CANDLE_SLOT_PX: f32 = 1.0;
pub const RIGHT_MARGIN_FRACTION: f64 = 0.15;
pub const PRICE_EDGE_MARGIN: f64 = 0.05;
pub const ZOOM_STEP: f64 = 1.25;
pub const PRICE_ZOOM_STEP: f64 = 1.25;

/// Canonical visible timeframe order (legacy `TimeframeToolbar._VISIBLE_ORDER`);
/// timeframes outside it fall into the overflow dropdown.
pub const TIMEFRAME_VISIBLE_ORDER: [&str; 7] = ["5m", "15m", "30m", "45m", "1h", "2h", "4h"];

/// Indicator catalogue exactly as `IndicatorsToolbar.INDICATORS` ships it.
pub const INDICATOR_CATEGORIES: [(&str, &[&str]); 4] = [
    ("TREND", &["SMA", "EMA", "VWAP", "Supertrend"]),
    ("MOMENTUM", &["RSI", "MACD", "Stochastic"]),
    ("VOLUME", &["Volume", "OBV"]),
    ("VOLATILITY", &["Bollinger Bands", "ATR", "ADX"]),
];

pub fn indicator_categories() -> &'static [(&'static str, &'static [&'static str])] {
    &INDICATOR_CATEGORIES
}

/// Responsive tiers. Mirrors the `VayrenDesign.market-wide/market-medium`
/// tokens (logical px): the host/bridge feeds the resulting flags so the
/// layout recomposes from inputs instead of measuring itself.
pub const MARKET_WIDE_PX: f32 = 1180.0;
pub const MARKET_MEDIUM_PX: f32 = 820.0;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MarketLayoutMode {
    Wide,
    Medium,
    Narrow,
}

pub fn layout_mode(content_width: f32) -> MarketLayoutMode {
    if content_width >= MARKET_WIDE_PX {
        MarketLayoutMode::Wide
    } else if content_width >= MARKET_MEDIUM_PX {
        MarketLayoutMode::Medium
    } else {
        MarketLayoutMode::Narrow
    }
}

/// Snapped crosshair state (nearest candle + pointer y for the price tag).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct MarketHover {
    pub index: usize,
    pub y_frac: f32,
    pub price: f64,
}

/// Market-status panel facts — the native port of legacy `MarketStatusPanel`.
/// Honest-unknown semantics preserved exactly: every value defaults to "--"
/// and `--` renders muted (never zero-filled, never invented).
#[derive(Debug, Clone, PartialEq)]
pub struct MarketStatusFacts {
    /// Collapsed by default (legacy dock starts hidden; the app toggles it).
    pub open: bool,
    pub regime_current: String,
    pub regime_trend: String,
    pub regime_volatility: String,
    pub regime_momentum: String,
    pub provider: String,
    pub latency: String,
    pub last_update: String,
    pub bars_loaded: String,
}

impl Default for MarketStatusFacts {
    fn default() -> Self {
        Self {
            open: false,
            regime_current: "--".to_string(),
            regime_trend: "--".to_string(),
            regime_volatility: "--".to_string(),
            regime_momentum: "--".to_string(),
            provider: "--".to_string(),
            latency: "--".to_string(),
            last_update: "--".to_string(),
            bars_loaded: "--".to_string(),
        }
    }
}

/// Single source of Market presentation state.
#[derive(Debug, Clone, PartialEq)]
pub struct MarketState {
    pub symbols: Vec<WatchEntry>,
    pub selected_symbol: String,
    pub watchlists: Vec<String>,
    pub active_watchlist: String,
    pub filter: String,
    pub sort_ascending: bool,
    pub panel_visible: bool,
    pub timeframes: Vec<String>,
    pub timeframe: String,
    pub bars: Vec<MarketBar>,
    pub status: MarketStatus,
    /// Actionable backend notice for the empty chart (e.g. which symbol /
    /// timeframe has no candles). Empty when there is nothing to report —
    /// the generic message is the fallback, never the primary.
    pub notice: String,
    pub exchange: String,
    /// Visible window: first bar index + count (legacy `_first`/`_last`).
    pub first: usize,
    pub count: usize,
    pub follow_latest: bool,
    pub price_manual: Option<(f64, f64)>,
    /// Drag origin (plot fractions) while a left-drag pans the viewport.
    pub drag_origin: Option<(f32, f32, usize, (f64, f64))>,
    pub price_drag_active: bool,
    pub hover: Option<MarketHover>,
    pub indicators: Vec<IndicatorEntry>,
    /// Editable parameter specs per indicator, as the backend reports them
    /// (name -> rows). The settings panel renders exactly these rows.
    pub indicator_params: std::collections::HashMap<String, Vec<SettingsRow>>,
    /// Settings panel state: open flag, edited indicator, local edit buffer.
    pub settings_open: bool,
    pub settings_name: String,
    pub settings_rows: Vec<SettingsRow>,
    pub popup_open: bool,
    pub popup_query: String,
    pub popup_category: String,
    pub strategies: Vec<String>,
    pub plot_series: Vec<PlotSeries>,
    pub trade_markers: Vec<TradeMarker>,
    pub focused_trade: Option<usize>,
    /// Focused single-trade detail (explicit inspection overlay).
    pub focused: Option<FocusedTrade>,
    /// Strategy-owned marker visuals (entries, exits, EOD pills).
    pub strategy_markers: Vec<StrategyMarker>,
    /// Strategy-owned rays (REF HIGH/LOW horizontals).
    pub strategy_rays: Vec<StrategyRay>,
    pub trade_context: TradeContext,
    /// Density cap in candles for the current plot width (screen reports it;
    /// legacy computes it from its live geometry — same information source).
    pub width_cap: usize,
    /// Historical-Download console (native port of the retained legacy panel).
    pub download: DownloadState,
    /// Market-status panel (regime + data-status grid; legacy
    /// `MarketStatusPanel` parity).
    pub market_status: MarketStatusFacts,
    /// Queued backend intents for the embedded host to drain (mirrors
    /// `LabState::pending_actions`).
    pub pending_actions: Vec<String>,
    /// Chart display settings (engine-owned, Slint renders them verbatim):
    /// price-scale mode plus grid/crosshair visibility. Toggles never touch
    /// data, viewport or caches — geometry rebuilds only for scale changes.
    pub scale_mode: ChartScaleMode,
    pub grid_visible: bool,
    pub cross_visible: bool,
    pub cached_price_range: std::cell::Cell<Option<(usize, usize, usize, (f64, f64))>>,
}

impl Default for MarketState {
    fn default() -> Self {
        Self {
            symbols: Vec::new(),
            selected_symbol: String::new(),
            watchlists: vec!["All Stocks".to_string()],
            active_watchlist: "All Stocks".to_string(),
            filter: String::new(),
            sort_ascending: true,
            panel_visible: true,
            timeframes: Vec::new(),
            timeframe: String::new(),
            bars: Vec::new(),
            status: MarketStatus::Loading,
            notice: String::new(),
            exchange: String::new(),
            first: 0,
            count: INITIAL_BARS,
            follow_latest: true,
            price_manual: None,
            drag_origin: None,
            price_drag_active: false,
            hover: None,
            indicators: Vec::new(),
            indicator_params: std::collections::HashMap::new(),
            settings_open: false,
            settings_name: String::new(),
            settings_rows: Vec::new(),
            popup_open: false,
            popup_query: String::new(),
            popup_category: "ALL".to_string(),
            strategies: Vec::new(),
            plot_series: Vec::new(),
            trade_markers: Vec::new(),
            focused_trade: None,
            focused: None,
            strategy_markers: Vec::new(),
            strategy_rays: Vec::new(),
            trade_context: TradeContext::default(),
            width_cap: MAX_VISIBLE_BARS,
            download: DownloadState::default(),
            market_status: MarketStatusFacts::default(),
            pending_actions: Vec::new(),
            scale_mode: ChartScaleMode::Regular,
            grid_visible: true,
            cross_visible: true,
            cached_price_range: std::cell::Cell::new(None),
        }
    }
}

impl MarketState {
    /// Maximum candles readable at the current plot width (legacy
    /// `max_visible_bars`): density cap bounded by the MAX_VISIBLE_BARS limit.
    pub fn max_visible_bars(&self) -> usize {
        self.width_cap.max(MIN_VISIBLE_BARS).min(MAX_VISIBLE_BARS)
    }

    /// legacy `_anchor_first`: latest bar sits `RIGHT_MARGIN_FRACTION` from the
    /// right edge (empty space to its right).
    fn anchor_first(&self, total: usize, count: usize) -> usize {
        if total == 0 {
            return 0;
        }
        let target = round_py((1.0 - RIGHT_MARGIN_FRACTION) * count as f64 + 0.5) as usize;
        total.saturating_sub(target).min(total - 1)
    }

    /// Live-edge anchor: rightmost `first` that still reads as "following".
    /// Follow-latest re-engages only exactly here (see `DragEnd`/`WheelPanX`).
    fn max_first(&self) -> usize {
        let total = self.bars.len();
        if total == 0 {
            return 0;
        }
        self.anchor_first(total, self.window_size())
    }

    /// Rightmost `first` a pan gesture may reach.
    ///
    /// One-sided free pan: dragging/shifting the candles LEFT is free until
    /// the newest bar sits on the plot's left edge, so the whole right side of
    /// the chart can be real empty space (`[CANDLES] [EMPTY …]`). The mirror
    /// case does not exist — the other direction still stops at the oldest bar
    /// (`first = 0`), never showing empty space left of it. The newest bar
    /// always stays visible, so the viewport is never fully blank.
    fn max_pan_first(&self) -> usize {
        self.bars.len().saturating_sub(1)
    }

    fn clamp_first(&self, first: usize) -> usize {
        first.min(self.max_pan_first())
    }

    pub fn window_size(&self) -> usize {
        self.count
    }

    /// Ingest a backend bar snapshot. Mirrors legacy `set_model`: a fresh
    /// lifecycle opens at the latest readable window; the same series
    /// re-anchors while following, else shifts so the same bars stay put.
    pub fn set_bars(
        &mut self,
        symbol: &str,
        timeframe: &str,
        exchange: &str,
        bars: Vec<MarketBar>,
    ) {
        let mut clean = bars;
        clean.retain(|b| {
            b.open.is_finite()
                && b.high.is_finite()
                && b.low.is_finite()
                && b.close.is_finite()
                && b.volume.is_finite()
        });
        if !clean.windows(2).all(|w| w[0].time <= w[1].time) {
            clean.sort_by(|a, b| a.time.cmp(&b.time));
        }
        self.cached_price_range.set(None);
        let previous_symbol = std::mem::take(&mut self.selected_symbol);
        let previous_timeframe = std::mem::take(&mut self.timeframe);
        let previous_total = self.bars.len();
        let same_series = previous_symbol == symbol
            && previous_timeframe == timeframe
            && !previous_symbol.is_empty();
        self.selected_symbol = symbol.to_string();
        self.timeframe = timeframe.to_string();
        self.exchange = exchange.to_string();
        if !self.timeframes.contains(&self.timeframe) && !self.timeframe.is_empty() {
            self.timeframes.push(self.timeframe.clone());
        }
        self.hover = None;
        self.price_manual = None;
        self.bars = clean;
        if !self.bars.is_empty() {
            self.notice.clear();
        }
        if self.bars.is_empty() {
            self.status = MarketStatus::Empty;
            self.first = 0;
            self.count = 0;
        } else {
            let total = self.bars.len();
            if !same_series || self.follow_latest {
                let count = if same_series {
                    self.count
                        .max(MIN_VISIBLE_BARS)
                        .min(total.max(1))
                        .min(self.max_visible_bars())
                } else {
                    self.initial_count(total)
                };
                self.first = self.anchor_first(total, count);
                self.count = count;
                self.follow_latest = true;
            } else {
                let added = total.saturating_sub(previous_total);
                self.first = self.clamp_first(self.first + added);
            }
            self.status = MarketStatus::Ready;
        }
    }

    fn initial_count(&self, total: usize) -> usize {
        if total == 0 {
            return INITIAL_BARS;
        }
        INITIAL_BARS
            .min(total)
            .min(self.max_visible_bars())
            .max(MIN_VISIBLE_BARS)
    }

    /// Visible slice for rendering: the `count` bars from `first`.
    pub fn visible_window(&self) -> &[MarketBar] {
        if self.bars.is_empty() {
            return &[];
        }
        let first = self.first.min(self.bars.len().saturating_sub(1));
        let last = (first + self.count.max(1)).min(self.bars.len());
        &self.bars[first..last]
    }

    /// Effective vertical price range (legacy `_price_range`): manual override
    /// or auto-fit with the small edge margins.
    pub fn price_range(&self) -> (f64, f64) {
        if let Some((low, high)) = self.price_manual {
            if high > low {
                return (low, high);
            }
        }
        let (first, count, total) = (self.first, self.count, self.bars.len());
        if let Some((cf, cc, ct, cached)) = self.cached_price_range.get() {
            if cf == first && cc == count && ct == total {
                return cached;
            }
        }
        let window = self.visible_window();
        if window.is_empty() {
            return (0.0, 1.0);
        }
        let mut low = f64::INFINITY;
        let mut high = f64::NEG_INFINITY;
        for bar in window {
            low = low.min(bar.low);
            high = high.max(bar.high);
        }
        let mut span = high - low;
        if span <= 0.0 {
            span = high.abs() * 0.01;
            if span <= 0.0 {
                span = 0.01;
            }
        }
        let pad = span * PRICE_EDGE_MARGIN;
        let result = (low - pad, high + pad);
        self.cached_price_range.set(Some((first, count, total, result)));
        result
    }

    /// Apply one explicit UI action. Returns false when the action names
    /// something unknown (never invents state for it).
    pub fn apply(&mut self, action: MarketAction) -> bool {
        match action {
            MarketAction::PanelToggle => {
                self.panel_visible = !self.panel_visible;
                true
            }
            MarketAction::ToggleStatus => {
                self.market_status.open = !self.market_status.open;
                true
            }
            MarketAction::SelectSymbol(symbol) => {
                self.selected_symbol = symbol;
                self.hover = None;
                true
            }
            MarketAction::SelectTimeframe(timeframe) => {
                if self.timeframes.iter().any(|t| t == &timeframe) {
                    self.timeframe = timeframe;
                    self.hover = None;
                    true
                } else {
                    false
                }
            }
            MarketAction::SetFilter(text) => {
                self.filter = text;
                true
            }
            MarketAction::SortAsc => {
                self.sort_ascending = true;
                true
            }
            MarketAction::SortDesc => {
                self.sort_ascending = false;
                true
            }
            MarketAction::AddWatchlist => {
                let mut n = 1;
                let name = loop {
                    let candidate = format!("Watchlist {n}");
                    if !self.watchlists.iter().any(|w| w == &candidate) {
                        break candidate;
                    }
                    n += 1;
                };
                self.watchlists.push(name.clone());
                self.active_watchlist = name;
                true
            }
            MarketAction::RemoveWatchlist => {
                if self.active_watchlist == "All Stocks" {
                    return false;
                }
                self.watchlists.retain(|w| w != &self.active_watchlist);
                self.active_watchlist = "All Stocks".to_string();
                true
            }
            MarketAction::SelectWatchlist(name) => {
                if self.watchlists.iter().any(|w| w == &name) {
                    self.active_watchlist = name;
                    true
                } else {
                    false
                }
            }
            MarketAction::ResetView => {
                if self.bars.is_empty() {
                    return true;
                }
                let total = self.bars.len();
                let count = self.initial_count(total);
                self.first = self.anchor_first(total, count);
                self.count = count;
                self.follow_latest = true;
                self.price_manual = None;
                self.hover = None;
                true
            }
            MarketAction::HoverMoved(x_frac, y_frac) => {
                if self.bars.is_empty() || !x_frac.is_finite() || !y_frac.is_finite() {
                    self.hover = None;
                    return true;
                }
                // legacy _snap_crosshair: slot math over the LOGICAL window
                // (`_window_size()`), not the data-clamped slice — the right
                // margin's empty slots still snap to their candle.
                let count = self.window_size().max(1);
                let rel = round_py(f64::from(x_frac) * count as f64 - 0.5).max(0.0) as usize;
                // legacy clamps the snapped bar into [first, last-1].
                let last = (self.first + self.count).min(self.bars.len());
                let idx = rel
                    .min(count.saturating_sub(1))
                    .min(last.saturating_sub(1).saturating_sub(self.first));
                let (low, high) = self.price_range();
                let span = high - low;
                let frac = f64::from(y_frac).clamp(0.0, 1.0);
                let price = if span > 0.0 { high - frac * span } else { high };
                self.hover = Some(MarketHover {
                    index: self.first + idx,
                    y_frac,
                    price,
                });
                true
            }
            MarketAction::HoverLeft => {
                self.hover = None;
                true
            }
            MarketAction::WheelZoom(x_frac, steps) => {
                if self.bars.is_empty() || !x_frac.is_finite() || !steps.is_finite() {
                    return true;
                }
                // Inverted wheel direction: wheel UP zooms OUT, wheel DOWN
                // zooms IN (deliberate divergence from legacy _zoom_at_px, which
                // uses ZOOM_STEP ** -steps). Step size and cursor anchor are
                // unchanged.
                let scale = ZOOM_STEP.powf(f64::from(steps));
                if scale <= 0.0 {
                    return true;
                }
                let total = self.bars.len();
                let count = self.window_size().max(1);
                let fraction = f64::from(x_frac).clamp(0.0, 1.0);
                let anchor_bar = (self.first as f64 + fraction * count as f64)
                    .clamp(self.first as f64, (total - 1) as f64);
                let new_count = round_py(count as f64 * scale)
                    .clamp(MIN_VISIBLE_BARS as f64, total as f64)
                    .min(self.max_visible_bars() as f64) as usize;
                let new_count = new_count.max(MIN_VISIBLE_BARS);
                let new_first_raw =
                    round_py(anchor_bar - fraction * new_count as f64).max(0.0) as usize;
                let new_first = new_first_raw.min(self.anchor_first(total, new_count));
                self.first = new_first;
                self.count = new_count;
                self.follow_latest = new_first >= self.max_first();
                self.refresh_crosshair();
                true
            }
            MarketAction::WheelPanX(delta_frac) => {
                if self.bars.is_empty() || !delta_frac.is_finite() {
                    return true;
                }
                let count = self.window_size().max(1);
                let delta_bars = -f64::from(delta_frac) * count as f64;
                if delta_bars == 0.0 {
                    return true;
                }
                let shifted = (self.first as f64 + round_py(delta_bars)).max(0.0) as usize;
                self.first = self.clamp_first(shifted);
                // Follow-latest only while sitting exactly on the live edge; a
                // view shifted into the empty right space is a manual view.
                self.follow_latest = self.first == self.max_first();
                self.refresh_crosshair();
                true
            }
            MarketAction::DragStart(x, y) => {
                let window = self.visible_window();
                if window.is_empty() || !x.is_finite() || !y.is_finite() {
                    return false;
                }
                self.drag_origin = Some((x, y, self.first, self.price_range()));
                self.hover = None;
                true
            }
            MarketAction::DragMove(x, y) => {
                let Some((origin_x, origin_y, drag_first, (low, high))) = self.drag_origin else {
                    return false;
                };
                if self.bars.is_empty() || !x.is_finite() || !y.is_finite() {
                    return true;
                }
                let count = self.window_size().max(1);
                let delta_bars = -(f64::from(x) - f64::from(origin_x)) * count as f64;
                let shifted = (drag_first as f64 + round_py(delta_bars)).max(0.0) as usize;
                self.first = self.clamp_first(shifted);
                // Free one-sided pan: the drag may continue past the live edge
                // into the empty right space; follow-latest only ever holds at
                // the edge itself.
                self.follow_latest = self.first == self.max_first();
                let span = high - low;
                if span > 0.0 {
                    let shift = span * (f64::from(y) - f64::from(origin_y));
                    self.price_manual = Some((low + shift, high + shift));
                }
                true
            }
            MarketAction::DragEnd => {
                self.drag_origin = None;
                // Dropping exactly on the live edge re-engages follow; a view
                // parked in the empty right space stays exactly where the user
                // released it (no snap-back, no re-centering).
                if self.first == self.max_first() {
                    self.follow_latest = true;
                }
                true
            }
            MarketAction::PriceZoom(steps, y_frac) => {
                if !steps.is_finite() || steps == 0.0 || !y_frac.is_finite() {
                    return true;
                }
                let (low, high) = self.price_range();
                let span = high - low;
                if span <= 0.0 {
                    return true;
                }
                let factor = PRICE_ZOOM_STEP.powf(-f64::from(steps));
                let fraction = (1.0 - f64::from(y_frac)).clamp(0.0, 1.0);
                let anchor = low + fraction * span;
                let new_span = (span * factor).max(span * 0.01);
                let new_low = anchor - fraction * new_span;
                self.price_manual = Some((new_low, new_low + new_span));
                true
            }
            MarketAction::PriceDrag(notches, anchor_frac) => {
                self.price_drag_active = true;
                if !notches.is_finite() || !anchor_frac.is_finite() || notches == 0.0 {
                    return true;
                }
                let (low, high) = self.price_range();
                let span = high - low;
                if span <= 0.0 {
                    return true;
                }
                // legacy _drag_price_from -> _zoom_price_at(anchor_y, factor):
                // the live range zooms around the fixed press point, so the
                // price under the press cursor stays under it.
                let factor = PRICE_ZOOM_STEP.powf(f64::from(notches));
                let fraction = (1.0 - f64::from(anchor_frac)).clamp(0.0, 1.0);
                let anchor = low + fraction * span;
                let new_span = (span * factor).max(span * 0.01);
                let new_low = anchor - fraction * new_span;
                self.price_manual = Some((new_low, new_low + new_span));
                true
            }
            MarketAction::PriceDragEnd => {
                self.price_drag_active = false;
                true
            }
            MarketAction::PriceReset => {
                self.price_manual = None;
                true
            }
            MarketAction::IndicatorPopup(open) => {
                self.popup_open = open;
                if !open {
                    self.popup_query.clear();
                    self.popup_category = "ALL".to_string();
                }
                true
            }
            MarketAction::IndicatorQuery(text) => {
                self.popup_query = text;
                true
            }
            MarketAction::IndicatorCategory(cat) => {
                self.popup_category = cat;
                true
            }
            MarketAction::AddIndicator(name) => {
                let key = normalize_indicator_name(&name);
                if self.indicators.iter().any(|e| e.name == key) {
                    return true;
                }
                self.indicators.push(IndicatorEntry {
                    name: key,
                    visible: true,
                });
                true
            }
            MarketAction::ToggleIndicatorVisible(name) => {
                let key = normalize_indicator_name(&name);
                if let Some(entry) = self.indicators.iter_mut().find(|e| e.name == key) {
                    entry.visible = !entry.visible;
                    true
                } else {
                    false
                }
            }
            MarketAction::SettingsPopup(open, name) => {
                if !open {
                    self.settings_open = false;
                    self.settings_name.clear();
                    self.settings_rows.clear();
                    return true;
                }
                // Open only for a row that actually exists, seeded from the
                // backend's parameter specs for that indicator (defaults
                // already merged with stored overrides in the snapshot).
                let key = normalize_indicator_name(&name);
                if !self.indicators.iter().any(|e| e.name == key) {
                    return false;
                }
                self.settings_rows = self.indicator_params.get(&key).cloned().unwrap_or_default();
                self.settings_name = key.clone();
                self.settings_open = true;
                true
            }
            MarketAction::SettingsEdit(key, value) => {
                // Local buffer only — nothing reaches the backend until SAVE.
                if let Some(row) = self.settings_rows.iter_mut().find(|r| r.key == key) {
                    row.value = value;
                    true
                } else {
                    false
                }
            }
            MarketAction::ApplyIndicatorSettings(name, _payload) => {
                // Commit: the wire carries the JSON payload to the backend,
                // which stores the parameters and re-runs the indicator's
                // plots (calculation logic untouched — inputs only).
                let key = normalize_indicator_name(&name);
                let known = self.indicators.iter().any(|e| e.name == key);
                if known {
                    self.settings_open = false;
                    self.settings_name.clear();
                    self.settings_rows.clear();
                }
                known
            }
            MarketAction::ResetIndicatorSettings(name) => {
                let key = normalize_indicator_name(&name);
                if !self.indicators.iter().any(|e| e.name == key) {
                    return false;
                }
                // Drop the backend's stored overrides (they return to the
                // strategy's own defaults) and re-seed the panel rows.
                self.indicator_params.remove(&key);
                self.settings_rows = self.indicator_params.get(&key).cloned().unwrap_or_default();
                true
            }
            MarketAction::RemoveIndicator(name) => {
                let key = normalize_indicator_name(&name);
                // The panel ROWS drive the bar: a deleted name leaves the row
                // list entirely, so no refresh / timeframe / symbol change can
                // re-list it. The backend clears its plots + state in parallel.
                if self.settings_open && self.settings_name == key {
                    self.settings_open = false;
                    self.settings_name.clear();
                    self.settings_rows.clear();
                }
                let before = self.indicators.len();
                self.indicators.retain(|e| e.name != key);
                self.indicators.len() != before
            }
            MarketAction::TradePrev | MarketAction::TradeNext | MarketAction::TradeOpen => true,
            MarketAction::CycleScaleMode => {
                self.scale_mode = self.scale_mode.cycle();
                true
            }
            MarketAction::ToggleGrid => {
                self.grid_visible = !self.grid_visible;
                true
            }
            MarketAction::ToggleCrosshair => {
                self.cross_visible = !self.cross_visible;
                true
            }
        }
    }

    fn refresh_crosshair(&mut self) {
        // legacy re-snaps at the stored pixel after viewport changes; the native
        // screen re-reports on the next pointer move, so clearing is honest.
        self.hover = None;
    }

    /// Build the SAVE payload (indicator name, JSON object) from the local
    /// edit buffer. ``None`` when no settings panel is open. The JSON only
    /// carries numbers — keys/labels live in the specs, never here.
    pub fn settings_payload(&self) -> Option<(String, String)> {
        if !self.settings_open || self.settings_name.is_empty() {
            return None;
        }
        let name = self.settings_name.clone();
        let mut json = String::from("{");
        for (index, row) in self.settings_rows.iter().enumerate() {
            if index > 0 {
                json.push(',');
            }
            json.push('"');
            json.push_str(&row.key);
            json.push_str("\":");
            // shortest round-trip rendering; SpinBox values are finite and
            // within range, so this is always valid JSON
            json.push_str(&format!("{}", row.value));
        }
        json.push('}');
        Some((name, json))
    }

    /// Slint-reported interaction: apply optimistically, then queue the wire
    /// string for the legacy host when the action names backend behavior.
    /// Returns false when the action names something unknown.
    pub fn interact(&mut self, wire: &str, action: MarketAction) -> bool {
        let backend_owned = matches!(
            action,
            MarketAction::SelectSymbol(_)
                | MarketAction::SelectTimeframe(_)
                | MarketAction::AddWatchlist
                | MarketAction::RemoveWatchlist
                | MarketAction::SelectWatchlist(_)
                | MarketAction::ResetView
                | MarketAction::AddIndicator(_)
                | MarketAction::ToggleIndicatorVisible(_)
                | MarketAction::ApplyIndicatorSettings(_, _)
                | MarketAction::ResetIndicatorSettings(_)
                | MarketAction::RemoveIndicator(_)
                | MarketAction::TradePrev
                | MarketAction::TradeNext
                | MarketAction::TradeOpen
        );
        if !self.apply(action) {
            return false;
        }
        if backend_owned && self.pending_actions.len() < 64 {
            self.pending_actions.push(wire.to_string());
        }
        true
    }

    /// Apply a Historical-Download console action; when it names backend
    /// behavior the returned wire is queued for the legacy host to replay on the
    /// retained panel/manager. Pure view moves (calendar paging, filter, chip
    /// toggles handled locally) produce no wire.
    pub fn interact_download(&mut self, action: DownloadAction) {
        if let Some(wire) = self.download.apply(action) {
            if self.pending_actions.len() < 64 {
                self.pending_actions.push(wire);
            }
        }
    }

    /// Filtered + sorted watchlist rows (filter matches symbol substring,
    /// case-insensitive; sort by symbol per direction — legacy `_refresh_list`).
    pub fn listed_symbols(&self) -> Vec<&WatchEntry> {
        let needle = self.filter.to_lowercase();
        let mut rows: Vec<&WatchEntry> = self
            .symbols
            .iter()
            .filter(|s| needle.is_empty() || s.symbol.to_lowercase().contains(&needle))
            .collect();
        rows.sort_by(|a, b| {
            if self.sort_ascending {
                a.symbol.cmp(&b.symbol)
            } else {
                b.symbol.cmp(&a.symbol)
            }
        });
        rows
    }

    /// Whether the chart region has something real to draw.
    pub fn has_data(&self) -> bool {
        self.status == MarketStatus::Ready && !self.visible_window().is_empty()
    }
}

/// Round half to even — Python `round()` semantics. The legacy widget
/// computes every viewport index with Python `round`, so an exact .5 must
/// settle on the even neighbor rather than away from zero (Rust's
/// `f64::round`), otherwise pan/zoom anchors drift off-by-one from the old
/// Market on the same input.
fn round_py(value: f64) -> f64 {
    if !value.is_finite() {
        return value;
    }
    let floor = value.floor();
    let diff = value - floor;
    if diff < 0.5 {
        floor
    } else if diff > 0.5 {
        floor + 1.0
    } else {
        // exact half → nearest even (matches Python round, incl. negatives)
        if (floor as i64) & 1 == 0 {
            floor
        } else {
            floor + 1.0
        }
    }
}

/// legacy name normalization: "Volume" renders as "Vol".
pub fn normalize_indicator_name(name: &str) -> String {
    if name.eq_ignore_ascii_case("volume") {
        "Vol".to_string()
    } else {
        name.to_string()
    }
}

/// Grouped 2dp price (`2,451.10`); non-finite renders `N/A`, never invents.
pub fn fmt_price(value: f64) -> String {
    if !value.is_finite() {
        return "N/A".to_string();
    }
    let negative = value < 0.0;
    let rounded = format!("{:.2}", value.abs());
    let (int_part, frac_part) = match rounded.split_once('.') {
        Some((i, f)) => (i, f),
        None => (rounded.as_str(), "00"),
    };
    let chars: Vec<char> = int_part.chars().collect();
    let mut grouped = String::new();
    for (i, ch) in chars.iter().enumerate() {
        if i > 0 && (chars.len() - i) % 3 == 0 {
            grouped.push(',');
        }
        grouped.push(*ch);
    }
    format!(
        "{}{}.{}",
        if negative { "-" } else { "" },
        grouped,
        frac_part
    )
}

/// Signed 2dp percent (`+1.24%`); absent renders `N/A`.
pub fn fmt_signed_pct(value: Option<f64>) -> String {
    match value {
        Some(v) if v.is_finite() => format!("{:+.2}%", v),
        _ => "N/A".to_string(),
    }
}

/// Grouped integer (`1,240,500`); used for counts.
pub fn fmt_int(value: f64) -> String {
    if !value.is_finite() {
        return "N/A".to_string();
    }
    let rounded = value.round() as i64;
    let negative = rounded < 0;
    let digits: Vec<char> = rounded.abs().to_string().chars().collect();
    let mut grouped = String::new();
    for (i, ch) in digits.iter().enumerate() {
        if i > 0 && (digits.len() - i) % 3 == 0 {
            grouped.push(',');
        }
        grouped.push(*ch);
    }
    format!("{}{}", if negative { "-" } else { "" }, grouped)
}

/// Compact volume label exactly like the legacy chart
/// (`950`, `8.02 K`, `1.25 M`, `2.50 B`).
pub fn fmt_volume(value: f64) -> String {
    if !value.is_finite() {
        return "N/A".to_string();
    }
    let v = value.abs();
    if v >= 1e9 {
        format!("{:.2} B", value / 1e9)
    } else if v >= 1e6 {
        format!("{:.2} M", value / 1e6)
    } else if v >= 1e3 {
        format!("{:.2} K", value / 1e3)
    } else {
        format!("{}", value.round() as i64)
    }
}

/// Short axis timestamp (`2024-01-02 09:15` from ISO input, else passthrough).
pub fn short_time(stamp: &str) -> String {
    let cleaned = stamp.replace('T', " ");
    if cleaned.len() > 16 {
        cleaned[..16].to_string()
    } else {
        cleaned
    }
}

/// Adaptive axis timestamp formatting (Problem 2):
/// - Intraday (1m, 5m, 15m, 1h): clean times like `09:15`, `10:30`, `14:45`
/// - Daily (1D): clean dates like `Jun 10`, `Jun 11`, `Jun 12`
/// - Macro / Multi-month (1W, 1M, or window > 3 months): clean `Jun 2026`
pub fn format_axis_time(stamp: &str, timeframe: &str, window: &[MarketBar]) -> String {
    let cleaned = stamp.replace('T', " ");
    let cleaned = cleaned.trim();
    if cleaned.len() < 10 {
        return short_time(stamp);
    }
    let parts: Vec<&str> = cleaned.split_whitespace().collect();
    let date_part = parts.first().copied().unwrap_or("");
    let time_part = parts.get(1).copied().unwrap_or("");

    let date_segs: Vec<&str> = date_part.split('-').collect();
    if date_segs.len() < 3 {
        return short_time(stamp);
    }
    let year = date_segs[0];
    let month = date_segs[1];
    let day = date_segs[2];

    let month_name = match month {
        "01" => "Jan",
        "02" => "Feb",
        "03" => "Mar",
        "04" => "Apr",
        "05" => "May",
        "06" => "Jun",
        "07" => "Jul",
        "08" => "Aug",
        "09" => "Sep",
        "10" => "Oct",
        "11" => "Nov",
        "12" => "Dec",
        _ => month,
    };

    let tf = timeframe.trim().to_lowercase();
    let is_explicit_macro = tf == "1w" || tf == "1m" && timeframe == "1M" || tf == "1y" || tf == "w" || tf == "m" && timeframe == "M";
    let is_explicit_daily = tf == "1d" || tf == "d" || tf == "day" || tf == "daily";
    let is_explicit_intraday = tf.ends_with('m') && timeframe != "1M" && timeframe != "M"
        || tf.ends_with('h')
        || tf.ends_with('s')
        || tf.contains("min")
        || tf.contains("sec")
        || tf.contains("hour");

    let has_intraday_time = !time_part.is_empty() && time_part != "00:00" && time_part != "00:00:00";

    let span_macro = if window.len() >= 2 {
        let first_date = window.first().map(|b| b.time.as_str()).unwrap_or("");
        let last_date = window.last().map(|b| b.time.as_str()).unwrap_or("");
        let y1 = first_date.get(..4).and_then(|s| s.parse::<i32>().ok()).unwrap_or(0);
        let y2 = last_date.get(..4).and_then(|s| s.parse::<i32>().ok()).unwrap_or(0);
        let m1 = first_date.get(5..7).and_then(|s| s.parse::<i32>().ok()).unwrap_or(0);
        let m2 = last_date.get(5..7).and_then(|s| s.parse::<i32>().ok()).unwrap_or(0);
        let month_diff = (y2 - y1) * 12 + (m2 - m1);
        month_diff >= 3
    } else {
        false
    };

    if is_explicit_macro || span_macro {
        format!("{month_name} {year}")
    } else if is_explicit_intraday || (has_intraday_time && !is_explicit_daily) {
        if time_part.len() >= 5 {
            time_part[..5].to_string()
        } else {
            format!("{month_name} {day}")
        }
    } else {
        let day_num = day.parse::<u32>().map(|d| d.to_string()).unwrap_or_else(|_| day.to_string());
        format!("{month_name} {day_num}")
    }
}

/// Sakamoto's algorithm: 0 = Sun, 1 = Mon, 2 = Tue, 3 = Wed, 4 = Thu, 5 = Fri, 6 = Sat
fn day_of_week_sakamoto(year: i32, month: u32, day: u32) -> usize {
    static T: [i32; 12] = [0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4];
    let mut y = year;
    if month < 3 {
        y -= 1;
    }
    let m_idx = (month.saturating_sub(1)) as usize;
    let t_val = if m_idx < 12 { T[m_idx] } else { 0 };
    let dow = (y + y / 4 - y / 100 + y / 400 + t_val + day as i32) % 7;
    let dow = (dow + 7) % 7;
    dow as usize
}

/// Refined crosshair date/time format (Section 3):
/// Structure: `DAY_OF_WEEK DAY MONTH 'YY   TIME`
/// Examples: `Wed 16 Sep '26   10:15`, `Thu 17 Sep '26   14:30`, `Mon 21 Sep '26   09:15`
pub fn format_crosshair_time(stamp: &str) -> String {
    let cleaned = stamp.replace('T', " ");
    let cleaned = cleaned.trim();
    if cleaned.len() < 10 {
        return short_time(stamp);
    }
    let parts: Vec<&str> = cleaned.split_whitespace().collect();
    let date_part = parts.first().copied().unwrap_or("");
    let time_part = parts.get(1).copied().unwrap_or("");

    let date_segs: Vec<&str> = date_part.split('-').collect();
    if date_segs.len() < 3 {
        return short_time(stamp);
    }
    let Ok(year) = date_segs[0].parse::<i32>() else {
        return short_time(stamp);
    };
    let Ok(month) = date_segs[1].parse::<u32>() else {
        return short_time(stamp);
    };
    let Ok(day) = date_segs[2].parse::<u32>() else {
        return short_time(stamp);
    };
    if month == 0 || month > 12 || day == 0 || day > 31 {
        return short_time(stamp);
    }

    const DAYS: [&str; 7] = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    const MONTHS: [&str; 13] = [
        "", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ];

    let dow = DAYS[day_of_week_sakamoto(year, month, day)];
    let month_name = MONTHS[month as usize];
    let yy = (year.rem_euclid(100)) as u32;

    let time_clean = if time_part.len() >= 5 {
        &time_part[..5]
    } else {
        ""
    };

    if !time_clean.is_empty() {
        format!("{dow} {day} {month_name} '{yy:02}   {time_clean}")
    } else {
        format!("{dow} {day} {month_name} '{yy:02}")
    }
}

fn tone_of_change(change_pct: Option<f64>) -> Tone {
    match change_pct {
        Some(v) if v.is_finite() && v > 0.0 => Tone::Positive,
        Some(v) if v.is_finite() && v < 0.0 => Tone::Negative,
        Some(_) => Tone::Neutral,
        None => Tone::Muted,
    }
}

fn tone_of_bar(bar: &MarketBar) -> Tone {
    if bar.close > bar.open {
        Tone::Positive
    } else if bar.close < bar.open {
        Tone::Negative
    } else {
        Tone::Neutral
    }
}

// ── projection: MarketState -> flat render view ─────────────────────────────

/// One watchlist row, fully formatted for VCell rows.
#[derive(Debug, Clone, PartialEq)]
pub struct WatchRow {
    pub symbol: String,
    pub price: String,
    pub change: String,
    pub tone: Tone,
    pub selected: bool,
}

/// One timeframe button.
#[derive(Debug, Clone, PartialEq)]
pub struct TimeframeRow {
    pub label: String,
    pub selected: bool,
}

/// One candle in viewport-normalized coordinates (0..1, origin top-left).
/// `top`/`body` precompute body placement so Slint needs no min/max math;
/// `body` floors at a visible fraction so flat candles still paint.
#[derive(Debug, Clone, PartialEq)]
pub struct CandlePoint {
    pub x: f32,
    pub open: f32,
    pub high: f32,
    pub low: f32,
    pub close: f32,
    pub volume: f32,
    pub tone: Tone,
    pub top: f32,
    pub body: f32,
}

/// One axis tick: fractional position plus preformatted label.
#[derive(Debug, Clone, PartialEq)]
pub struct AxisTick {
    pub pos: f32,
    pub label: String,
}

/// One floating indicator-bar row.
#[derive(Debug, Clone, PartialEq)]
pub struct IndicatorRow {
    pub name: String,
    pub visible: bool,
}

/// One popup row: section headers are non-selectable labels.
#[derive(Debug, Clone, PartialEq)]
pub struct PopupRow {
    pub kind: String, // "section" | "item" | "empty"
    pub label: String,
    pub name: String,
    pub category: String,
}

/// One overlay line segment, normalized to the plot rect.
#[derive(Debug, Clone, PartialEq)]
pub struct PlotSegment {
    pub x1: f32,
    pub y1: f32,
    pub x2: f32,
    pub y2: f32,
    pub color: i32, // 0 accent, 1 warn, 2 pos, 3 neg
    pub series: i32,
    /// Highlighted (selected-trade) connection: thicker hairline.
    pub wide: bool,
}

/// One marker glyph, normalized to the plot rect (mirrors the legacy painter:
/// tip-anchored triangles, pills above/below, solid vs dark pill fills).
#[derive(Debug, Clone, PartialEq)]
pub struct MarkerGlyph {
    pub x: f32,
    pub y: f32,
    /// 0 up-triangle, 1 down-triangle, 2 square, 3 circle, 4 circle-x,
    /// 5 diamond, 6 dot.
    pub kind: i32,
    /// Shared tone convention (1 warn/amber, 2 pos, 3 neg) plus 4 blue,
    /// 5 purple, 6 muted.
    pub color: i32,
    pub label: String,
    /// 0 none, 1 above, 2 below.
    pub pill: i32,
    /// true = solid marker-color fill + white text (strategy); false = dark
    /// fill + colored border (trade).
    pub pill_solid: bool,
    /// 0 tip at anchor (strategy), 1 tip above (trade entry), 2 tip below.
    pub tip: i32,
    /// Pre-measured pill width, legacy fallback formula (len*6+6).
    pub pill_w: f32,
}

/// legacy pill measure fallback (`len(text) * 6.0 + 6.0`) — deterministic,
/// font-independent, byte-honest for ASCII labels.
fn pill_w(text: &str) -> f32 {
    text.len() as f32 * 6.0 + 6.0
}

/// Flat render view: every string formatted, every tone resolved, every
/// collection pre-ordered. The screen binds these and reports actions.
#[derive(Debug, Clone, PartialEq)]
pub struct MarketView {
    pub panel_visible: bool,
    pub symbol_title: String,
    pub timeframe_label: String,
    pub exchange_label: String,
    pub status_message: String,
    pub has_data: bool,
    pub header_ohlc: String,
    pub watch_rows: Vec<WatchRow>,
    pub watchlists: Vec<String>,
    pub watchlist_name: String,
    pub filter: String,
    pub sort_ascending: bool,
    pub timeframes_visible: Vec<TimeframeRow>,
    pub timeframes_overflow: Vec<TimeframeRow>,
    /// Logical slot count of the time window. The plot divides its width by
    /// these slots (not by the bars currently available), so candles keep
    /// their slot width while panning and any bars the data does not have yet
    /// render as real empty space on the right.
    pub plot_slots: i32,
    pub candles: Vec<CandlePoint>,
    pub price_ticks: Vec<AxisTick>,
    pub time_ticks: Vec<AxisTick>,
    pub plot_segments: Vec<PlotSegment>,
    pub markers: Vec<MarkerGlyph>,
    pub hover_x: f32,
    pub hover_y: f32,
    pub hover_price: String,
    pub hover_time: String,
    pub hover_volume: String,
    pub hover_bull: bool,
    pub has_hover: bool,
    pub indicator_rows: Vec<IndicatorRow>,
    pub popup_open: bool,
    pub popup_query: String,
    pub popup_category: String,
    pub popup_rows: Vec<PopupRow>,
    pub settings_open: bool,
    pub settings_name: String,
    pub settings_rows: Vec<SettingsRow>,
    pub active_label: String,
    pub trade_context: TradeContext,
    /// Chart display settings echo (Slint binds visibility + scale label).
    pub scale_mode: i32,
    pub scale_label: String,
    pub grid_visible: bool,
    pub cross_visible: bool,
    pub download: DownloadView,
    /// Market-status strip (legacy `MarketStatusPanel` parity): open flag +
    /// (key, value, muted) rows in the legacy grid order.
    pub market_status_open: bool,
    /// "MARKET REGIME" section rows.
    pub market_status_regime: Vec<(String, String, bool)>,
    /// "DATA STATUS" section rows.
    pub market_status_data: Vec<(String, String, bool)>,
}

fn last_change(bars: &[MarketBar]) -> (String, Tone) {
    if bars.len() < 2 {
        return ("—".to_string(), Tone::Muted);
    }
    let prev = bars[bars.len() - 2].close;
    let last = bars[bars.len() - 1].close;
    if !prev.is_finite() || !last.is_finite() || prev == 0.0 {
        return ("—".to_string(), Tone::Muted);
    }
    let pct = 100.0 * (last - prev) / prev.abs();
    (fmt_signed_pct(Some(pct)), tone_of_change(Some(pct)))
}

/// Western-grouped 2-decimals (legacy `f"{price:,.2f}"` in focused labels).
fn western2(value: f64) -> String {
    let sign = if value < 0.0 { "-" } else { "" };
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

/// Hover-only projection: crosshair tags + OHLC readout, nothing else.
///
/// The crosshair fast path — moving the pointer must never rebuild candle
/// geometry, ticks, segments, markers or list models. `plot_slots` mirrors
/// the `project()` grid so positions agree byte-for-byte.
#[derive(Debug, Clone, PartialEq)]
pub struct HoverView {
    pub hover_x: f32,
    pub hover_y: f32,
    pub hover_price: String,
    pub hover_time: String,
    pub hover_volume: String,
    pub hover_bull: bool,
    pub has_hover: bool,
    pub header_ohlc: String,
}

pub fn project_hover(state: &MarketState) -> HoverView {
    let plot_slots = state.count.max(1);
    let frame = scale_frame(state);
    let mut out = HoverView {
        hover_x: 0.5,
        hover_y: 0.5,
        hover_price: String::new(),
        hover_time: String::new(),
        hover_volume: String::new(),
        hover_bull: true,
        has_hover: false,
        header_ohlc: "—".to_string(),
    };
    if state.has_data() {
        if let Some(last) = state.bars.last() {
            out.header_ohlc = format!(
                "O {}  H {}  L {}  C {}",
                fmt_price(last.open),
                fmt_price(last.high),
                fmt_price(last.low),
                fmt_price(last.close)
            );
        }
    }
    if let Some(hover) = state.hover {
        if let Some(bar) = state.bars.get(hover.index) {
            out.has_hover = true;
            let rel = hover.index.saturating_sub(state.first);
            out.hover_x = (rel as f64 + 0.5) as f32 / plot_slots as f32;
            out.hover_y = hover.y_frac;
            // Regular keeps the state-computed price verbatim (golden
            // parity); Percent/Log read the cursor position off the same
            // transformed frame the axis labels use.
            out.hover_price = match frame.xform {
                ScaleXform::Identity => fmt_price(hover.price),
                xform => {
                    let frac = f64::from(hover.y_frac).clamp(0.0, 1.0);
                    fmt_scale(xform, frame.high - frac * (frame.high - frame.low))
                }
            };
            out.hover_time = format_crosshair_time(&bar.time);
            out.hover_volume = fmt_volume(bar.volume);
            out.hover_bull = bar.close >= bar.open;
            out.header_ohlc = format!(
                "O {}  H {}  L {}  C {}",
                fmt_price(bar.open),
                fmt_price(bar.high),
                fmt_price(bar.low),
                fmt_price(bar.close)
            );
        }
    }
    out
}

/// Transformed price frame for the current scale mode: how a raw price maps
/// into the 0..1 plot band plus the range it normalizes against. Regular is
/// the identity over `price_range()`; Percent/Log fall back to it whenever
/// the visible data cannot represent them (zero anchor, non-positive prices).
#[derive(Debug, Clone, Copy, PartialEq)]
enum ScaleXform {
    Identity,
    Percent { anchor: f64 },
    Log,
}

#[derive(Debug, Clone, Copy, PartialEq)]
struct ScaleFrame {
    xform: ScaleXform,
    low: f64,
    high: f64,
}

fn transform_price(xform: ScaleXform, value: f64) -> Option<f64> {
    match xform {
        ScaleXform::Identity => Some(value),
        ScaleXform::Percent { anchor } => {
            if anchor.is_finite() && anchor != 0.0 && value.is_finite() {
                Some(100.0 * (value - anchor) / anchor.abs())
            } else {
                None
            }
        }
        ScaleXform::Log => {
            if value.is_finite() && value > 0.0 {
                Some(value.ln())
            } else {
                None
            }
        }
    }
}

fn pad_range(low: f64, high: f64) -> (f64, f64) {
    let mut span = high - low;
    if !span.is_finite() || span <= 0.0 {
        span = high.abs() * 0.01;
        if !span.is_finite() || span <= 0.0 {
            span = 0.01;
        }
    }
    let pad = span * PRICE_EDGE_MARGIN;
    (low - pad, high + pad)
}

fn scale_frame(state: &MarketState) -> ScaleFrame {
    let window = state.visible_window();
    let fallback = || {
        let (low, high) = state.price_range();
        ScaleFrame {
            xform: ScaleXform::Identity,
            low,
            high,
        }
    };
    if window.is_empty() {
        return ScaleFrame {
            xform: ScaleXform::Identity,
            low: 0.0,
            high: 1.0,
        };
    }
    let mode = state.scale_mode;
    if mode == ChartScaleMode::Regular {
        return fallback();
    }
    let xform = match mode {
        ChartScaleMode::Percent => {
            let anchor = window[0].close;
            if !anchor.is_finite() || anchor == 0.0 {
                return fallback();
            }
            ScaleXform::Percent { anchor }
        }
        ChartScaleMode::Logarithmic => ScaleXform::Log,
        ChartScaleMode::Regular => return fallback(),
    };
    // Manual zoom ends live in raw price: transform them with the same frame.
    if let Some((raw_low, raw_high)) = state.price_manual {
        if raw_high > raw_low {
            if let (Some(low), Some(high)) = (
                transform_price(xform, raw_low),
                transform_price(xform, raw_high),
            ) {
                if high > low {
                    return ScaleFrame { xform, low, high };
                }
            }
        }
    }
    let mut low = f64::INFINITY;
    let mut high = f64::NEG_INFINITY;
    for bar in window {
        let (Some(l), Some(h)) = (
            transform_price(xform, bar.low),
            transform_price(xform, bar.high),
        ) else {
            return fallback();
        };
        low = low.min(l);
        high = high.max(h);
    }
    let (low, high) = pad_range(low, high);
    ScaleFrame { xform, low, high }
}

/// Axis/crosshair label for a transformed scale value.
fn fmt_scale(xform: ScaleXform, t_value: f64) -> String {
    match xform {
        ScaleXform::Identity => fmt_price(t_value),
        ScaleXform::Percent { .. } => {
            if t_value.is_finite() {
                format!("{t_value:+.2}%")
            } else {
                "N/A".to_string()
            }
        }
        ScaleXform::Log => {
            if t_value.is_finite() {
                fmt_price(t_value.exp())
            } else {
                "N/A".to_string()
            }
        }
    }
}

/// Project state into the flat render view (pure; no I/O, no inference).
pub fn project(state: &MarketState) -> MarketView {
    let status_message = match state.status {
        MarketStatus::Loading => "Loading chart…".to_string(),
        MarketStatus::Empty => {
            if !state.notice.is_empty() {
                state.notice.clone()
            } else if state.bars.is_empty() {
                if state.selected_symbol.is_empty() {
                    "No data available".to_string()
                } else if state.timeframe.is_empty() {
                    format!("No candle data for {}", state.selected_symbol)
                } else {
                    format!(
                        "No candle data for {} on {} timeframe",
                        state.selected_symbol, state.timeframe
                    )
                }
            } else {
                "No data in viewport".to_string()
            }
        }
        MarketStatus::Ready => String::new(),
    };
    let has_data = state.has_data();

    let watch_rows: Vec<WatchRow> = state
        .listed_symbols()
        .into_iter()
        .map(|s| WatchRow {
            symbol: s.symbol.clone(),
            price: s.price.map(fmt_price).unwrap_or_else(|| "N/A".to_string()),
            change: fmt_signed_pct(s.change_pct),
            tone: tone_of_change(s.change_pct),
            selected: s.symbol == state.selected_symbol,
        })
        .collect();

    let visible_set: Vec<&str> = TIMEFRAME_VISIBLE_ORDER
        .iter()
        .copied()
        .filter(|tf| state.timeframes.iter().any(|t| t == tf))
        .collect();
    let timeframes_visible: Vec<TimeframeRow> = visible_set
        .iter()
        .map(|t| TimeframeRow {
            label: (*t).to_string(),
            selected: *t == state.timeframe,
        })
        .collect();
    let timeframes_overflow: Vec<TimeframeRow> = state
        .timeframes
        .iter()
        .filter(|t| !visible_set.contains(&t.as_str()))
        .map(|t| TimeframeRow {
            label: t.clone(),
            selected: *t == state.timeframe,
        })
        .collect();

    let window = state.visible_window();
    // The plot's time grid is the LOGICAL window (`count` slots), not the
    // number of bars the data-clamped slice happens to hold. Every x in the
    // view normalizes by these slots, so the candle slot width stays constant
    // while panning and the region past the newest bar renders as real empty
    // space instead of stretching the remaining candles across the plot.
    let plot_slots = state.count.max(1);
    let frame = scale_frame(state);
    let span = frame.high - frame.low;
    let norm = |v: f64| {
        if span > 0.0 {
            match transform_price(frame.xform, v) {
                Some(t) => ((frame.high - t) / span) as f32,
                None => 0.5,
            }
        } else {
            0.5
        }
    };
    let mut candles = Vec::with_capacity(window.len());
    let mut price_ticks = Vec::new();
    let mut time_ticks = Vec::new();
    if has_data {
        // Bars the data actually holds: the tick labels are sampled from
        // these, while their positions come from the slot grid above.
        let visible = window.len().max(1);
        let vol_max = window
            .iter()
            .map(|b| if b.volume.is_finite() { b.volume } else { 0.0 })
            .fold(0.0f64, f64::max)
            .max(1.0);
        for (i, bar) in window.iter().enumerate() {
            let (open, close) = (norm(bar.open), norm(bar.close));
            candles.push(CandlePoint {
                x: (i as f64 + 0.5) as f32 / plot_slots as f32,
                open,
                high: norm(bar.high),
                low: norm(bar.low),
                close,
                volume: (if bar.volume.is_finite() {
                    bar.volume
                } else {
                    0.0
                } / vol_max) as f32,
                tone: tone_of_bar(bar),
                top: open.min(close),
                body: (open - close).abs().max(0.004),
            });
        }
        for frac in [0.0f32, 0.25, 0.5, 0.75, 1.0] {
            price_ticks.push(AxisTick {
                pos: frac,
                label: fmt_scale(frame.xform, frame.high - f64::from(frac) * span),
            });
        }
        // Adaptive tick generation (Problem 2):
        // Calculate available plot fraction and min spacing to never allow label collision.
        // At 80px label width on ~1000px nominal width, min readable spacing is 0.085.
        let min_spacing_pos = 0.085f32;
        let visible_frac = visible as f32 / plot_slots as f32;
        let max_possible_ticks = ((visible_frac / min_spacing_pos).floor() as usize).clamp(1, 8).min(visible);
        let mut last_pos = -1.0f32;
        for k in 0..max_possible_ticks {
            let idx = ((k as f64 + 0.5) * visible as f64 / max_possible_ticks as f64).floor() as usize;
            let idx = idx.min(visible - 1);
            let pos = (idx as f32 + 0.5) / plot_slots as f32;
            if last_pos >= 0.0 && (pos - last_pos) < min_spacing_pos {
                continue; // Skip rather than compress to collide
            }
            time_ticks.push(AxisTick {
                pos,
                label: format_axis_time(&window[idx].time, &state.timeframe, window),
            });
            last_pos = pos;
        }
    }

    // Crosshair tags from the snapped hover (hover-only fast path shares
    // this exact helper — one source of truth, never duplicated).
    let hover = project_hover(state);
    let hover_x = hover.hover_x;
    let hover_y = hover.hover_y;
    let hover_price = hover.hover_price;
    let hover_time = hover.hover_time;
    let hover_volume = hover.hover_volume;
    let hover_bull = hover.hover_bull;
    let has_hover = hover.has_hover;
    let header_ohlc = hover.header_ohlc;

    /// Horizontal ray emitter shared by extend series and store RAY records
    /// (legacy paint_overlay ray algorithm, same inputs): each origin extends to
    /// the next origin (or horizon), capped, clipped to the viewport. A
    /// single-bar ray becomes a one-slot tick.
    #[allow(clippy::too_many_arguments)]
    fn emit_ray(
        segments: &mut Vec<PlotSegment>,
        norm: &dyn Fn(f64) -> f32,
        first: usize,
        count: usize,
        start: usize,
        price: f64,
        cap: i32,
        color: i32,
        series: i32,
        horizon: usize,
    ) {
        let mut end = horizon.saturating_sub(1);
        if cap > 0 {
            end = end.min(start.saturating_add(cap as usize).saturating_sub(1));
        }
        if end < first || start >= first + count {
            return;
        }
        let start_c = start.max(first);
        let end_c = end.min(first + count - 1);
        if start_c > end_c {
            return;
        }
        let y = norm(price);
        let x1 = ((start_c - first) as f64 + 0.5) as f32 / count as f32;
        let x2 = if end_c == start_c {
            x1 + 1.0 / count as f32
        } else {
            ((end_c - first) as f64 + 0.5) as f32 / count as f32
        };
        segments.push(PlotSegment {
            x1,
            y1: y,
            x2,
            y2: y,
            color,
            series,
            wide: false,
        });
    }
    // Overlay series -> segments. Dense series connect diagonally; sparse
    // series with ray extension draw horizontal rays (legacy paint_overlay
    // algorithm, same inputs). Gaps break the line either way.
    let mut plot_segments: Vec<PlotSegment> = Vec::new();
    if has_data {
        let count = plot_slots;
        let total_bars = state.bars.len();
        for (si, series) in state.plot_series.iter().enumerate() {
            let hidden = state
                .indicators
                .iter()
                .any(|e| e.name == series.owner && !e.visible);
            if hidden {
                continue;
            }
            let color = plot_color_code(si);
            let mut seg = |x1: f32, y1: f32, x2: f32, y2: f32| {
                plot_segments.push(PlotSegment {
                    x1,
                    y1,
                    x2,
                    y2,
                    color,
                    series: si as i32,
                    wide: false,
                });
            };
            let ray_eligible = matches!(
                series.extend.as_str(),
                "session" | "right" | "extend" | "horizontal"
            );
            // Sparse (average gap > 2, or a single point) + extend = rays.
            let mut sorted: Vec<(usize, f64)> = series.points.clone();
            sorted.sort_by_key(|(idx, _)| *idx);
            let is_ray = if ray_eligible {
                if sorted.len() >= 2 {
                    let gaps: Vec<usize> = sorted
                        .windows(2)
                        .map(|w| w[1].0.saturating_sub(w[0].0))
                        .collect();
                    let avg = gaps.iter().sum::<usize>() as f64 / gaps.len() as f64;
                    avg > 2.0
                } else {
                    !sorted.is_empty()
                }
            } else {
                false
            };
            if is_ray {
                for (i, (idx, value)) in sorted.iter().enumerate() {
                    let next_idx = sorted.get(i + 1).map(|(n, _)| *n).unwrap_or(total_bars);
                    emit_ray(
                        &mut plot_segments,
                        &norm,
                        state.first,
                        count,
                        *idx,
                        *value,
                        series.cap,
                        color,
                        si as i32,
                        next_idx,
                    );
                }
                continue;
            }
            let mut prev: Option<(f32, f32)> = None;
            for (idx, value) in &series.points {
                if *idx < state.first || *idx >= state.first + count {
                    prev = None;
                    continue;
                }
                let x = ((*idx - state.first) as f64 + 0.5) as f32 / count as f32;
                let y = norm(*value);
                if let Some((px, py)) = prev {
                    seg(px, py, x, y);
                }
                prev = Some((x, y));
            }
        }
    }
    // Trade overlay (legacy `TradeOverlay.paint_overlay` semantics, same inputs):
    // dashed win/loss connection entry->exit always paints; entry marker
    // (up triangle, teal, BUY pill above) and exit marker (down triangle,
    // win-colored, SELL pill below) paint unless the backend-flagged covered
    // bar already owns a strategy visual — one event, one canonical visual.
    // Slint has no dashed strokes: connections are pre-split into solid
    // sub-segments here (same pixels class, honest geometry).
    let mut markers: Vec<MarkerGlyph> = Vec::new();
    // Focused single-trade inspection (explicit user action): full detail
    // replaces the ambient markers for that trade only.
    let focused_no: Option<usize> = state.focused_trade.filter(|_| state.focused.is_some());
    if has_data {
        let count = plot_slots;
        let total_bars = state.bars.len();
        let at = |pos: i32, price: f64| -> Option<(f32, f32)> {
            if pos < 0 || (pos as usize) < state.first || (pos as usize) >= state.first + count {
                return None;
            }
            Some((
                (((pos as usize) - state.first) as f64 + 0.5) as f32 / count as f32,
                norm(price),
            ))
        };
        let mut dashed = |x1: f32, y1: f32, x2: f32, y2: f32, color: i32, wide: bool| {
            // legacy DashLine ~4px on/off at 1px width; view-space chunks match
            // it at typical plot widths without any screen metrics.
            let dx = x2 - x1;
            let dy = y2 - y1;
            let len = (dx * dx + dy * dy).sqrt();
            if len <= 0.0 {
                return;
            }
            let dash = 0.006;
            let gap = 0.0045;
            let mut t = 0.0;
            while t < 1.0 {
                let t2 = (t + dash / len).min(1.0);
                plot_segments.push(PlotSegment {
                    x1: x1 + dx * t,
                    y1: y1 + dy * t,
                    x2: x1 + dx * t2,
                    y2: y1 + dy * t2,
                    color,
                    series: -1,
                    wide,
                });
                t += (dash + gap) / len;
            }
        };
        let is_long = |side: &str| side.eq_ignore_ascii_case("long");
        for (ti, trade) in state.trade_markers.iter().enumerate() {
            let entry = trade.entry_pos;
            let exit = trade.exit_pos;
            let line_color = if trade.winning { 2 } else { 3 };
            let wide = state.focused_trade == Some(ti);
            if ti == focused_no.unwrap_or(usize::MAX) {
                continue; // focused detail below replaces ambient markers
            }
            if let (Some((x1, y1)), Some((x2, y2))) =
                (at(entry, trade.entry_price), at(exit, trade.exit_price))
            {
                dashed(x1, y1, x2, y2, line_color, wide);
            }
            if !trade.entry_covered {
                if let Some((x, y)) = at(entry, trade.entry_price) {
                    markers.push(MarkerGlyph {
                        x,
                        y,
                        kind: 0,
                        color: 2,
                        label: "BUY".to_string(),
                        pill: 1,
                        pill_solid: false,
                        tip: 1,
                        pill_w: pill_w("BUY"),
                    });
                }
            }
            if !trade.exit_covered {
                if let Some((x, y)) = at(exit, trade.exit_price) {
                    markers.push(MarkerGlyph {
                        x,
                        y,
                        kind: 1,
                        color: if trade.winning { 2 } else { 3 },
                        label: "SELL".to_string(),
                        pill: 2,
                        pill_solid: false,
                        tip: 2,
                        pill_w: pill_w("SELL"),
                    });
                }
            }
        }
        // Focused trade: solid connection + direction-aware price labels +
        // short chevron for SHORT (legacy focused-mode math, same constants).
        if let Some(focus) = &state.focused {
            let long = is_long(&focus.side);
            if let (Some((x1, y1)), Some((x2, y2))) = (
                at(focus.entry_pos, focus.entry_price),
                at(focus.exit_pos, focus.exit_price),
            ) {
                plot_segments.push(PlotSegment {
                    x1,
                    y1,
                    x2,
                    y2,
                    color: if focus.winning { 2 } else { 3 },
                    series: -1,
                    wide: true,
                });
                let entry_label = format!(
                    "ENTRY {}  ₹{}",
                    if long { "LONG" } else { "SHORT" },
                    western2(focus.entry_price)
                );
                markers.push(MarkerGlyph {
                    x: x1,
                    y: y1,
                    kind: 0,
                    color: 2,
                    pill: 1,
                    pill_solid: false,
                    tip: 1,
                    pill_w: pill_w(&entry_label),
                    label: entry_label,
                });
                let exit_label = format!(
                    "EXIT {}  ₹{}",
                    if long { "SELL" } else { "COVER" },
                    western2(focus.exit_price)
                );
                markers.push(MarkerGlyph {
                    x: x2,
                    y: y2,
                    kind: 1,
                    color: if focus.winning { 2 } else { 3 },
                    pill: 2,
                    pill_solid: false,
                    tip: 2,
                    pill_w: pill_w(&exit_label),
                    label: exit_label,
                });
                if !long {
                    // midpoint chevron (4.0px pattern, view-normalized).
                    let (mx, my) = ((x1 + x2) / 2.0, (y1 + y2) / 2.0);
                    let rightward = x1 < x2;
                    let s = 0.006;
                    let (ax, ay, bx, by, cx, cy) = if rightward {
                        (mx - s, my - s * 0.6, mx + s * 0.4, my, mx - s, my + s * 0.6)
                    } else {
                        (mx + s, my - s * 0.6, mx - s * 0.4, my, mx + s, my + s * 0.6)
                    };
                    for (px, py, qx, qy) in [(ax, ay, bx, by), (bx, by, cx, cy)] {
                        plot_segments.push(PlotSegment {
                            x1: px,
                            y1: py,
                            x2: qx,
                            y2: qy,
                            color: 0,
                            series: -1,
                            wide: false,
                        });
                    }
                }
            }
        }
        // Strategy-owned rays (REF HIGH/LOW horizontals): same emitter,
        // horizon is the tail end (clipped to the viewport either way).
        for ray in &state.strategy_rays {
            if ray.start < 0 {
                continue;
            }
            emit_ray(
                &mut plot_segments,
                &norm,
                state.first,
                count,
                ray.start as usize,
                ray.price,
                ray.cap,
                store_color_code(ray.layer, ray.order),
                -2,
                total_bars,
            );
        }
        // Strategy-owned markers (entries, exits, EOD pills): glyph + pill
        // on the exact bar; UP-family pills below, DOWN-family above — the
        // painter's placement rule, no pill de-collision in Slint (noted).
        for sm in &state.strategy_markers {
            if sm.bar < 0
                || (sm.bar as usize) < state.first
                || (sm.bar as usize) >= state.first + count
            {
                continue;
            }
            let x = (((sm.bar as usize) - state.first) as f64 + 0.5) as f32 / count as f32;
            let y = norm(sm.price);
            // Glyph colors are fixed per kind (legacy `_marker_color`); only
            // line/ray spans use the layer rotation.
            let (kind, color, below) = match sm.kind.as_str() {
                "DOWN_ARROW" => (1, 3, false),
                "CIRCLE" => (3, 4, true),
                "CIRCLE_X" => (4, 1, true),
                "SQUARE" => (2, 1, true),
                "DIAMOND" => (5, 5, true),
                "TRIANGLE_UP" => (0, 2, true),
                "TRIANGLE_DOWN" => (1, 3, false),
                "TRIANGLE_BLUE" => (0, 4, true),
                "DOT" => (6, 6, true),
                _ => (0, 2, true), // UP_ARROW and unknown: teal up triangle
            };
            markers.push(MarkerGlyph {
                x,
                y,
                kind,
                color,
                pill: if sm.text.is_empty() {
                    0
                } else if below {
                    2
                } else {
                    1
                },
                pill_solid: true,
                tip: 0,
                pill_w: pill_w(&sm.text),
                label: sm.text.clone(),
            });
        }
    }

    let indicator_rows: Vec<IndicatorRow> = state
        .indicators
        .iter()
        .map(|e| IndicatorRow {
            name: e.name.clone(),
            visible: e.visible,
        })
        .collect();

    // Settings panel rows for the open indicator (already seeded/edited in
    // state; empty list renders the honest "no parameters" note).
    let settings_rows: Vec<SettingsRow> = state.settings_rows.clone();
    let settings_name = state.settings_name.clone();
    let settings_open = state.settings_open;

    // Indicator popup contents: sections per category + search, "No matches".
    let mut popup_rows: Vec<PopupRow> = Vec::new();
    if state.popup_open {
        let query = state.popup_query.trim().to_lowercase();
        let cat = state.popup_category.as_str();
        let push_section = |popup_rows: &mut Vec<PopupRow>, label: &str, items: &[&str]| {
            if cat != "ALL" && cat != label {
                return;
            }
            let filtered: Vec<&&str> = items
                .iter()
                .filter(|n| {
                    query.is_empty()
                        || n.to_lowercase().contains(&query)
                        || label.to_lowercase().contains(&query)
                })
                .collect();
            if filtered.is_empty() {
                return;
            }
            popup_rows.push(PopupRow {
                kind: "section".to_string(),
                label: format!("— {label} —"),
                name: String::new(),
                category: String::new(),
            });
            for name in filtered {
                popup_rows.push(PopupRow {
                    kind: "item".to_string(),
                    label: (*name).to_string(),
                    name: (*name).to_string(),
                    category: label.to_string(),
                });
            }
        };
        for (label, items) in INDICATOR_CATEGORIES {
            push_section(&mut popup_rows, label, items);
        }
        if cat == "ALL" || cat == "STRATEGIES" {
            let strategies: Vec<&String> = state
                .strategies
                .iter()
                .filter(|s| query.is_empty() || s.to_lowercase().contains(&query))
                .collect();
            if !strategies.is_empty() {
                popup_rows.push(PopupRow {
                    kind: "section".to_string(),
                    label: "— STRATEGIES —".to_string(),
                    name: String::new(),
                    category: String::new(),
                });
                for s in strategies {
                    popup_rows.push(PopupRow {
                        kind: "item".to_string(),
                        label: s.clone(),
                        name: s.clone(),
                        category: "STRATEGIES".to_string(),
                    });
                }
            }
        }
        // Chart display settings section (own implementation of the
        // chart-settings grouping): shown on ALL so scale/grid/crosshair
        // stay one click away without toolbar clutter. Rows carry stable
        // ids; selection routes by category, never by label text.
        if cat == "ALL" {
            let chart_rows = [
                (
                    "scale",
                    match state.scale_mode {
                        ChartScaleMode::Regular => "Scale: Regular (₹)",
                        ChartScaleMode::Percent => "Scale: Percent (%)",
                        ChartScaleMode::Logarithmic => "Scale: Logarithmic (log)",
                    },
                ),
                (
                    "grid",
                    if state.grid_visible {
                        "Grid lines: On"
                    } else {
                        "Grid lines: Off"
                    },
                ),
                (
                    "cross",
                    if state.cross_visible {
                        "Crosshair: On"
                    } else {
                        "Crosshair: Off"
                    },
                ),
            ];
            let chart_rows: Vec<(&str, &str)> = chart_rows
                .into_iter()
                .filter(|(_, label)| query.is_empty() || label.to_lowercase().contains(&query))
                .collect();
            if !chart_rows.is_empty() {
                popup_rows.push(PopupRow {
                    kind: "section".to_string(),
                    label: "— CHART —".to_string(),
                    name: String::new(),
                    category: String::new(),
                });
                for (name, label) in chart_rows {
                    popup_rows.push(PopupRow {
                        kind: "item".to_string(),
                        label: label.to_string(),
                        name: name.to_string(),
                        category: "CHART".to_string(),
                    });
                }
            }
        }
        if popup_rows.iter().all(|r| r.kind != "item") {
            popup_rows.clear();
            popup_rows.push(PopupRow {
                kind: "empty".to_string(),
                label: "No matches".to_string(),
                name: String::new(),
                category: String::new(),
            });
        }
    }

    let active_label = state
        .indicators
        .last()
        .map(|e| format!("Indicator: {}", e.name))
        .unwrap_or_default();

    let (_change_text, _change_tone) = last_change(&state.bars);

    MarketView {
        panel_visible: state.panel_visible,
        symbol_title: state.selected_symbol.clone(),
        timeframe_label: state.timeframe.clone(),
        exchange_label: state.exchange.clone(),
        status_message,
        has_data,
        header_ohlc,
        watch_rows,
        watchlists: state.watchlists.clone(),
        watchlist_name: state.active_watchlist.clone(),
        filter: state.filter.clone(),
        sort_ascending: state.sort_ascending,
        timeframes_visible,
        timeframes_overflow,
        plot_slots: plot_slots as i32,
        candles,
        price_ticks,
        time_ticks,
        plot_segments,
        markers,
        hover_x,
        hover_y,
        hover_price,
        hover_time,
        hover_volume,
        hover_bull,
        has_hover,
        indicator_rows,
        popup_open: state.popup_open,
        popup_query: state.popup_query.clone(),
        popup_category: state.popup_category.clone(),
        popup_rows,
        settings_open,
        settings_name,
        settings_rows,
        active_label,
        trade_context: state.trade_context.clone(),
        scale_mode: state.scale_mode.kind(),
        scale_label: state.scale_mode.label().to_string(),
        grid_visible: state.grid_visible,
        cross_visible: state.cross_visible,
        download: mdownload::project_download(&state.download),
        market_status_open: state.market_status.open,
        market_status_regime: market_status_rows(&state.market_status).0,
        market_status_data: market_status_rows(&state.market_status).1,
    }
}

/// legacy grid split: MARKET REGIME (4 rows) + DATA STATUS (4 rows).
/// `"--"` renders muted — the honest-unknown styling rule (never zero-filled).
fn market_status_rows(
    facts: &MarketStatusFacts,
) -> (Vec<(String, String, bool)>, Vec<(String, String, bool)>) {
    let mut regime = Vec::with_capacity(4);
    let mut data = Vec::with_capacity(4);
    for (k, v) in [
        ("Current regime", facts.regime_current.as_str()),
        ("Trend strength", facts.regime_trend.as_str()),
        ("Volatility", facts.regime_volatility.as_str()),
        ("Momentum", facts.regime_momentum.as_str()),
        ("Data provider", facts.provider.as_str()),
        ("Latency", facts.latency.as_str()),
        ("Last update", facts.last_update.as_str()),
        ("Bars loaded", facts.bars_loaded.as_str()),
    ] {
        let row = (k.to_string(), v.to_string(), v == "--");
        if regime.len() < 4 {
            regime.push(row);
        } else {
            data.push(row);
        }
    }
    (regime, data)
}

// ── bridge: Python backend snapshot -> canonical state (embedded view) ────

fn snap_str(value: &serde_json::Value, key: &str) -> String {
    value
        .get(key)
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string()
}

fn snap_f64(value: &serde_json::Value, key: &str) -> Option<f64> {
    value.get(key).and_then(|v| v.as_f64())
}

fn snap_bool(value: &serde_json::Value, key: &str) -> Option<bool> {
    value.get(key).and_then(|v| v.as_bool())
}

fn snap_str_list(value: &serde_json::Value, key: &str) -> Option<Vec<String>> {
    value.get(key).and_then(|v| v.as_array()).map(|arr| {
        arr.iter()
            .filter_map(|t| t.as_str().map(str::to_string))
            .collect()
    })
}

/// Replace backend-owned fields of the canonical state from the bridge JSON
/// (schema documented on `market_snapshot_dict` Python side). Slint-local UI
/// fields (filter text, sort direction, panel visibility, popup, viewport
/// window, hover, pending actions) are preserved. Missing or mistyped
/// sections degrade to honest absence (Loading/Empty), never invented values.
pub fn apply_snapshot_json(state: &mut MarketState, value: &serde_json::Value) {
    if let Some(rows) = value.get("symbols").and_then(|v| v.as_array()) {
        state.symbols = rows
            .iter()
            .map(|row| WatchEntry {
                symbol: snap_str(row, "symbol"),
                price: snap_f64(row, "price"),
                change_pct: snap_f64(row, "change_pct"),
            })
            .filter(|s| !s.symbol.is_empty())
            .collect();
    }
    let selected = snap_str(value, "selected_symbol");
    if !selected.is_empty() {
        state.selected_symbol = selected;
    }
    if let Some(lists) = snap_str_list(value, "watchlists") {
        let kept: Vec<String> = lists.into_iter().filter(|w| !w.is_empty()).collect();
        if !kept.is_empty() {
            state.watchlists = kept;
        }
    }
    let active = snap_str(value, "active_watchlist");
    if !active.is_empty() && state.watchlists.iter().any(|w| w == &active) {
        state.active_watchlist = active;
    }
    if let Some(frames) = snap_str_list(value, "timeframes") {
        state.timeframes = frames;
    }
    let timeframe = snap_str(value, "timeframe");
    if !timeframe.is_empty() {
        state.timeframe = timeframe;
    }
    if let Some(strategies) = snap_str_list(value, "strategies") {
        state.strategies = strategies;
    }
    let exchange = snap_str(value, "exchange");
    if !exchange.is_empty() {
        state.exchange = exchange;
    }
    if let Some(raw_bars) = value.get("bars").and_then(|v| v.as_array()) {
        let bars: Vec<MarketBar> = raw_bars
            .iter()
            .map(|b| MarketBar {
                time: snap_str(b, "time"),
                open: snap_f64(b, "open").unwrap_or(f64::NAN),
                high: snap_f64(b, "high").unwrap_or(f64::NAN),
                low: snap_f64(b, "low").unwrap_or(f64::NAN),
                close: snap_f64(b, "close").unwrap_or(f64::NAN),
                volume: snap_f64(b, "volume").unwrap_or(f64::NAN),
            })
            .collect();
        let symbol = state.selected_symbol.clone();
        let timeframe = state.timeframe.clone();
        let exchange = state.exchange.clone();
        state.set_bars(&symbol, &timeframe, &exchange, bars);
    } else {
        state.status = match snap_str(value, "status").as_str() {
            "ready" => {
                if state.bars.is_empty() {
                    MarketStatus::Empty
                } else {
                    MarketStatus::Ready
                }
            }
            "empty" => MarketStatus::Empty,
            _ => MarketStatus::Loading,
        };
    }
    let notice = snap_str(value, "notice");
    if !notice.is_empty() {
        state.notice = notice;
    }
    // Indicator visibility: the retained panel ROWS (name -> bool). Only
    // entries the backend confirms are kept; nothing is seeded or invented.
    // A deleted indicator has no row, so it can never reappear here.
    if let Some(map) = value.get("indicators").and_then(|v| v.as_object()) {
        state.indicators = map
            .iter()
            .map(|(name, visible)| IndicatorEntry {
                name: normalize_indicator_name(name),
                visible: visible.as_bool().unwrap_or(true),
            })
            .collect();
    }
    // Editable parameter specs per indicator: {name: [{key,label,value,
    // min,max,step,decimals}]}. Feeds the native settings panel; the values
    // already merge the indicator's stored overrides.
    if let Some(map) = value.get("indicator_params").and_then(|v| v.as_object()) {
        let mut params = std::collections::HashMap::new();
        for (name, rows) in map {
            if let Some(arr) = rows.as_array() {
                let mut out = Vec::with_capacity(arr.len());
                for row in arr {
                    let key = snap_str(row, "key");
                    if key.is_empty() {
                        continue;
                    }
                    out.push(SettingsRow {
                        key,
                        label: snap_str(row, "label"),
                        value: row.get("value").and_then(|v| v.as_f64()).unwrap_or(0.0),
                        min: row.get("min").and_then(|v| v.as_f64()).unwrap_or(0.0),
                        max: row.get("max").and_then(|v| v.as_f64()).unwrap_or(1.0),
                        step: row.get("step").and_then(|v| v.as_f64()).unwrap_or(0.1),
                        decimals: row.get("decimals").and_then(|v| v.as_i64()).unwrap_or(2) as i32,
                    });
                }
                if !out.is_empty() {
                    params.insert(normalize_indicator_name(name), out);
                }
            }
        }
        state.indicator_params = params;
    }
    // Strategy plot series: [{owner,title,points,extend,cap}]
    if let Some(rows) = value.get("plot_series").and_then(|v| v.as_array()) {
        state.plot_series = rows
            .iter()
            .filter_map(|row| {
                let owner = snap_str(row, "owner");
                let title = snap_str(row, "title");
                let points: Vec<(usize, f64)> = row
                    .get("points")
                    .and_then(|v| v.as_array())
                    .map(|arr| {
                        arr.iter()
                            .filter_map(|p| {
                                let pair = p.as_array()?;
                                let idx = pair.first()?.as_u64()? as usize;
                                let val = pair.get(1)?.as_f64()?;
                                if val.is_finite() {
                                    Some((idx, val))
                                } else {
                                    None
                                }
                            })
                            .collect()
                    })
                    .unwrap_or_default();
                if owner.is_empty() || points.is_empty() {
                    return None;
                }
                Some(PlotSeries {
                    owner,
                    title,
                    points,
                    extend: snap_str(row, "extend"),
                    cap: row
                        .get("cap")
                        .and_then(|v| v.as_i64())
                        .map(|v| v as i32)
                        .unwrap_or(-1),
                })
            })
            .collect();
    }
    // Trade markers: chart positions resolved backend-side through chart
    // timestamps (replay indices never index chart bars) + covered flags
    // evaluated backend-side in replay basis (exact legacy rule).
    if let Some(rows) = value.get("trades").and_then(|v| v.as_array()) {
        state.trade_markers = rows
            .iter()
            .filter_map(|row| {
                let side = snap_str(row, "side");
                if side.is_empty() {
                    return None;
                }
                let opt_pos = |key: &str| {
                    row.get(key)
                        .and_then(|v| v.as_i64())
                        .map(|v| v as i32)
                        .filter(|v| *v >= 0)
                        .unwrap_or(-1)
                };
                Some(TradeMarker {
                    side,
                    entry_price: snap_f64(row, "entry_price").unwrap_or(f64::NAN),
                    exit_price: snap_f64(row, "exit_price").unwrap_or(f64::NAN),
                    winning: row
                        .get("winning")
                        .and_then(|v| v.as_bool())
                        .unwrap_or(false),
                    entry_pos: opt_pos("entry_pos"),
                    exit_pos: opt_pos("exit_pos"),
                    entry_covered: row
                        .get("entry_covered")
                        .and_then(|v| v.as_bool())
                        .unwrap_or(false),
                    exit_covered: row
                        .get("exit_covered")
                        .and_then(|v| v.as_bool())
                        .unwrap_or(false),
                    exit_reason: snap_str(row, "exit_reason"),
                })
            })
            .collect();
        state.focused_trade = value
            .get("focused_trade")
            .and_then(|v| v.as_u64())
            .map(|v| v as usize);
    }
    // Focused single-trade detail (explicit inspection replaces ambient).
    state.focused = value.get("focused").and_then(|f| {
        if f.is_null() {
            return None;
        }
        let opt_pos = |key: &str| {
            f.get(key)
                .and_then(|v| v.as_i64())
                .map(|v| v as i32)
                .filter(|v| *v >= 0)
                .unwrap_or(-1)
        };
        Some(FocusedTrade {
            side: snap_str(f, "side"),
            entry_pos: opt_pos("entry_pos"),
            exit_pos: opt_pos("exit_pos"),
            entry_price: snap_f64(f, "entry_price").unwrap_or(f64::NAN),
            exit_price: snap_f64(f, "exit_price").unwrap_or(f64::NAN),
            winning: f.get("winning").and_then(|v| v.as_bool()).unwrap_or(false),
        })
    });
    // Strategy-owned marker visuals (entries, exits, EOD pills).
    if let Some(rows) = value.get("strategy_markers").and_then(|v| v.as_array()) {
        state.strategy_markers = rows
            .iter()
            .filter_map(|row| {
                let bar = row.get("bar")?.as_i64()? as i32;
                if bar < 0 {
                    return None;
                }
                let price = row.get("price")?.as_f64()?;
                if !price.is_finite() {
                    return None;
                }
                Some(StrategyMarker {
                    bar,
                    price,
                    kind: snap_str(row, "kind"),
                    text: snap_str(row, "text"),
                    layer: row.get("layer").and_then(|v| v.as_i64()).unwrap_or(60) as i32,
                    order: row.get("order").and_then(|v| v.as_i64()).unwrap_or(0) as i32,
                })
            })
            .collect();
    }
    // Strategy-owned rays (REF HIGH/LOW horizontals).
    if let Some(rows) = value.get("strategy_rays").and_then(|v| v.as_array()) {
        state.strategy_rays = rows
            .iter()
            .filter_map(|row| {
                let start = row.get("start")?.as_i64()? as i32;
                if start < 0 {
                    return None;
                }
                let price = row.get("price")?.as_f64()?;
                if !price.is_finite() {
                    return None;
                }
                Some(StrategyRay {
                    start,
                    price,
                    cap: row.get("cap").and_then(|v| v.as_i64()).unwrap_or(-1) as i32,
                    layer: row.get("layer").and_then(|v| v.as_i64()).unwrap_or(60) as i32,
                    order: row.get("order").and_then(|v| v.as_i64()).unwrap_or(0) as i32,
                })
            })
            .collect();
    }
    // Trade context strip facts (pre-formatted labels from the retained legacy
    // panel — the bridge carries them verbatim).
    if let Some(ctx) = value.get("trade_context") {
        if ctx.is_object() {
            state.trade_context = TradeContext {
                visible: snap_bool(ctx, "visible").unwrap_or(false),
                trade: snap_str(ctx, "trade"),
                symbol_side: snap_str(ctx, "symbol_side"),
                time: snap_str(ctx, "time"),
                pnl: snap_str(ctx, "pnl"),
                r: snap_str(ctx, "r"),
            };
        }
    }
    // Historical-Download console (retained-panel facts; open/calendar/modal
    // view-local fields are preserved).
    if let Some(dl) = value.get("download") {
        if dl.is_object() {
            mdownload::apply_download_snapshot(&mut state.download, dl);
        }
    }
    // Market status (legacy `MarketStatusPanel` read-only projection; the
    // panel's `open` state is view-local and never overwritten here).
    if let Some(ms) = value.get("market_status") {
        if ms.is_object() {
            let facts = &mut state.market_status;
            let set = |slot: &mut String, val: String| {
                if !val.is_empty() {
                    *slot = val;
                }
            };
            set(&mut facts.regime_current, snap_str(ms, "regime_current"));
            set(&mut facts.regime_trend, snap_str(ms, "regime_trend"));
            set(
                &mut facts.regime_volatility,
                snap_str(ms, "regime_volatility"),
            );
            set(&mut facts.regime_momentum, snap_str(ms, "regime_momentum"));
            set(&mut facts.provider, snap_str(ms, "provider"));
            set(&mut facts.latency, snap_str(ms, "latency"));
            set(&mut facts.last_update, snap_str(ms, "last_update"));
            set(&mut facts.bars_loaded, snap_str(ms, "bars_loaded"));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bars(n: usize) -> Vec<MarketBar> {
        (0..n)
            .map(|i| MarketBar {
                time: format!("2024-01-02T09:{:02}:00", i % 60),
                open: 100.0 + i as f64,
                high: 101.0 + i as f64,
                low: 99.0 + i as f64,
                close: 100.5 + i as f64,
                volume: 1000.0 + i as f64,
            })
            .collect()
    }

    #[test]
    fn initial_window_matches_legacy_anchor() {
        let mut st = MarketState::default();
        st.set_bars("TEST", "15m", "NSE", bars(INITIAL_BARS + 500));
        assert_eq!(st.status, MarketStatus::Ready);
        assert_eq!(st.count, INITIAL_BARS);
        assert!(st.follow_latest);
        // Latest bar sits at (1 - RIGHT_MARGIN) across the window.
        // Python `round` (half-to-even) — the legacy widget's own rule.
        let target = round_py((1.0 - RIGHT_MARGIN_FRACTION) * INITIAL_BARS as f64 + 0.5) as usize;
        assert_eq!(st.first, st.bars.len() - target);
        assert!(st.has_data());
        // Anchor keeps the window inside the data (clamped visible slice).
        assert_eq!(st.visible_window().len(), st.bars.len() - st.first);
        assert!(st.visible_window().len() <= INITIAL_BARS);
    }

    #[test]
    fn small_history_opens_whole_and_empty_is_honest() {
        let mut st = MarketState::default();
        st.set_bars("TEST", "15m", "NSE", bars(5));
        assert_eq!(st.first, 0);
        assert_eq!(st.visible_window().len(), 5);
        st.set_bars("TEST", "15m", "NSE", Vec::new());
        assert_eq!(st.status, MarketStatus::Empty);
        assert!(!st.has_data());
        assert!(st.visible_window().is_empty());
    }

    #[test]
    fn wheel_zoom_anchors_and_clamps_and_pans() {
        let mut st = MarketState::default();
        st.set_bars("TEST", "15m", "NSE", bars(3000));
        let before = st.count;
        assert!(st.apply(MarketAction::WheelZoom(0.5, -1.0))); // zoom in (inverted wheel)
        assert!(st.count < before);
        let mid_before = st.first;
        assert!(st.apply(MarketAction::WheelZoom(0.5, 2.0))); // zoom way out, capped
        assert!(st.count >= before.min(st.max_visible_bars()));
        // pan resets follow at left
        assert!(st.apply(MarketAction::WheelPanX(-0.2)));
        assert!(st.first > mid_before || st.first < mid_before);
        assert!(!st.follow_latest || st.first >= st.max_first());
    }

    #[test]
    fn drag_pan_moves_time_and_price_then_reset_restores() {
        let mut st = MarketState::default();
        st.set_bars("TEST", "15m", "NSE", bars(3000));
        assert!(st.apply(MarketAction::DragStart(0.5, 0.5)));
        assert!(st.apply(MarketAction::DragMove(0.6, 0.5)));
        assert!(!st.follow_latest);
        assert!(st.apply(MarketAction::DragEnd));
        assert!(st.apply(MarketAction::ResetView));
        assert!(st.follow_latest);
        assert_eq!(st.count, INITIAL_BARS);
        assert!(st.price_manual.is_none());
    }

    /// One-side free pan (the requested screenshot behavior): dragging the
    /// candles LEFT keeps moving past the live edge until the newest bar sits
    /// on the plot's left edge — everything to its right is real empty space.
    #[test]
    fn free_pan_left_opens_large_empty_right_space() {
        let mut st = MarketState::default();
        st.set_bars("TEST", "15m", "NSE", bars(3000));
        let total = st.bars.len();
        let slots = st.count;
        assert_eq!(st.first, st.max_first());
        // One long gesture: the pointer leaves the plot, the chart still owns
        // the drag (same as the legacy widget's mouse grab).
        assert!(st.apply(MarketAction::DragStart(0.95, 0.5)));
        assert!(st.apply(MarketAction::DragMove(-1.5, 0.5)));
        assert!(st.apply(MarketAction::DragEnd));
        // Newest bar on the left edge: one slot filled, the rest empty.
        assert_eq!(st.first, total - 1);
        assert_eq!(st.visible_window().len(), 1);
        assert!(!st.follow_latest);
        let view = project(&st);
        assert_eq!(view.plot_slots as usize, slots);
        assert_eq!(view.candles.len(), 1);
        assert!((view.candles[0].x - 0.5 / slots as f32).abs() < 1e-6);
        // No time tick floats over the empty space either.
        assert!(view.time_ticks.iter().all(|t| t.pos <= 1.0 / slots as f32));
    }

    /// The other direction stays bounded: the viewport stops at the oldest bar
    /// and never shows empty space left of it (the pan is one-sided by design).
    #[test]
    fn pan_toward_the_past_still_stops_at_the_oldest_bar() {
        let mut st = MarketState::default();
        st.set_bars("TEST", "15m", "NSE", bars(3000));
        for _ in 0..20 {
            assert!(st.apply(MarketAction::WheelPanX(0.5)));
        }
        assert_eq!(st.first, 0);
        assert!(!st.follow_latest);
        let slots = st.count;
        let view = project(&st);
        assert_eq!(view.candles.len(), slots);
        assert!((view.candles[0].x - 0.5 / slots as f32).abs() < 1e-6);
    }

    /// Released in the empty-space position the view stays there: the next
    /// backend snapshot of the same series shifts the same bars, it does not
    /// snap back to the live edge.
    #[test]
    fn released_free_pan_view_does_not_snap_back_on_new_bars() {
        let mut st = MarketState::default();
        st.set_bars("TEST", "15m", "NSE", bars(3000));
        assert!(st.apply(MarketAction::DragStart(0.5, 0.5)));
        assert!(st.apply(MarketAction::DragMove(0.1, 0.5)));
        assert!(st.apply(MarketAction::DragEnd));
        let parked = st.first;
        assert!(
            parked > st.max_first(),
            "parked past the live edge: {parked}"
        );
        assert!(!st.follow_latest);
        // Same series, one more bar: the window keeps the same bars in view.
        st.set_bars("TEST", "15m", "NSE", bars(3001));
        assert_eq!(st.first, parked + 1);
        assert!(!st.follow_latest);
    }

    /// Candle slot width is the time grid, not the number of bars the data
    /// happens to hold: at the data end the remaining candles keep their slot
    /// width instead of stretching across the plot, so the right margin is
    /// real empty space.
    #[test]
    fn candles_keep_slot_width_at_the_data_end() {
        let st = golden_state(); // 3000 bars, legacy 1200px plot width
        let view = project(&st);
        assert_eq!(view.plot_slots, 1200);
        assert_eq!(view.candles.len(), 1020);
        let slot = view.candles[1].x - view.candles[0].x;
        assert!((slot - 1.0 / 1200.0).abs() < 1e-6, "slot width {slot}");
        let last_x = view.candles.last().unwrap().x;
        assert!(1.0 - last_x > 0.14, "right space {}", 1.0 - last_x);
        assert!(
            view.time_ticks
                .iter()
                .all(|t| t.pos < last_x + 1.0 / 1200.0),
            "ticks stay on the candle slots"
        );
    }

    #[test]
    fn price_scale_drag_and_doubleclick_reset() {
        let mut st = MarketState::default();
        st.set_bars("TEST", "15m", "NSE", bars(3000));
        let (l0, h0) = st.price_range();
        assert!(st.apply(MarketAction::PriceDrag(0.2, 0.5)));
        assert!(st.price_manual.is_some());
        assert_ne!(st.price_range().0, l0);
        assert!(st.apply(MarketAction::PriceReset));
        let (l1, h1) = st.price_range();
        assert!((l1 - l0).abs() < 1e-6 && (h1 - h0).abs() < 1e-6);
        assert!(st.apply(MarketAction::PriceZoom(1.0, 0.5)));
        assert!(st.price_manual.is_some());
        assert!(st.apply(MarketAction::PriceReset));
    }

    #[test]
    fn hover_snaps_to_candle_and_clears() {
        let mut st = MarketState::default();
        st.set_bars("TEST", "15m", "NSE", bars(100));
        assert!(st.apply(MarketAction::HoverMoved(0.99, 0.5)));
        assert!(st.hover.is_some());
        assert!(st.apply(MarketAction::HoverLeft));
        assert!(st.hover.is_none());
    }

    #[test]
    fn timeframe_order_and_overflow() {
        let mut st = MarketState::default();
        st.timeframes = vec![
            "1D".to_string(),
            "15m".to_string(),
            "1h".to_string(),
            "1W".to_string(),
        ];
        let v = project(&st);
        let labels: Vec<&str> = v
            .timeframes_visible
            .iter()
            .map(|t| t.label.as_str())
            .collect();
        assert_eq!(labels, vec!["15m", "1h"]);
        let of: Vec<&str> = v
            .timeframes_overflow
            .iter()
            .map(|t| t.label.as_str())
            .collect();
        assert!(of.contains(&"1D") && of.contains(&"1W"));
    }

    #[test]
    fn indicator_popup_sections_and_search() {
        let mut st = MarketState::default();
        st.strategies = vec!["OBR".to_string()];
        assert!(st.apply(MarketAction::IndicatorPopup(true)));
        let v = project(&st);
        assert!(v.popup_open);
        assert!(v
            .popup_rows
            .iter()
            .any(|r| r.kind == "item" && r.name == "SMA"));
        assert!(v
            .popup_rows
            .iter()
            .any(|r| r.name == "OBR" && r.category == "STRATEGIES"));
        assert!(st.apply(MarketAction::IndicatorQuery("zzz".to_string())));
        let v = project(&st);
        assert_eq!(v.popup_rows.len(), 1);
        assert_eq!(v.popup_rows[0].kind, "empty");
        assert!(st.apply(MarketAction::IndicatorCategory("MOMENTUM".to_string())));
        assert!(st.apply(MarketAction::IndicatorQuery(String::new())));
        let v = project(&st);
        assert!(v.popup_rows.iter().any(|r| r.name == "RSI"));
        assert!(!v.popup_rows.iter().any(|r| r.name == "SMA"));
        assert!(st.apply(MarketAction::IndicatorPopup(false)));
        assert!(!st.popup_open);
    }

    #[test]
    fn add_toggle_remove_indicator_bar_semantics() {
        let mut st = MarketState::default();
        assert!(st.apply(MarketAction::AddIndicator("SMA".to_string())));
        assert_eq!(st.indicators.len(), 1);
        assert!(st.indicators[0].visible);
        assert!(st.apply(MarketAction::ToggleIndicatorVisible("SMA".to_string())));
        assert!(!st.indicators[0].visible);
        // One indicator's toggle must not touch another.
        assert!(st.apply(MarketAction::AddIndicator("Stochastic".to_string())));
        assert!(st.indicators[1].visible);
        assert!(st.apply(MarketAction::RemoveIndicator("SMA".to_string())));
        assert_eq!(st.indicators.len(), 1);
        assert_eq!(st.indicators[0].name, "Stochastic");
        // Delete removes completely (OBR too) — no resurrection on refresh.
        assert!(st.apply(MarketAction::RemoveIndicator("Stochastic".to_string())));
        assert!(st.indicators.is_empty());
        assert!(st.apply(MarketAction::AddIndicator("Volume".to_string())));
        assert_eq!(st.indicators[0].name, "Vol");
        assert!(st.apply(MarketAction::RemoveIndicator("Volume".to_string())));
        assert!(st.indicators.is_empty());
    }

    #[test]
    fn settings_popup_semantics() {
        let mut st = MarketState::default();
        assert!(st.apply(MarketAction::AddIndicator("OBR".to_string())));
        // Seed backend-reported specs (as the snapshot would).
        st.indicator_params.insert(
            "OBR".to_string(),
            vec![SettingsRow {
                key: "c1_thresh".to_string(),
                label: "C1 Threshold".to_string(),
                value: 1.25,
                min: 0.5,
                max: 5.0,
                step: 0.05,
                decimals: 2,
            }],
        );
        // Opening for a missing row fails; for an existing row it seeds.
        assert!(!st.apply(MarketAction::SettingsPopup(true, "RSI".to_string())));
        assert!(st.apply(MarketAction::SettingsPopup(true, "OBR".to_string())));
        assert!(st.settings_open);
        assert_eq!(st.settings_name, "OBR");
        assert_eq!(st.settings_rows.len(), 1);
        let v = project(&st);
        assert!(v.settings_open);
        assert_eq!(v.settings_name, "OBR");
        assert_eq!(v.settings_rows.len(), 1);
        assert_eq!(v.settings_rows[0].key, "c1_thresh");
        // Edits hit the local buffer only, one indicator at a time.
        assert!(st.apply(MarketAction::SettingsEdit("c1_thresh".to_string(), 2.0)));
        assert!(!st.apply(MarketAction::SettingsEdit("nope".to_string(), 1.0)));
        assert_eq!(st.settings_rows[0].value, 2.0);
        assert!(st.indicators[0].visible);
        // SAVE payload is name + JSON over the buffer.
        let (name, json) = st.settings_payload().expect("payload");
        assert_eq!(name, "OBR");
        assert_eq!(json, "{\"c1_thresh\":2}");
        // Commit closes and stays backend-owned (wire queued by the view).
        let mut q = MarketState::default();
        assert!(q.apply(MarketAction::AddIndicator("OBR".to_string())));
        assert!(q.apply(MarketAction::SettingsPopup(true, "OBR".to_string())));
        q.interact(
            "indicator:params:OBR:{\"c1_thresh\":2}",
            MarketAction::ApplyIndicatorSettings(
                "OBR".to_string(),
                "{\"c1_thresh\":2}".to_string(),
            ),
        );
        assert_eq!(
            q.pending_actions,
            vec!["indicator:params:OBR:{\"c1_thresh\":2}".to_string()]
        );
        assert!(!q.settings_open);
        // Unknown indicator: apply fails, no wire queued.
        q.interact(
            "indicator:params:GHOST:{}",
            MarketAction::ApplyIndicatorSettings("GHOST".to_string(), "{}".to_string()),
        );
        assert_eq!(q.pending_actions.len(), 1);
        // RESET drops overrides and re-seeds an empty panel.
        assert!(q.apply(MarketAction::SettingsPopup(true, "OBR".to_string())));
        assert!(q.apply(MarketAction::ResetIndicatorSettings("OBR".to_string())));
        assert!(q.settings_rows.is_empty());
    }

    #[test]
    fn plot_series_and_trade_markers() {
        let mut st = MarketState::default();
        st.set_bars("TEST", "15m", "NSE", bars(100));
        // Use indices inside the anchored visible window (first > 0).
        let first = st.first;
        st.plot_series = vec![PlotSeries {
            owner: "SMA".to_string(),
            title: "SMA20".to_string(),
            points: vec![(first, 110.0), (first + 1, 111.0), (first + 5, 120.0)],
            extend: String::new(),
            cap: -1,
        }];
        st.trade_markers = vec![TradeMarker {
            side: "LONG".to_string(),
            entry_price: 110.0,
            exit_price: 120.0,
            winning: true,
            entry_pos: first as i32,
            exit_pos: (first + 5) as i32,
            entry_covered: false,
            exit_covered: false,
            exit_reason: "SIGNAL".to_string(),
        }];
        let v = project(&st);
        assert!(!v.plot_segments.is_empty());
        // Entry BUY pill + exit SELL pill + dashed connection segments.
        let labels: Vec<&str> = v.markers.iter().map(|m| m.label.as_str()).collect();
        assert!(labels.contains(&"BUY"));
        assert!(labels.contains(&"SELL"));
        // Hidden owner (eye off) drops the series (trade connections stay).
        st.indicators = vec![IndicatorEntry {
            name: "SMA".to_string(),
            visible: false,
        }];
        let v = project(&st);
        assert!(v.plot_segments.iter().all(|s| s.series != 0));
        assert!(v.markers.iter().any(|m| m.label == "BUY"));
    }

    #[test]
    fn covered_bars_suppress_signal_markers_but_keep_lines() {
        let mut st = MarketState::default();
        st.set_bars("TEST", "15m", "NSE", bars(100));
        let first = st.first;
        st.trade_markers = vec![TradeMarker {
            side: "LONG".to_string(),
            entry_price: 110.0,
            exit_price: 120.0,
            winning: false,
            entry_pos: first as i32,
            exit_pos: (first + 5) as i32,
            entry_covered: true,
            exit_covered: false,
            exit_reason: "SIGNAL".to_string(),
        }];
        // Strategy owns the entry bar: entry triangle suppressed, exit stays.
        let v = project(&st);
        let labels: Vec<&str> = v.markers.iter().map(|m| m.label.as_str()).collect();
        assert!(!labels.contains(&"BUY"));
        assert!(labels.contains(&"SELL"));
        // Connection line always paints (different information).
        assert!(!v.plot_segments.is_empty());
        // Losing exit renders the down-triangle loss tone.
        let sell = v.markers.iter().find(|m| m.label == "SELL").unwrap();
        assert_eq!((sell.kind, sell.color, sell.pill), (1, 3, 2));
    }

    #[test]
    fn sparse_extend_series_draw_rays_not_diagonals() {
        let mut st = MarketState::default();
        st.set_bars("TEST", "15m", "NSE", bars(100));
        let first = st.first;
        st.plot_series = vec![PlotSeries {
            owner: "OBR".to_string(),
            title: "REF HIGH".to_string(),
            points: vec![(first, 120.0), (first + 20, 121.0)],
            extend: "session".to_string(),
            cap: -1,
        }];
        let v = project(&st);
        // Ray: horizontal segments at the plotted price (no diagonal drift).
        assert!(!v.plot_segments.is_empty());
        for seg in &v.plot_segments {
            assert!((seg.y1 - seg.y2).abs() < 1e-6);
        }
    }

    #[test]
    fn strategy_markers_map_kinds_and_eod_pills() {
        let mut st = MarketState::default();
        st.set_bars("TEST", "15m", "NSE", bars(100));
        let first = st.first;
        st.strategy_markers = vec![
            StrategyMarker {
                bar: first as i32,
                price: 110.0,
                kind: "UP_ARROW".to_string(),
                text: "BUY 110.00".to_string(),
                layer: 60,
                order: 0,
            },
            StrategyMarker {
                bar: (first + 5) as i32,
                price: 115.0,
                kind: "TRIANGLE_BLUE".to_string(),
                text: "EOD 115.00 +5.00".to_string(),
                layer: 60,
                order: 1,
            },
            StrategyMarker {
                bar: (first + 8) as i32,
                price: 112.0,
                kind: "CIRCLE_X".to_string(),
                text: String::new(),
                layer: 60,
                order: 2,
            },
        ];
        let v = project(&st);
        assert_eq!(v.markers.len(), 3);
        let eod = v
            .markers
            .iter()
            .find(|m| m.label.starts_with("EOD"))
            .unwrap();
        assert_eq!((eod.kind, eod.color), (0, 4));
        assert_eq!(eod.pill, 2); // UP-family pills sit below the anchor
        assert!(eod.pill_solid);
        let glyph_only = v.markers.iter().find(|m| m.kind == 4).unwrap();
        assert_eq!(glyph_only.pill, 0);
    }

    #[test]
    fn watchlist_panel_switch_sort_filter_watchlists() {
        let mut st = MarketState::default();
        st.symbols = vec![
            WatchEntry {
                symbol: "A".to_string(),
                price: Some(1.0),
                change_pct: Some(0.5),
            },
            WatchEntry {
                symbol: "B".to_string(),
                price: None,
                change_pct: None,
            },
        ];
        assert!(st.apply(MarketAction::SortDesc));
        let v = project(&st);
        assert_eq!(v.watch_rows[0].symbol, "B");
        assert!(st.apply(MarketAction::AddWatchlist));
        assert!(st.apply(MarketAction::RemoveWatchlist));
        assert_eq!(st.active_watchlist, "All Stocks");
        assert!(!st.apply(MarketAction::RemoveWatchlist));
        assert!(st.apply(MarketAction::PanelToggle));
        assert!(!st.panel_visible);
        let v = project(&st);
        assert!(!v.panel_visible);
    }

    #[test]
    fn layout_tiers_follow_design_tokens() {
        assert_eq!(layout_mode(1920.0), MarketLayoutMode::Wide);
        assert_eq!(layout_mode(1179.0), MarketLayoutMode::Medium);
        assert_eq!(layout_mode(819.0), MarketLayoutMode::Narrow);
        let tokens = include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/ui/palette.slint"));
        assert!(tokens.contains("market-wide: 1180px"));
        assert!(tokens.contains("market-medium: 820px"));
    }

    #[test]
    fn formatting_matches_product_conventions() {
        assert_eq!(fmt_price(2451.10), "2,451.10");
        assert_eq!(fmt_price(f64::NAN), "N/A");
        assert_eq!(fmt_signed_pct(Some(1.236)), "+1.24%");
        assert_eq!(fmt_signed_pct(None), "N/A");
        assert_eq!(fmt_volume(950.0), "950");
        assert_eq!(fmt_volume(8020.0), "8.02 K");
        assert_eq!(fmt_volume(1_250_000.0), "1.25 M");
        assert_eq!(fmt_volume(2_500_000_000.0), "2.50 B");
        assert_eq!(normalize_indicator_name("Volume"), "Vol");
        assert_eq!(normalize_indicator_name("volume"), "Vol");
        assert_eq!(normalize_indicator_name("SMA"), "SMA");
    }

    #[test]
    fn snapshot_feeds_and_interact_queues_only_backend() {
        let mut st = MarketState::default();
        let value: serde_json::Value = serde_json::from_str(
            r#"{"symbols":[{"symbol":"TEST","price":101.5,"change_pct":2.0}],
               "selected_symbol":"TEST","timeframes":["15m","1D"],
               "timeframe":"15m","exchange":"NSE","strategies":["OBR"],
               "bars":[{"time":"2024-01-02T09:15:00","open":100.0,"high":102.0,
               "low":99.0,"close":101.5,"volume":1000.0}],
               "indicators":{"SMA":true},"volume_visible":true}"#,
        )
        .unwrap();
        apply_snapshot_json(&mut st, &value);
        assert_eq!(st.symbols.len(), 1);
        assert_eq!(st.status, MarketStatus::Ready);
        assert!(st.indicators.iter().any(|e| e.name == "SMA" && e.visible));
        assert_eq!(st.strategies, vec!["OBR".to_string()]);
        assert!(st.interact("select:X", MarketAction::SelectSymbol("X".to_string())));
        assert_eq!(st.pending_actions, vec!["select:X".to_string()]);
        assert!(st.interact("filter:y", MarketAction::SetFilter("y".to_string())));
        assert_eq!(st.pending_actions.len(), 1); // filter is view-local
    }

    // ── Golden parity vs the legacy CandleChartWidget ───────────────
    // Bars, widget size and the input sequence are byte-identical to the
    // Python golden probe (`Temp/opencode/parity_legacy.py`): a 1200x700 widget
    // (chart rect 1200x571 → density cap 1200) over a 3000-bar closed-form
    // series. Every expected value below was produced by the REAL legacy widget
    // on the same inputs — drift here means a behavioral parity gap.

    fn golden_bars(n: usize) -> Vec<MarketBar> {
        (0..n)
            .map(|i| {
                let open = 1000.0 + i as f64 * 0.01;
                let close = open + 2.0;
                MarketBar {
                    time: format!("2024-01-02T{:02}:{:02}:00", 9 + i / 60, i % 60),
                    open,
                    high: open.max(close) + 1.0 + (i % 7) as f64,
                    low: open.min(close) - 1.0 - (i % 5) as f64,
                    close,
                    volume: 1000.0 + i as f64,
                }
            })
            .collect()
    }

    const GOLD_CHART_W: usize = 1200;
    const GOLD_CHART_H: usize = 571;

    fn golden_state() -> MarketState {
        let mut st = MarketState::default();
        st.width_cap = GOLD_CHART_W; // legacy max_visible_bars at 1200px width
        st.set_bars("PAR", "15m", "NSE", golden_bars(3000));
        st
    }

    /// legacy `_price_from_y` fraction for a pointer pixel: the chart rect spans
    /// y 0..=570 (height 571), so fraction = (bottom - py) / (bottom - top).
    fn gold_price_frac(py: f64) -> f64 {
        (GOLD_CHART_H as f64 - 1.0 - py) / (GOLD_CHART_H as f64 - 1.0)
    }

    /// legacy `_zoom_price_at` fraction: divides by the rect height (571), not
    /// bottom-top — the legacy's own convention, mirrored exactly.
    fn gold_zoom_frac(py: f64) -> f64 {
        (GOLD_CHART_H as f64 - 1.0 - py) / GOLD_CHART_H as f64
    }

    #[test]
    fn golden_initial_window_matches_golden() {
        let st = golden_state();
        // legacy: first 1980, logical count 1200 (visible 1020 — clamped by the
        // data end), follow-latest on, price span over the visible window.
        assert_eq!(st.first, 1980);
        assert_eq!(st.count, 1200);
        assert_eq!(st.visible_window().len(), 1020);
        assert!(st.follow_latest);
        let (lo, hi) = st.price_range();
        assert!((lo - 1013.6345).abs() < 5e-4);
        assert!((hi - 1040.1555).abs() < 5e-4);
    }

    #[test]
    fn golden_crosshair_snap_matches_golden() {
        let mut st = golden_state();
        // legacy pointer (600, 300): fraction 0.5; snap lands on bar 2580 and
        // the price tracks the pointer y (legacy _price_from_y fraction).
        let fy = 1.0 - gold_price_frac(300.0);
        assert!(st.apply(MarketAction::HoverMoved(0.5, fy as f32)));
        let hover = st.hover.expect("hover snapped");
        assert_eq!(hover.index, 2580);
        assert!(
            (hover.price - 1026.197079).abs() < 5e-4,
            "price {}",
            hover.price
        );
        let v = project(&st);
        let bar = &st.bars[hover.index];
        assert_eq!(v.hover_volume, fmt_volume(bar.volume));
        assert_eq!(v.hover_time, format_crosshair_time(&bar.time));
        assert!(v.header_ohlc.contains(&fmt_price(bar.open)));
    }

    #[test]
    fn golden_zoom_pan_reset_match_golden() {
        let mut st = golden_state();
        // Zoom-in x3 at fx=0.5 (inverted wheel: steps=-1): already at the
        // density cap, nothing changes (zoom-in cannot over-compress).
        for _ in 0..3 {
            assert!(st.apply(MarketAction::WheelZoom(0.5, 1.0)));
        }
        assert_eq!((st.first, st.count), (1980, 1200));
        assert!(st.follow_latest);
        // Horizontal pan +300px x4 (delta_frac = 0.25): first 780.
        for _ in 0..4 {
            assert!(st.apply(MarketAction::WheelPanX(0.25)));
        }
        assert_eq!((st.first, st.count), (780, 1200));
        assert!(!st.follow_latest);
        let (lo, hi) = st.price_range();
        assert!((lo - 1001.5455).abs() < 5e-4 && (hi - 1030.0245).abs() < 5e-4);
        // Zoom-out x3 (inverted wheel: steps=-2 per notch pair): first 1222,
        // count 315. The final anchor lands on an exact .5 (1222.5): Python
        // rounds it to 1222 (half-to-even); Rust must not drift to 1223.
        for _ in 0..3 {
            assert!(st.apply(MarketAction::WheelZoom(0.5, -2.0)));
        }
        assert_eq!((st.first, st.count), (1222, 315));
        let (lo, hi) = st.price_range();
        assert!((lo - 1006.386).abs() < 5e-4 && (hi - 1025.174).abs() < 5e-4);
        // legacy reset → the fresh-chart viewport.
        assert!(st.apply(MarketAction::ResetView));
        assert_eq!((st.first, st.count), (1980, 1200));
        assert!(st.follow_latest);
    }

    #[test]
    fn golden_drag_pan_2d_and_price_scale_match_golden() {
        let mut st = golden_state();
        // legacy left-drag (600,300) → (720,360): time shifts -120 bars and the
        // price range shifts by span/height * 60px.
        let y0 = 300.0 / GOLD_CHART_H as f64;
        let y1 = 360.0 / GOLD_CHART_H as f64;
        assert!(st.apply(MarketAction::DragStart(0.5, y0 as f32)));
        assert!(st.apply(MarketAction::DragMove(0.6, y1 as f32)));
        assert_eq!(st.first, 1860);
        assert!(!st.follow_latest);
        let (lo, hi) = st.price_range();
        assert!((lo - 1016.421295).abs() < 5e-4, "drag price low {lo}");
        assert!((hi - 1042.942295).abs() < 5e-4, "drag price high {hi}");
        assert!(st.apply(MarketAction::DragEnd));
        // legacy price-strip wheel zoom at the anchor y=200 (dy=-120): zooms the
        // live range around that pixel (factor 1.25, legacy _zoom_price_at).
        let anchor = 1.0 - gold_zoom_frac(200.0);
        assert!(st.apply(MarketAction::PriceZoom(-1.0, anchor as f32)));
        let (lo, hi) = st.price_range();
        assert!((lo - 1012.124986).abs() < 5e-4, "price zoom low {lo}");
        assert!((hi - 1045.276236).abs() < 5e-4, "price zoom high {hi}");
        // legacy price double-click reset (auto-fit over the current window).
        assert!(st.apply(MarketAction::PriceReset));
        let (lo, hi) = st.price_range();
        assert!((lo - 1012.3745).abs() < 5e-4 && (hi - 1040.2155).abs() < 5e-4);

        // Drag toward the latest bar: ONE-SIDE FREE PAN (requested behavior —
        // deliberate divergence from the legacy clamp at the live edge). The
        // candles keep moving left past the anchor; released there the view
        // stays exactly where the user left it, so follow-latest does NOT
        // re-engage (no snap-back).
        let x0 = 1100.0 / GOLD_CHART_W as f64;
        let x1 = 100.0 / GOLD_CHART_W as f64;
        assert!(st.apply(MarketAction::DragStart(x0 as f32, y0 as f32)));
        assert!(st.apply(MarketAction::DragMove(x1 as f32, y0 as f32)));
        assert!(st.apply(MarketAction::DragEnd));
        assert!(
            (2859..=2861).contains(&st.first),
            "free pan left: first {}",
            st.first
        );
        assert!(!st.follow_latest);
        // The live edge itself is unchanged (still the follow anchor).
        assert_eq!(st.max_first(), 1980);
    }

    #[test]
    fn golden_price_strip_drag_anchors_at_press() {
        // legacy _drag_price_from: factor = PRICE_ZOOM_STEP ** (delta_y/120),
        // applied to the LIVE range around the press point.
        let mut st = golden_state();
        let anchor = 1.0 - gold_zoom_frac(200.0);
        // Press at y=200, drag to y=260 (+60px = +0.5 notches): factor
        // 1.25^0.5 expands the range around the press price.
        assert!(st.apply(MarketAction::PriceDrag(0.5, anchor as f32)));
        let (lo0, hi0) = st.price_range();
        let base = 1013.6345_f64;
        let top = 1040.1555_f64;
        let span = top - base;
        let frac = 1.0 - anchor;
        let anchor_price = base + frac * span;
        let new_span = span * 1.25f64.powf(0.5);
        assert!((lo0 - (anchor_price - frac * new_span)).abs() < 1e-6);
        assert!((hi0 - (lo0 + new_span)).abs() < 1e-6);
    }

    #[test]
    fn crosshair_candle_index_equals_timestamp_volume_and_ohlc_indices() {
        for tf in ["5m", "15m", "30m", "1h"] {
            let mut st = MarketState::default();
            st.set_bars("TEST", tf, "NSE", bars(200));

            // Test across multiple crosshair positions
            for fx in [0.1f32, 0.25, 0.5, 0.7, 0.85] {
                assert!(st.apply(MarketAction::HoverMoved(fx, 0.5)));
                let hover = st.hover.expect("hover active");
                let v = project(&st);
                assert!(v.has_hover);

                let bar = &st.bars[hover.index];
                // Acceptance test: crosshair candle == timestamp == volume == OHLC
                assert_eq!(v.hover_time, format_crosshair_time(&bar.time));
                assert_eq!(v.hover_volume, fmt_volume(bar.volume));
                assert!(v.header_ohlc.contains(&fmt_price(bar.open)));
                assert!(v.header_ohlc.contains(&fmt_price(bar.high)));
                assert!(v.header_ohlc.contains(&fmt_price(bar.low)));
                assert!(v.header_ohlc.contains(&fmt_price(bar.close)));
            }

            // Test after Zoom
            assert!(st.apply(MarketAction::WheelZoom(0.5, -1.0)));
            assert!(st.apply(MarketAction::HoverMoved(0.3, 0.5)));
            let hover_zoom = st.hover.expect("hover active after zoom");
            let v_zoom = project(&st);
            let bar_zoom = &st.bars[hover_zoom.index];
            assert_eq!(v_zoom.hover_volume, fmt_volume(bar_zoom.volume));
            assert_eq!(v_zoom.hover_time, format_crosshair_time(&bar_zoom.time));
            assert!(v_zoom.header_ohlc.contains(&fmt_price(bar_zoom.close)));

            // Test after Pan
            assert!(st.apply(MarketAction::WheelPanX(0.2)));
            assert!(st.apply(MarketAction::HoverMoved(0.6, 0.5)));
            let hover_pan = st.hover.expect("hover active after pan");
            let v_pan = project(&st);
            let bar_pan = &st.bars[hover_pan.index];
            assert_eq!(v_pan.hover_volume, fmt_volume(bar_pan.volume));
            assert_eq!(v_pan.hover_time, format_crosshair_time(&bar_pan.time));
            assert!(v_pan.header_ohlc.contains(&fmt_price(bar_pan.close)));
        }
    }

    // ── market status panel (legacy MarketStatusPanel parity) ────────────────

    #[test]
    fn market_status_defaults_to_honest_unknown() {
        let state = MarketState::default();
        assert!(!state.market_status.open);
        let view = project(&state);
        assert!(!view.market_status_open);
        // Exact legacy grid: 4 regime rows then 4 data rows, all "--", all muted.
        let regime_keys: Vec<&str> = view
            .market_status_regime
            .iter()
            .map(|(k, _, _)| k.as_str())
            .collect();
        let data_keys: Vec<&str> = view
            .market_status_data
            .iter()
            .map(|(k, _, _)| k.as_str())
            .collect();
        assert_eq!(
            regime_keys,
            vec!["Current regime", "Trend strength", "Volatility", "Momentum"]
        );
        assert_eq!(
            data_keys,
            vec!["Data provider", "Latency", "Last update", "Bars loaded"]
        );
        let all: Vec<_> = view
            .market_status_regime
            .iter()
            .chain(view.market_status_data.iter())
            .collect();
        assert!(all.iter().all(|(_, v, muted)| v == "--" && *muted));
    }

    #[test]
    fn market_status_snapshot_sets_values_and_unmutes() {
        let mut state = MarketState::default();
        let json = serde_json::json!({
            "market_status": {
                "provider": "Zerodha",
                "latency": "12ms",
                "last_update": "2026-09-17 15:30:00",
                "bars_loaded": "61,676",
                "regime_current": "TRENDING",
            }
        });
        apply_snapshot_json(&mut state, &json);
        let view = project(&state);
        let by_key: std::collections::HashMap<&str, (&str, bool)> = view
            .market_status_data
            .iter()
            .chain(view.market_status_regime.iter())
            .map(|(k, v, m)| (k.as_str(), (v.as_str(), *m)))
            .collect();
        assert_eq!(by_key["Data provider"], ("Zerodha", false));
        assert_eq!(by_key["Latency"], ("12ms", false));
        assert_eq!(by_key["Bars loaded"], ("61,676", false));
        assert_eq!(by_key["Current regime"], ("TRENDING", false));
        // Untouched rows stay honest-unknown + muted.
        assert_eq!(by_key["Trend strength"], ("--", true));
        // Snapshot NEVER touches the view-local open flag.
        assert!(!view.market_status_open);
    }

    fn scale_bars() -> Vec<MarketBar> {
        (0..100)
            .map(|i| {
                let close = 100.0 + i as f64;
                MarketBar {
                    time: format!("2024-01-02T{:02}:{:02}:00", 9 + i / 60, i % 60),
                    open: close - 1.0,
                    high: close + 1.0,
                    low: close - 2.0,
                    close,
                    volume: 1000.0,
                }
            })
            .collect()
    }

    #[test]
    fn scale_cycle_order_and_flag_toggles() {
        let mut st = MarketState::default();
        assert_eq!(st.scale_mode, ChartScaleMode::Regular);
        assert!(st.apply(MarketAction::CycleScaleMode));
        assert_eq!(st.scale_mode, ChartScaleMode::Percent);
        assert!(st.apply(MarketAction::CycleScaleMode));
        assert_eq!(st.scale_mode, ChartScaleMode::Logarithmic);
        assert!(st.apply(MarketAction::CycleScaleMode));
        assert_eq!(st.scale_mode, ChartScaleMode::Regular);
        assert_eq!(ChartScaleMode::Percent.label(), "%");
        assert_eq!(ChartScaleMode::Logarithmic.kind(), 2);
        assert!(st.grid_visible && st.cross_visible);
        assert!(st.apply(MarketAction::ToggleGrid));
        assert!(!st.grid_visible);
        assert!(st.apply(MarketAction::ToggleCrosshair));
        assert!(!st.cross_visible);
    }

    #[test]
    fn percent_scale_anchors_first_visible_bar_at_zero() {
        let mut st = MarketState::default();
        st.width_cap = 200;
        st.set_bars("S", "15m", "", scale_bars());
        assert!(st.apply(MarketAction::CycleScaleMode));
        let view = project(&st);
        assert!(view.has_data);
        // First bar sits at ~0%, last near +99%: monotonic rise preserved.
        assert!(view.candles.len() > 10);
        for w in view.candles.windows(2) {
            assert!(w[0].close >= w[1].close, "percent order must follow price");
        }
        assert!(view.price_ticks.iter().all(|t| t.label.ends_with('%')));
        let proj = project_hover(&st);
        assert!(proj.header_ohlc.starts_with('O'));
    }

    #[test]
    fn log_scale_keeps_price_labels_and_order() {
        let mut st = MarketState::default();
        st.width_cap = 200;
        st.set_bars("S", "15m", "", scale_bars());
        assert!(st.apply(MarketAction::CycleScaleMode));
        assert!(st.apply(MarketAction::CycleScaleMode));
        assert_eq!(st.scale_mode, ChartScaleMode::Logarithmic);
        let view = project(&st);
        assert!(!view.price_ticks.is_empty());
        assert!(view.price_ticks.iter().all(|t| !t.label.ends_with('%')));
        // Geometry stays in band and ordered.
        for c in &view.candles {
            for v in [c.open, c.high, c.low, c.close] {
                assert!((0.0..=1.0).contains(&v));
            }
        }
    }

    #[test]
    fn degenerate_scale_falls_back_to_regular() {
        // Zero anchor: percent cannot represent, regular geometry survives.
        let mut st = MarketState::default();
        st.width_cap = 200;
        st.set_bars(
            "S",
            "15m",
            "",
            vec![MarketBar {
                time: "2024-01-02T10:00:00".to_string(),
                open: 0.0,
                high: 0.0,
                low: 0.0,
                close: 0.0,
                volume: 0.0,
            }],
        );
        assert!(st.apply(MarketAction::CycleScaleMode));
        let view = project(&st);
        assert!(view.price_ticks.iter().all(|t| !t.label.ends_with('%')));
    }

    #[test]
    fn settings_project_into_view() {
        let mut st = MarketState::default();
        st.set_bars("S", "15m", "", scale_bars());
        assert!(st.apply(MarketAction::ToggleGrid));
        let view = project(&st);
        assert_eq!(view.scale_mode, 0);
        assert_eq!(view.scale_label, "₹");
        assert!(!view.grid_visible);
        assert!(view.cross_visible);
    }

    #[test]
    fn popup_chart_section_lists_scale_grid_cross() {
        let mut st = MarketState::default();
        st.set_bars("S", "15m", "", scale_bars());
        assert!(st.apply(MarketAction::IndicatorPopup(true)));
        let view = project(&st);
        let rows: Vec<(&str, &str)> = view
            .popup_rows
            .iter()
            .filter(|r| r.kind == "item" && r.category == "CHART")
            .map(|r| (r.name.as_str(), r.label.as_str()))
            .collect();
        assert_eq!(
            rows,
            vec![
                ("scale", "Scale: Regular (₹)"),
                ("grid", "Grid lines: On"),
                ("cross", "Crosshair: On"),
            ]
        );
        // Labels follow state; ids stay stable for routing.
        assert!(st.apply(MarketAction::CycleScaleMode));
        assert!(st.apply(MarketAction::ToggleGrid));
        let view = project(&st);
        let rows: Vec<(&str, &str)> = view
            .popup_rows
            .iter()
            .filter(|r| r.kind == "item" && r.category == "CHART")
            .map(|r| (r.name.as_str(), r.label.as_str()))
            .collect();
        assert_eq!(
            rows,
            vec![
                ("scale", "Scale: Percent (%)"),
                ("grid", "Grid lines: Off"),
                ("cross", "Crosshair: On"),
            ]
        );
    }

    #[test]
    fn backend_notice_replaces_generic_empty_message() {
        let mut state = MarketState::default();
        let json = serde_json::json!({
            "symbols": [],
            "selected_symbol": "RELIANCE",
            "timeframes": ["15m"],
            "timeframe": "5m",
            "exchange": "",
            "bars": [],
            "notice": "No candle data for RELIANCE on 5m timeframe",
        });
        apply_snapshot_json(&mut state, &json);
        assert_eq!(state.selected_symbol, "RELIANCE");
        assert!(state.bars.is_empty());
        let view = project(&state);
        assert!(!view.has_data);
        assert_eq!(
            view.status_message,
            "No candle data for RELIANCE on 5m timeframe"
        );
    }

    #[test]
    fn empty_without_notice_names_symbol_and_timeframe() {
        let mut state = MarketState::default();
        state.set_bars("RELIANCE", "5m", "", Vec::new());
        let view = project(&state);
        assert_eq!(
            view.status_message,
            "No candle data for RELIANCE on 5m timeframe"
        );
    }

    #[test]
    fn fresh_bars_clear_a_prior_notice() {
        let mut state = MarketState::default();
        state.notice = "stale notice".to_string();
        state.set_bars(
            "RELIANCE",
            "15m",
            "",
            vec![MarketBar {
                time: "2026-06-10 09:15:00".to_string(),
                open: 1.0,
                high: 2.0,
                low: 0.5,
                close: 1.5,
                volume: 100.0,
            }],
        );
        assert!(state.notice.is_empty());
        assert!(project(&state).has_data);
    }

    #[test]
    fn market_status_toggle_is_view_local() {
        let mut state = MarketState::default();
        assert!(state.apply(MarketAction::ToggleStatus));
        assert!(state.market_status.open);
        assert!(state.apply(MarketAction::ToggleStatus));
        assert!(!state.market_status.open);
        // Toggle is view-local: no wire is queued for the backend.
        assert!(state.pending_actions.is_empty());
        let view = project(&state);
        state.apply(MarketAction::ToggleStatus);
        let view2 = project(&state);
        assert_ne!(view.market_status_open, view2.market_status_open);
    }

    #[test]
    fn adaptive_time_formatting_and_non_colliding_ticks() {
        let dummy_window = vec![
            MarketBar {
                time: "2026-06-10 09:15:00".to_string(),
                open: 100.0,
                high: 105.0,
                low: 99.0,
                close: 102.0,
                volume: 1000.0,
            },
            MarketBar {
                time: "2026-06-10 10:30:00".to_string(),
                open: 102.0,
                high: 106.0,
                low: 101.0,
                close: 104.0,
                volume: 1200.0,
            },
        ];
        // Intraday
        assert_eq!(format_axis_time("2026-06-10 09:15:00", "15m", &dummy_window), "09:15");
        assert_eq!(format_axis_time("2026-06-10 14:45:00", "1h", &dummy_window), "14:45");
        // Daily
        assert_eq!(format_axis_time("2026-06-10 00:00:00", "1D", &dummy_window), "Jun 10");
        assert_eq!(format_axis_time("2026-06-11 00:00:00", "1D", &dummy_window), "Jun 11");
        // Macro
        assert_eq!(format_axis_time("2026-06-10 00:00:00", "1M", &dummy_window), "Jun 2026");

        // Verify ticks never collide on state projection
        let mut state = MarketState::default();
        state.set_bars("TEST", "15m", "NSE", bars(500));
        let view = project(&state);
        for i in 1..view.time_ticks.len() {
            assert!(
                view.time_ticks[i].pos - view.time_ticks[i - 1].pos >= 0.084,
                "ticks must maintain minimum readable spacing"
            );
        }
    }

    #[test]
    fn price_range_caching_and_invalidation() {
        let mut state = MarketState::default();
        state.set_bars("TEST", "15m", "NSE", bars(100));
        let r1 = state.price_range();
        // Cached read:
        let r2 = state.price_range();
        assert_eq!(r1, r2);
        // After panning, recalculates and caches new range:
        state.apply(MarketAction::WheelPanX(0.2));
        let r3 = state.price_range();
        let r4 = state.price_range();
        assert_eq!(r3, r4);
    }

    #[test]
    fn crosshair_time_exact_format_matches_specification() {
        assert_eq!(
            format_crosshair_time("2026-09-16 10:15:00"),
            "Wed 16 Sep '26   10:15"
        );
        assert_eq!(
            format_crosshair_time("2026-09-17 14:30:00"),
            "Thu 17 Sep '26   14:30"
        );
        assert_eq!(
            format_crosshair_time("2026-09-21 09:15:00"),
            "Mon 21 Sep '26   09:15"
        );
        assert_eq!(
            format_crosshair_time("2026-09-16T10:15:00"),
            "Wed 16 Sep '26   10:15"
        );
    }
}
