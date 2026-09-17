//! Embeddable native Market view — offscreen Slint host for the Qt shell.
//!
//! Architecture (constitution §3) mirrors `vayren-strategy-lab-view` 1:1: the
//! Qt main window owns a `SlintMarketHost` viewport that blits this crate's
//! frames; all view-model state lives in `vayren_shell::market`, and
//! `ui/market_host.slint` is a pure pass-through of the verified
//! `vayren-shell/ui/market.slint`. Interactions route through
//! `MarketState::interact` (optimistic local apply; backend-affecting actions
//! also queue a wire string the Qt host drains and replays through the SAME
//! signals a user click emits).
//!
//! Threading: every C ABI function must run on the creating thread; violations
//! return `-6`, never UB. Error codes: -1 null view · -2 null pointer ·
//! -3 invalid UTF-8 · -4 invalid JSON · -5 bad argument · -6 wrong thread ·
//! -7 platform setup failed · -9 internal panic.

slint::include_modules!();

use std::cell::RefCell;
use std::ffi::{c_char, c_float, c_int, c_uchar, c_uint};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::rc::Rc;

use slint::platform::{
    software_renderer::{MinimalSoftwareWindow, RepaintBufferType},
    Platform, PointerEventButton, WindowAdapter, WindowEvent,
};
use slint::{ComponentHandle, LogicalPosition, PhysicalSize, Rgb8Pixel, SharedString, VecModel};
use vayren_shell::market::{self, MarketAction, MarketLayoutMode, MarketState};
use vayren_shell::market_download::{self as mdownload, DownloadAction};

pub const ABI_VERSION: u32 = 1;
const MIN_SCALE: f32 = 0.25;
const MAX_SCALE: f32 = 8.0;

thread_local! {
    static PENDING_WINDOW: RefCell<Option<Rc<MinimalSoftwareWindow>>> = const { RefCell::new(None) };
}

struct EmbedPlatform;

impl Platform for EmbedPlatform {
    fn create_window_adapter(&self) -> Result<Rc<dyn WindowAdapter>, slint::PlatformError> {
        let window: Rc<MinimalSoftwareWindow> = PENDING_WINDOW
            .with(|slot| slot.borrow_mut().take())
            .expect("embed view created without a pending window");
        Ok(window as Rc<dyn WindowAdapter>)
    }
}

fn ensure_platform() -> Result<(), i32> {
    // Slint may already be initialized by another native host in this process;
    // the platforms are interchangeable, so proceed either way.
    let _ = slint::platform::set_platform(Box::new(EmbedPlatform));
    Ok(())
}

pub struct MarketView {
    window: Rc<MinimalSoftwareWindow>,
    _ui: MarketHostWindow,
    state: Rc<RefCell<MarketState>>,
    width_px: u32,
    height_px: u32,
    scale_factor: f32,
    thread: std::thread::ThreadId,
}

fn model<T: Clone + 'static>(rows: Vec<T>) -> slint::ModelRc<T> {
    Rc::new(VecModel::from(rows)).into()
}

/// Mirror of the render-test `apply_view` — same view-model, different bind
/// target. If one changes shape, so must the other.
fn apply_state(ui: &MarketHostWindow, state: &MarketState) {
    let view = market::project(state);
    ui.set_panel_visible(view.panel_visible);
    ui.set_symbol_title(view.symbol_title.into());
    ui.set_timeframe_label(view.timeframe_label.into());
    ui.set_exchange_label(view.exchange_label.into());
    ui.set_status_message(view.status_message.into());
    ui.set_has_data(view.has_data);
    ui.set_header_ohlc(view.header_ohlc.into());
    ui.set_watch_rows(model(
        view.watch_rows
            .into_iter()
            .map(|w| MarketWatchRow {
                symbol: w.symbol.into(),
                price: w.price.into(),
                change: w.change.into(),
                tone: w.tone.cell(),
                selected: w.selected,
            })
            .collect(),
    ));
    ui.set_watchlists(model(
        view.watchlists
            .into_iter()
            .map(SharedString::from)
            .collect::<Vec<_>>(),
    ));
    ui.set_watchlist_name(view.watchlist_name.into());
    ui.set_filter(view.filter.into());
    ui.set_sort_ascending(view.sort_ascending);
    let tf = |rows: Vec<market::TimeframeRow>| {
        model(
            rows.into_iter()
                .map(|t| MarketTimeframe {
                    label: t.label.into(),
                    selected: t.selected,
                })
                .collect(),
        )
    };
    ui.set_timeframes_visible(tf(view.timeframes_visible));
    ui.set_timeframes_overflow(tf(view.timeframes_overflow));
    ui.set_plot_slots(view.plot_slots);
    ui.set_candles(model(
        view.candles
            .into_iter()
            .map(|c| MarketCandle {
                x: c.x,
                o: c.open,
                h: c.high,
                l: c.low,
                c: c.close,
                vol: c.volume,
                top: c.top,
                body: c.body,
                tone: c.tone.cell(),
            })
            .collect(),
    ));
    let ticks = |rows: Vec<market::AxisTick>| {
        model(
            rows.into_iter()
                .map(|t| MarketTick {
                    pos: t.pos,
                    label: t.label.into(),
                })
                .collect(),
        )
    };
    ui.set_price_ticks(ticks(view.price_ticks));
    ui.set_time_ticks(ticks(view.time_ticks));
    ui.set_plot_segments(model(
        view.plot_segments
            .into_iter()
            .map(|s| MarketPlotSeg {
                x1: s.x1,
                y1: s.y1,
                x2: s.x2,
                y2: s.y2,
                color: s.color,
                wide: s.wide,
            })
            .collect(),
    ));
    ui.set_markers(model(
        view.markers
            .into_iter()
            .map(|m| MarketMarker {
                x: m.x,
                y: m.y,
                kind: m.kind,
                color: m.color,
                label: m.label.into(),
                pill: m.pill,
                pill_solid: m.pill_solid,
                tip: m.tip,
                pill_w: m.pill_w,
            })
            .collect(),
    ));
    ui.set_hover_x(view.hover_x);
    ui.set_hover_y(view.hover_y);
    ui.set_hover_price(view.hover_price.into());
    ui.set_hover_time(view.hover_time.into());
    ui.set_hover_volume(view.hover_volume.into());
    ui.set_hover_bull(view.hover_bull);
    ui.set_has_hover(view.has_hover);
    ui.set_indicator_rows(model(
        view.indicator_rows
            .into_iter()
            .map(|i| MarketIndicator {
                name: i.name.into(),
                visible: i.visible,
            })
            .collect(),
    ));
    ui.set_popup_open(view.popup_open);
    ui.set_popup_query(view.popup_query.into());
    ui.set_popup_category(view.popup_category.into());
    ui.set_popup_rows(model(
        view.popup_rows
            .into_iter()
            .map(|r| MarketPopupRow {
                kind: r.kind.into(),
                label: r.label.into(),
                name: r.name.into(),
                category: r.category.into(),
            })
            .collect(),
    ));
    ui.set_active_label(view.active_label.into());
    ui.set_trade_context(MarketTradeContext {
        visible: view.trade_context.visible,
        trade: view.trade_context.trade.into(),
        symbol_side: view.trade_context.symbol_side.into(),
        time: view.trade_context.time.into(),
        pnl: view.trade_context.pnl.into(),
        r: view.trade_context.r.into(),
    });
    set_dl(ui, &view.download);
}

/// Bind the Historical-Download console view (flat → host pass-through props).
fn set_dl(ui: &MarketHostWindow, d: &mdownload::DownloadView) {
    ui.set_dl_open(d.open);
    ui.set_dl_busy(d.busy);
    ui.set_dl_interval_index(d.interval_index as i32);
    ui.set_dl_interval_label(d.interval_label.clone().into());
    ui.set_dl_interval_items(model(
        d.interval_items
            .iter()
            .map(|s| SharedString::from(s.clone()))
            .collect(),
    ));
    ui.set_dl_selected_text(d.selected_text.clone().into());
    ui.set_dl_progress_text(d.progress_text.clone().into());
    ui.set_dl_stock_rows(model(
        d.stock_rows
            .iter()
            .map(|s| DlStock {
                symbol: s.symbol.clone().into(),
                selected: s.selected,
            })
            .collect(),
    ));
    ui.set_dl_chips(model(
        d.chips
            .iter()
            .map(|s| SharedString::from(s.clone()))
            .collect(),
    ));
    ui.set_dl_chips_note(d.chips_note.clone().into());
    ui.set_dl_filter(d.filter.clone().into());
    ui.set_dl_from_display(d.from_display.clone().into());
    ui.set_dl_to_display(d.to_display.clone().into());
    ui.set_dl_plan(DlPlan {
        stocks: d.plan.stocks.clone().into(),
        interval: d.plan.interval.clone().into(),
        range: d.plan.range.clone().into(),
        days: d.plan.days.clone().into(),
        rows: d.plan.rows.clone().into(),
        error: d.plan.error.clone().into(),
    });
    let s = &d.status;
    ui.set_dl_status(DlStatus {
        mode: s.mode.clone().into(),
        status: s.status.clone().into(),
        symbol: s.symbol.clone().into(),
        interval: s.interval.clone().into(),
        range: s.range.clone().into(),
        chunk: s.chunk.clone().into(),
        rows: s.rows.clone().into(),
        coverage: s.coverage.clone().into(),
        progress_pct: s.progress_pct,
        progress_note: s.progress_note.clone().into(),
        perf_rows: s.perf_rows.clone().into(),
        perf_elapsed: s.perf_elapsed.clone().into(),
        perf_eta: s.perf_eta.clone().into(),
        perf_size: s.perf_size.clone().into(),
        complete_status: s.complete_status.clone().into(),
        complete_rows: s.complete_rows.clone().into(),
        complete_coverage: s.complete_coverage.clone().into(),
        complete_duration: s.complete_duration.clone().into(),
        complete_size: s.complete_size.clone().into(),
        error_line: s.error_line.clone().into(),
        error_detail: s.error_detail.clone().into(),
        error_details_shown: s.error_details_shown,
        cov_symbol: s.cov_symbol.clone().into(),
        cov_interval: s.cov_interval.clone().into(),
        cov_range: s.cov_range.clone().into(),
        cov_coverage: s.cov_coverage.clone().into(),
        cov_rows: s.cov_rows.clone().into(),
        provider_name: s.provider_name.clone().into(),
        provider_status: s.provider_status.clone().into(),
        provider_label: s.provider_label.clone().into(),
        provider_detail: s.provider_detail.clone().into(),
        configure_visible: s.configure_visible,
        advanced_visible: s.advanced_visible,
        advanced_open: s.advanced_open,
        env_visible: s.env_visible,
        broker_caps: s.broker_caps.clone().into(),
        broker_error: s.broker_error.clone().into(),
    });
    ui.set_dl_brokers(model(
        d.brokers
            .iter()
            .map(|s| SharedString::from(s.clone()))
            .collect(),
    ));
    ui.set_dl_broker_index(d.broker_index as i32);
    ui.set_dl_log(model(
        d.log
            .iter()
            .map(|s| SharedString::from(s.clone()))
            .collect(),
    ));
    ui.set_dl_log_expanded(d.log_expanded);
    ui.set_dl_cal_open(d.cal_open);
    ui.set_dl_cal_title(d.cal_title.clone().into());
    ui.set_dl_cal_days(model(
        d.cal_days
            .iter()
            .map(|c| DlCalDay {
                day: c.day,
                label: c.label.clone().into(),
                x: c.x,
                y: c.y,
            })
            .collect(),
    ));
    ui.set_dl_weekdays(model(
        d.weekday_labels
            .iter()
            .map(|s| SharedString::from(s.clone()))
            .collect(),
    ));
    ui.set_dl_cred_open(d.cred_open);
    ui.set_dl_cred_fields(model(
        d.cred_fields
            .iter()
            .map(|f| DlCredField {
                key: f.key.clone().into(),
                label: f.label.clone().into(),
                secret: f.secret,
                value: f.value.clone().into(),
                revealed: f.revealed,
            })
            .collect(),
    ));
    ui.set_dl_cred_has_stored(d.cred_has_stored);
    ui.set_dl_cred_status(d.cred_status.clone().into());
    ui.set_dl_confirm_clear(d.confirm_clear);
}

/// Report a Slint interaction into the central state, then re-bind the view.
fn report(
    ui: &MarketHostWindow,
    state: &Rc<RefCell<MarketState>>,
    wire: &str,
    action: MarketAction,
) {
    {
        let mut guard = state.borrow_mut();
        guard.interact(wire, action);
    }
    if let Some(ui) = ui.as_weak().upgrade() {
        apply_state(&ui, &state.borrow());
    }
}

/// Report a download-console interaction and re-bind the view.
fn dl_refresh(ui: &MarketHostWindow, state: &Rc<RefCell<MarketState>>, action: DownloadAction) {
    state.borrow_mut().interact_download(action);
    apply_state(ui, &state.borrow());
}

fn wire_dl(ui: &MarketHostWindow, state: &Rc<RefCell<MarketState>>) {
    macro_rules! dl_unit {
        ($set:ident, $action:expr) => {{
            let strong = state.clone();
            let weak = ui.as_weak();
            ui.$set(move || {
                if let Some(ui) = weak.upgrade() {
                    dl_refresh(&ui, &strong, $action);
                }
            });
        }};
    }
    macro_rules! dl_str {
        ($set:ident, $make:expr) => {{
            let strong = state.clone();
            let weak = ui.as_weak();
            ui.$set(move |v: SharedString| {
                if let Some(ui) = weak.upgrade() {
                    dl_refresh(&ui, &strong, $make(v.to_string()));
                }
            });
        }};
    }
    dl_unit!(on_dl_toggle, DownloadAction::Toggle);
    dl_unit!(on_dl_select_all, DownloadAction::SelectAll);
    dl_unit!(on_dl_clear_all, DownloadAction::ClearAll);
    dl_unit!(on_dl_close_cal, DownloadAction::CloseCal);
    dl_unit!(on_dl_cal_prev, DownloadAction::CalPrevMonth);
    dl_unit!(on_dl_cal_next, DownloadAction::CalNextMonth);
    dl_unit!(on_dl_download, DownloadAction::Download);
    dl_unit!(on_dl_coverage, DownloadAction::CheckCoverage);
    dl_unit!(on_dl_cancel, DownloadAction::Cancel);
    dl_unit!(on_dl_retry, DownloadAction::Retry);
    dl_unit!(on_dl_view_coverage, DownloadAction::ViewCoverage);
    dl_unit!(on_dl_error_details, DownloadAction::ToggleErrorDetails);
    dl_unit!(on_dl_advanced, DownloadAction::ToggleAdvanced);
    dl_unit!(on_dl_log_toggle, DownloadAction::ToggleLog);
    dl_unit!(on_dl_log_clear, DownloadAction::ClearLog);
    dl_unit!(on_dl_open_creds, DownloadAction::OpenCreds);
    dl_unit!(on_dl_close_creds, DownloadAction::CloseCreds);
    dl_unit!(on_dl_cred_test, DownloadAction::CredTest);
    dl_unit!(on_dl_cred_save, DownloadAction::CredSave);
    dl_unit!(on_dl_cred_clear, DownloadAction::CredClear);
    dl_str!(on_dl_interval, |v| DownloadAction::Interval(v));
    dl_str!(on_dl_toggle_symbol, |v| DownloadAction::ToggleSymbol(v));
    dl_str!(on_dl_filter_changed, |v| DownloadAction::SetFilter(v));
    dl_str!(on_dl_open_cal, |v| DownloadAction::OpenCal(v));
    dl_str!(on_dl_quick, |v| DownloadAction::QuickRange(v));
    dl_str!(on_dl_broker, |v| DownloadAction::Broker(v));
    dl_str!(on_dl_cred_reveal, |v| DownloadAction::CredReveal(v));
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_dl_pick_day(move |d: i32| {
            if let Some(ui) = weak.upgrade() {
                dl_refresh(&ui, &strong, DownloadAction::PickDay(d));
            }
        });
    }
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_dl_cred_confirm(move |yes: bool| {
            if let Some(ui) = weak.upgrade() {
                dl_refresh(&ui, &strong, DownloadAction::CredConfirmClear(yes));
            }
        });
    }
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_dl_cred_field(move |k: SharedString, v: SharedString| {
            if let Some(ui) = weak.upgrade() {
                dl_refresh(
                    &ui,
                    &strong,
                    DownloadAction::CredField(k.to_string(), v.to_string()),
                );
            }
        });
    }
}

fn wire_view(ui: &MarketHostWindow, state: Rc<RefCell<MarketState>>) {
    wire_dl(ui, &state);
    macro_rules! bind {
        ($set:ident, $wire:expr, $action:expr) => {{
            let strong = state.clone();
            let weak = ui.as_weak();
            ui.$set(move || {
                let Some(ui) = weak.upgrade() else { return };
                report(&ui, &strong, $wire, $action);
            });
        }};
    }
    bind!(on_panel_toggle, "panel", MarketAction::PanelToggle);
    bind!(on_sort_asc, "sort", MarketAction::SortAsc);
    bind!(on_sort_desc, "sort", MarketAction::SortDesc);
    bind!(
        on_add_watchlist,
        "watchlist:add",
        MarketAction::AddWatchlist
    );
    bind!(
        on_remove_watchlist,
        "watchlist:remove",
        MarketAction::RemoveWatchlist
    );
    bind!(on_reset_view, "reset", MarketAction::ResetView);
    bind!(on_hover_left, "", MarketAction::HoverLeft);
    bind!(on_drag_end, "", MarketAction::DragEnd);
    bind!(on_price_drag_end, "", MarketAction::PriceDragEnd);
    bind!(on_price_reset, "", MarketAction::PriceReset);
    bind!(on_trade_prev, "trade:prev", MarketAction::TradePrev);
    bind!(on_trade_next, "trade:next", MarketAction::TradeNext);
    bind!(on_trade_open, "trade:open", MarketAction::TradeOpen);

    macro_rules! bind_str {
        ($set:ident, $make:expr) => {{
            let strong = state.clone();
            let weak = ui.as_weak();
            ui.$set(move |text: SharedString| {
                let Some(ui) = weak.upgrade() else { return };
                let (wire, action) = $make(text.to_string());
                report(&ui, &strong, &wire, action);
            });
        }};
    }
    bind_str!(on_symbol_selected, |s| {
        (format!("select:{s}"), MarketAction::SelectSymbol(s))
    });
    bind_str!(on_timeframe_picked, |s| (
        format!("timeframe:{s}"),
        MarketAction::SelectTimeframe(s)
    ));
    bind_str!(on_filter_changed, |s| (
        String::new(),
        MarketAction::SetFilter(s)
    ));
    bind_str!(on_select_watchlist, |s| (
        format!("watchlist:select:{s}"),
        MarketAction::SelectWatchlist(s)
    ));
    bind_str!(on_add_indicator, |s| (
        format!("indicator:add:{s}"),
        MarketAction::AddIndicator(s)
    ));
    bind_str!(on_toggle_indicator_visible, |s| (
        format!("indicator:vis:{s}"),
        MarketAction::ToggleIndicatorVisible(s)
    ));
    bind_str!(on_settings_indicator, |s| (
        format!("indicator:settings:{s}"),
        MarketAction::SettingsIndicator(s)
    ));
    bind_str!(on_source_indicator, |s| (
        format!("indicator:source:{s}"),
        MarketAction::SourceIndicator(s)
    ));
    bind_str!(on_remove_indicator, |s| (
        format!("indicator:rm:{s}"),
        MarketAction::RemoveIndicator(s)
    ));
    bind_str!(on_indicator_query, |s| (
        String::new(),
        MarketAction::IndicatorQuery(s)
    ));
    bind_str!(on_indicator_category, |s| (
        String::new(),
        MarketAction::IndicatorCategory(s)
    ));

    // bool / float / 2-float callbacks.
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_indicator_popup(move |open: bool| {
            let Some(ui) = weak.upgrade() else { return };
            report(&ui, &strong, "", MarketAction::IndicatorPopup(open));
        });
    }
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_hover_moved(move |x: f32, y: f32| {
            let Some(ui) = weak.upgrade() else { return };
            report(&ui, &strong, "", MarketAction::HoverMoved(x, y));
        });
    }
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_wheel_zoom(move |x: f32, steps: f32| {
            let Some(ui) = weak.upgrade() else { return };
            report(&ui, &strong, "", MarketAction::WheelZoom(x, steps));
        });
    }
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_wheel_pan(move |frac: f32| {
            let Some(ui) = weak.upgrade() else { return };
            report(&ui, &strong, "", MarketAction::WheelPanX(frac));
        });
    }
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_drag_start(move |x: f32, y: f32| {
            let Some(ui) = weak.upgrade() else { return };
            report(&ui, &strong, "", MarketAction::DragStart(x, y));
        });
    }
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_drag_move(move |x: f32, y: f32| {
            let Some(ui) = weak.upgrade() else { return };
            report(&ui, &strong, "", MarketAction::DragMove(x, y));
        });
    }
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_price_zoom(move |steps: f32, y: f32| {
            let Some(ui) = weak.upgrade() else { return };
            report(&ui, &strong, "", MarketAction::PriceZoom(steps, y));
        });
    }
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_price_drag(move |notches: f32, anchor: f32| {
            let Some(ui) = weak.upgrade() else { return };
            report(&ui, &strong, "", MarketAction::PriceDrag(notches, anchor));
        });
    }
}

fn view_of<'a>(ptr: *mut MarketView) -> Result<&'a mut MarketView, i32> {
    if ptr.is_null() {
        return Err(-1);
    }
    // SAFETY: non-null handles come only from `create`, live until `destroy`,
    // and every entry point checks thread affinity first.
    Ok(unsafe { &mut *ptr })
}

fn cstr(ptr: *const c_char) -> Result<&'static str, i32> {
    if ptr.is_null() {
        return Err(-2);
    }
    // SAFETY: contract requires valid NUL-terminated UTF-8 for the call.
    unsafe { std::ffi::CStr::from_ptr(ptr) }
        .to_str()
        .map_err(|_| -3)
}

fn guard<F>(action: F) -> i32
where
    F: FnOnce() -> Result<i32, i32>,
{
    match catch_unwind(AssertUnwindSafe(action)) {
        Ok(result) => result.unwrap_or_else(|code| code),
        Err(_) => -9,
    }
}

#[no_mangle]
pub extern "C" fn vayren_market_abi_version() -> c_uint {
    ABI_VERSION
}

#[no_mangle]
pub extern "C" fn vayren_market_view_create(
    width_px: c_uint,
    height_px: c_uint,
    scale_factor: c_float,
) -> *mut MarketView {
    match catch_unwind(AssertUnwindSafe(|| {
        MarketView::create(width_px, height_px, scale_factor)
    })) {
        Ok(Ok(view)) => Box::into_raw(view),
        _ => std::ptr::null_mut(),
    }
}

#[no_mangle]
pub extern "C" fn vayren_market_view_destroy(view: *mut MarketView) {
    let _ = catch_unwind(AssertUnwindSafe(|| {
        if !view.is_null() {
            // SAFETY: inverse of `Box::into_raw` in `create`.
            unsafe { drop(Box::from_raw(view)) };
        }
    }));
}

#[no_mangle]
pub extern "C" fn vayren_market_view_resize(
    view: *mut MarketView,
    width_px: c_uint,
    height_px: c_uint,
    scale_factor: c_float,
) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        if width_px == 0 || height_px == 0 {
            return Err(-5);
        }
        view.width_px = width_px;
        view.height_px = height_px;
        view.scale_factor = scale_factor.clamp(MIN_SCALE, MAX_SCALE);
        view.apply_geometry();
        Ok(0)
    })
}

/// Replace backend-owned state from the bridge JSON (see
/// `market_snapshot_dict` on the Python side; parsed defensively by
/// `market::apply_snapshot_json`). Returns 0 on success, -4 on invalid JSON.
#[no_mangle]
pub extern "C" fn vayren_market_view_set_snapshot(
    view: *mut MarketView,
    json_utf8: *const c_char,
) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        let text = cstr(json_utf8)?;
        let value: serde_json::Value = serde_json::from_str(text).map_err(|_| -4)?;
        {
            let mut state = view.state.borrow_mut();
            market::apply_snapshot_json(&mut state, &value);
        }
        apply_state(&view._ui, &view.state.borrow());
        view.window.request_redraw();
        Ok(0)
    })
}

#[no_mangle]
pub extern "C" fn vayren_market_view_tick(view: *mut MarketView) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        slint::platform::update_timers_and_animations();
        Ok(0)
    })
}

/// Pop the oldest queued user action into `out_utf8` (`out_len` bytes).
/// Returns bytes written (>0), 0 when the queue is empty, -5 when the buffer
/// is too small (the action stays queued; the host retries with room).
#[no_mangle]
pub extern "C" fn vayren_market_view_next_action(
    view: *mut MarketView,
    out_utf8: *mut c_uchar,
    out_len: usize,
) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        if out_utf8.is_null() {
            return Err(-2);
        }
        let action = { view.state.borrow().pending_actions.first().cloned() };
        let Some(action) = action else { return Ok(0) };
        let bytes = action.as_bytes();
        if bytes.len() > out_len {
            return Err(-5);
        }
        // SAFETY: contract guarantees a writable `out_len`-byte buffer and we
        // checked `bytes.len() <= out_len`.
        let out = unsafe { std::slice::from_raw_parts_mut(out_utf8, bytes.len()) };
        out.copy_from_slice(bytes);
        view.state.borrow_mut().pending_actions.remove(0);
        Ok(bytes.len() as i32)
    })
}

#[no_mangle]
pub extern "C" fn vayren_market_view_render(
    view: *mut MarketView,
    out_rgb: *mut c_uchar,
    out_len: usize,
) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        if out_rgb.is_null() {
            return Err(-2);
        }
        // SAFETY: contract guarantees a writable `out_len`-byte buffer.
        let out = unsafe { std::slice::from_raw_parts_mut(out_rgb, out_len) };
        Ok(if view.render_into(out)? { 1 } else { 0 })
    })
}

fn pointer_event(
    view: *mut MarketView,
    make: impl FnOnce(LogicalPosition) -> WindowEvent + std::panic::UnwindSafe,
    x: c_float,
    y: c_float,
) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        if !x.is_finite() || !y.is_finite() {
            return Err(-5);
        }
        view.window.dispatch_event(make(LogicalPosition::new(x, y)));
        Ok(0)
    })
}

#[no_mangle]
pub extern "C" fn vayren_market_view_pointer_move(
    view: *mut MarketView,
    x: c_float,
    y: c_float,
) -> c_int {
    pointer_event(
        view,
        |position| WindowEvent::PointerMoved { position },
        x,
        y,
    )
}

fn pointer_button(button: c_int) -> Result<PointerEventButton, i32> {
    match button {
        0 => Ok(PointerEventButton::Left),
        1 => Ok(PointerEventButton::Right),
        2 => Ok(PointerEventButton::Middle),
        _ => Err(-5),
    }
}

fn pointer_button_event(
    view: *mut MarketView,
    pressed: bool,
    x: c_float,
    y: c_float,
    button: c_int,
) -> c_int {
    let resolved = match pointer_button(button) {
        Ok(b) => b,
        Err(code) => return code,
    };
    pointer_event(
        view,
        |position| {
            if pressed {
                WindowEvent::PointerPressed {
                    position,
                    button: resolved,
                }
            } else {
                WindowEvent::PointerReleased {
                    position,
                    button: resolved,
                }
            }
        },
        x,
        y,
    )
}

#[no_mangle]
pub extern "C" fn vayren_market_view_pointer_press(
    view: *mut MarketView,
    x: c_float,
    y: c_float,
    button: c_int,
) -> c_int {
    pointer_button_event(view, true, x, y, button)
}

#[no_mangle]
pub extern "C" fn vayren_market_view_pointer_release(
    view: *mut MarketView,
    x: c_float,
    y: c_float,
    button: c_int,
) -> c_int {
    pointer_button_event(view, false, x, y, button)
}

#[no_mangle]
pub extern "C" fn vayren_market_view_pointer_leave(view: *mut MarketView) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        view.window.dispatch_event(WindowEvent::PointerExited);
        Ok(0)
    })
}

#[no_mangle]
pub extern "C" fn vayren_market_view_scroll(
    view: *mut MarketView,
    x: c_float,
    y: c_float,
    delta_x: c_float,
    delta_y: c_float,
) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        if !delta_x.is_finite() || !delta_y.is_finite() {
            return Err(-5);
        }
        view.window.dispatch_event(WindowEvent::PointerScrolled {
            position: LogicalPosition::new(x, y),
            delta_x,
            delta_y,
        });
        Ok(0)
    })
}

#[no_mangle]
pub extern "C" fn vayren_market_view_key(
    view: *mut MarketView,
    text_utf8: *const c_char,
    pressed: c_int,
) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        let text: SharedString = cstr(text_utf8)?.into();
        if pressed != 0 {
            view.window.dispatch_event(WindowEvent::KeyPressed { text });
        } else {
            view.window
                .dispatch_event(WindowEvent::KeyReleased { text });
        }
        Ok(0)
    })
}

impl MarketView {
    fn create(width_px: u32, height_px: u32, scale_factor: f32) -> Result<Box<Self>, i32> {
        if width_px == 0 || height_px == 0 {
            return Err(-5);
        }
        ensure_platform()?;
        let scale = scale_factor.clamp(MIN_SCALE, MAX_SCALE);
        let window = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
        PENDING_WINDOW.with(|slot| slot.borrow_mut().replace(window.clone()));
        let ui = MarketHostWindow::new().map_err(|_| -7)?;
        PENDING_WINDOW.with(|slot| {
            slot.borrow_mut().take();
        });
        let state = Rc::new(RefCell::new(MarketState::default()));
        wire_view(&ui, state.clone());
        apply_state(&ui, &state.borrow());
        let mut view = Box::new(MarketView {
            window,
            _ui: ui,
            state,
            width_px,
            height_px,
            scale_factor: scale,
            thread: std::thread::current().id(),
        });
        view.apply_geometry();
        Ok(view)
    }

    fn check_thread(&self) -> Result<(), i32> {
        if std::thread::current().id() == self.thread {
            Ok(())
        } else {
            Err(-6)
        }
    }

    fn apply_geometry(&mut self) {
        self.window
            .set_size(PhysicalSize::new(self.width_px, self.height_px));
        self.window.dispatch_event(WindowEvent::ScaleFactorChanged {
            scale_factor: self.scale_factor,
        });
        let logical = self.width_px as f32 / self.scale_factor.max(MIN_SCALE);
        let mode = market::layout_mode(logical);
        self._ui.set_wide(mode == MarketLayoutMode::Wide);
        self._ui.set_medium(mode != MarketLayoutMode::Narrow);
        self.window.request_redraw();
    }

    fn render_into(&self, out: &mut [u8]) -> Result<bool, i32> {
        let need = self.width_px as usize * self.height_px as usize * 3;
        if out.len() < need {
            return Err(-5);
        }
        // SAFETY: Rgb8Pixel is three contiguous bytes (r, g, b); the caller
        // guarantees `out` outlives this call and is exclusively borrowed.
        let pixels =
            unsafe { std::slice::from_raw_parts_mut(out.as_mut_ptr() as *mut Rgb8Pixel, need / 3) };
        let stride = self.width_px as usize;
        let painted = self.window.draw_if_needed(|renderer| {
            let _ = renderer.render(pixels, stride);
        });
        Ok(painted)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn snapshot_feeds_canonical_state_end_to_end() {
        let mut state = MarketState::default();
        let value: serde_json::Value = serde_json::from_str(
            r#"{"symbols":[{"symbol":"RELIANCE","price":2451.10,"change_pct":1.24}],
               "selected_symbol":"RELIANCE","timeframes":["15m","1h"],
               "timeframe":"15m","exchange":"NSE",
               "bars":[{"time":"2024-01-02T09:15:00","open":2440.0,"high":2455.5,
               "low":2438.0,"close":2451.10,"volume":1200400.0}],
               "indicators":{"SMA":true}}"#,
        )
        .unwrap();
        market::apply_snapshot_json(&mut state, &value);
        assert_eq!(state.symbols.len(), 1);
        let view = market::project(&state);
        assert!(view.has_data);
        assert!(view.header_ohlc.contains("2,451.10"));
        assert!(view
            .indicator_rows
            .iter()
            .any(|i| i.name == "SMA" && i.visible));
    }

    #[test]
    fn empty_snapshot_stays_loading() {
        let mut state = MarketState::default();
        let value: serde_json::Value =
            serde_json::from_str(r#"{"symbols":[],"status":"loading"}"#).unwrap();
        market::apply_snapshot_json(&mut state, &value);
        assert_eq!(state.status, market::MarketStatus::Loading);
        assert!(!market::project(&state).has_data);
    }
}
