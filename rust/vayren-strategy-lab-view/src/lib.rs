//! Embeddable native Strategy Lab view — offscreen Slint host for the Qt shell.
//!
//! Architecture (constitution §3: Rust+Slint owns ALL native UI state and
//! presentation) mirrors `vayren-portfolio-view` 1:1:
//!
//! ```text
//! Qt main window (SAME process, Qt GUI thread only)
//!   │  SlintStrategyLabHost (dumb viewport: blits pixels, forwards events)
//!   │  C ABI below (plain integers, UTF-8 JSON, RGB bytes — no objects)
//!   ▼
//! THIS crate (Rust view-model, reused from vayren-shell — single owner)
//!   │  lab::LabState / lab::project / lab::apply_snapshot_json
//!   │  LabHostWindow (hosts the EXISTING verified StrategyLabScreen;
//!   │    ui/lab_host.slint is pure pass-through, zero visual delta)
//!   │  MinimalSoftwareWindow (documented Slint offscreen path, no event loop)
//! ```
//!
//! Interaction: Slint callbacks route through `LabState::interaction_*` (the
//! view reacts optimistically) and queue a pending action; the Qt host drains
//! actions via `vayren_strategy_lab_view_next_action` and forwards them to the
//! Python backend, whose next snapshot echoes the authoritative result.
//!
//! Threading: every C ABI function must be called on the SAME thread that
//! created the view. Violations return `-6`, never UB.
//!
//! Error codes: `-1` null view · `-2` null pointer · `-3` invalid UTF-8 ·
//! `-4` invalid JSON · `-5` bad argument · `-6` wrong thread ·
//! `-7` platform setup failed · `-9` internal panic.

slint::include_modules!();

use std::cell::RefCell;
use std::ffi::{c_char, c_float, c_int, c_uchar, c_uint};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::rc::Rc;

use slint::platform::{
    software_renderer::{MinimalSoftwareWindow, RepaintBufferType},
    Platform, PointerEventButton, WindowAdapter, WindowEvent,
};
use slint::{ComponentHandle, LogicalPosition, PhysicalSize, Rgb8Pixel};
use vayren_shell::lab::{self, LabState};

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
    match slint::platform::set_platform(Box::new(EmbedPlatform)) {
        Ok(()) => Ok(()),
        // Slint already initialized in this process (other native hosts):
        // this platform is interchangeable, proceed.
        Err(_) => Ok(()),
    }
}

pub struct LabView {
    window: Rc<MinimalSoftwareWindow>,
    _ui: LabHostWindow,
    state: Rc<RefCell<LabState>>,
    width_px: u32,
    height_px: u32,
    scale_factor: f32,
    thread: std::thread::ThreadId,
}

/// Mirror of `shell::apply_lab` on the standalone shell — same view-model,
/// different bind target. If one changes shape, so must the other.
fn apply_state(ui: &LabHostWindow, state: &LabState) {
    let view = lab::project(state);
    ui.set_lab(LabHeader {
        has_strategy: view.has_strategy,
        name: view.name.into(),
        description: view.description.into(),
        tags: view.tags.into(),
        version: view.version.into(),
        modified: view.modified.into(),
        last_backtest: view.last_backtest.into(),
        state_label: view.state_label.into(),
        state_tone: view.state_tone,
        mode: view.mode,
        outdated: view.outdated,
        run_enabled: view.run_enabled,
        universe: view.universe.into(),
        timeframe: view.timeframe.into(),
        dates: view.dates.into(),
        capital: view.capital.into(),
        cost: view.cost.into(),
        show_results: view.show_results,
        tab: view.tab as i32,
        summary_line: view.summary_line.into(),
        empty_reason: view.empty_reason.into(),
        equity_caption: view.equity_caption.into(),
        drawdown_caption: view.drawdown_caption.into(),
        ranking_count: view.ranking_count.into(),
        run_stop: view.run_stop,
        verdict_label: view.verdict_label.into(),
        verdict_note: view.verdict_note.into(),
        verdict_tone: view.verdict_tone,
        progress_label: view.progress_label.into(),
        run_label: view.run_label.into(),
        code: view.code.into(),
        dirty: view.dirty,
        cfg_universe_csv: view.cfg_universe_csv.into(),
        timeframe_index: view.timeframe_index,
        cfg_dates_start: view.cfg_dates_start.into(),
        cfg_dates_end: view.cfg_dates_end.into(),
        cfg_capital: view.cfg_capital.into(),
        config_error: view.config_error.into(),
        rank_search: view.rank_search.into(),
        rank_desc: view.rank_desc,
        rankby_current: view.rankby_current,
        trade_needle: view.trade_needle.into(),
        trade_symbol: view.trade_symbol.into(),
        trade_filters_active: view.trade_filters_active,
        selected_trade: view.selected_trade,
        compare_side: view.compare_side,
        is_compare: view.is_compare,
        detail_symbol: view
            .detail
            .as_ref()
            .map(|d| d.symbol.clone().into())
            .unwrap_or_default(),
        detail_title: view
            .detail
            .as_ref()
            .map(|d| d.title.clone().into())
            .unwrap_or_default(),
        detail_stats: view
            .detail
            .as_ref()
            .map(|d| d.stats.clone().into())
            .unwrap_or_default(),
        detail_caption: view
            .detail
            .as_ref()
            .map(|d| d.caption.clone().into())
            .unwrap_or_default(),
        cmp_verdict: view.compare.banner_verdict.into(),
        cmp_reason: view.compare.banner_reason.into(),
        cmp_tone: view.compare.banner_tone,
        cmp_trade_summary: view.compare.trade_summary.into(),
        cmp_stale: view.compare.stale_notice.into(),
        cmp_scope: view.compare.board_scope.into(),
        cmp_leader: view.compare.board_leader.into(),
        cmp_has_sides: view.compare.has_sides,
        sym_open: view.sym_open,
        sym_search: view.sym_search.into(),
        sym_button_line: view.sym_button_line.into(),
        sym_count_line: view.sym_count_line.into(),
    });
    ui.set_library(
        Rc::new(slint::VecModel::from(
            view.library
                .into_iter()
                .map(|r| LabLibraryRow {
                    real_index: r.real_index,
                    name: r.name.into(),
                    description: r.description.into(),
                    state_label: r.state_label.into(),
                    favorite: r.favorite,
                    selected: r.selected,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_kpis(
        Rc::new(slint::VecModel::from(
            view.kpis
                .into_iter()
                .map(|k| LabKpi {
                    label: k.label.into(),
                    value: k.value.into(),
                    tone: k.tone,
                    emphasized: k.emphasized,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_ranking(
        Rc::new(slint::VecModel::from(
            view.ranking
                .into_iter()
                .map(|r| LabRankRow {
                    rank: r.rank.into(),
                    symbol: r.symbol.into(),
                    pnl: r.pnl.into(),
                    ret: r.ret.into(),
                    trades: r.trades.into(),
                    win: r.win.into(),
                    pf: r.pf.into(),
                    dd: r.dd.into(),
                    sharpe: r.sharpe.into(),
                    pnl_tone: r.pnl_tone.cell(),
                    pf_tone: r.pf_tone.cell(),
                    unranked: r.unranked,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_trades(
        Rc::new(slint::VecModel::from(
            view.trades
                .into_iter()
                .map(|t| LabTradeRow {
                    no: t.no.into(),
                    abs_index: t.abs_index,
                    symbol: t.symbol.into(),
                    side: t.side.into(),
                    entry: t.entry.into(),
                    entry_px: t.entry_px.into(),
                    exit: t.exit.into(),
                    exit_px: t.exit_px.into(),
                    pnl: t.pnl.into(),
                    r: t.r.into(),
                    bars: t.bars.into(),
                    reason: t.reason.into(),
                    pnl_tone: t.pnl_tone.cell(),
                    selected: t.selected,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    let to_points = |series: &[(f32, f32)]| -> Vec<LabPoint> {
        series
            .iter()
            .map(|(x, y)| LabPoint {
                x: x * 1000.0,
                y: y * 300.0,
            })
            .collect()
    };
    ui.set_equity(Rc::new(slint::VecModel::from(to_points(&view.equity))).into());
    ui.set_drawdown(Rc::new(slint::VecModel::from(to_points(&view.drawdown))).into());
    ui.set_params(
        Rc::new(slint::VecModel::from(
            view.params
                .into_iter()
                .map(|p| LabParam {
                    key: p.key.into(),
                    label: p.label.into(),
                    value: p.value.into(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_timeframes(
        Rc::new(slint::VecModel::from(
            view.timeframes
                .into_iter()
                .map(slint::SharedString::from)
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_rankby_labels(
        Rc::new(slint::VecModel::from(
            view.rankby_labels
                .into_iter()
                .map(slint::SharedString::from)
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_rank_heads(
        Rc::new(slint::VecModel::from(
            view.rank_heads
                .into_iter()
                .map(slint::SharedString::from)
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_trade_heads(
        Rc::new(slint::VecModel::from(
            view.trade_heads
                .into_iter()
                .map(slint::SharedString::from)
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    let detail = view.detail.unwrap_or_default();
    ui.set_detail_metrics(
        Rc::new(slint::VecModel::from(
            detail
                .metrics
                .into_iter()
                .map(|m| LabDetailMetric {
                    label: m.label.into(),
                    value: m.value.into(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_detail_equity(Rc::new(slint::VecModel::from(to_points(&detail.equity))).into());
    ui.set_board_labels(
        Rc::new(slint::VecModel::from(
            view.compare
                .board
                .iter()
                .map(|r| slint::SharedString::from(r.label.clone()))
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_board_columns(
        Rc::new(slint::VecModel::from(
            view.compare
                .board_symbols
                .iter()
                .map(slint::SharedString::from)
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_board_trades(
        Rc::new(slint::VecModel::from(
            view.compare
                .board_trades
                .into_iter()
                .map(slint::SharedString::from)
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    let cols = view.compare.board_symbols.len().max(1) as i32;
    ui.set_board_cols(cols);
    ui.set_board_cells(
        Rc::new(slint::VecModel::from(
            view.compare
                .board
                .iter()
                .flat_map(|r| r.cells.iter())
                .map(|c| LabBoardCell {
                    symbol: c.symbol.clone().into(),
                    text: c.text.clone().into(),
                    best: c.best,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_matrix(
        Rc::new(slint::VecModel::from(
            view.compare
                .matrix
                .into_iter()
                .map(|m| LabMatrixRow {
                    key: m.key.into(),
                    buy: m.buy.into(),
                    sell: m.sell.into(),
                    winner: m.winner,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_compare_ranking(
        Rc::new(slint::VecModel::from(
            view.compare
                .ranking
                .into_iter()
                .map(|r| LabRankRow {
                    rank: r.rank.into(),
                    symbol: r.symbol.into(),
                    pnl: r.pnl.into(),
                    ret: r.ret.into(),
                    trades: r.trades.into(),
                    win: r.win.into(),
                    pf: r.pf.into(),
                    dd: r.dd.into(),
                    sharpe: r.sharpe.into(),
                    pnl_tone: r.pnl_tone.cell(),
                    pf_tone: r.pf_tone.cell(),
                    unranked: r.unranked,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_equity_buy(Rc::new(slint::VecModel::from(to_points(&view.compare.equity_buy))).into());
    ui.set_equity_sell(Rc::new(slint::VecModel::from(to_points(&view.compare.equity_sell))).into());
    ui.set_drawdown_buy(
        Rc::new(slint::VecModel::from(to_points(&view.compare.drawdown_buy))).into(),
    );
    ui.set_drawdown_sell(
        Rc::new(slint::VecModel::from(to_points(
            &view.compare.drawdown_sell,
        )))
        .into(),
    );
    ui.set_sym_visible(
        Rc::new(slint::VecModel::from(
            view.sym_visible
                .into_iter()
                .map(slint::SharedString::from)
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_sym_visible_on(Rc::new(slint::VecModel::from(view.sym_visible_on)).into());
    ui.set_filter_active(lab::filter_kind(state.filter));
}

fn wire_view(ui: &LabHostWindow, state: Rc<RefCell<LabState>>) {
    // Slint reports, Rust mutates centrally (optimistic local + queued action).
    let bind =
        |ui: &LabHostWindow, state: &Rc<RefCell<LabState>>, handler: fn(&mut LabState, i32)| {
            let strong = state.clone();
            let weak = ui.as_weak();
            move |arg: i32| {
                {
                    let mut guard = strong.borrow_mut();
                    handler(&mut guard, arg);
                }
                if let Some(ui) = weak.upgrade() {
                    apply_state(&ui, &strong.borrow());
                }
            }
        };
    ui.on_library_picked(bind(ui, &state, |s, i| {
        s.interaction_select(i.max(0) as usize);
    }));
    ui.on_filter_picked(bind(ui, &state, |s, i| {
        s.interaction_filter(match i {
            1 => lab::LibFilter::Favorites,
            2 => lab::LibFilter::Recent,
            _ => lab::LibFilter::All,
        });
    }));
    ui.on_mode_picked(bind(ui, &state, |s, i| {
        s.interaction_mode(lab::LabMode::from_kind(i));
    }));
    ui.on_tab_picked(bind(ui, &state, |s, i| {
        s.interaction_tab(i.max(0) as usize)
    }));
    let strong = state.clone();
    let weak = ui.as_weak();
    ui.on_run_requested(move || {
        strong.borrow_mut().interaction_run();
        if let Some(ui) = weak.upgrade() {
            apply_state(&ui, &strong.borrow());
        }
    });
    let strong = state.clone();
    let weak = ui.as_weak();
    ui.on_search_changed(move |text| {
        strong.borrow_mut().interaction_search(text.as_str());
        if let Some(ui) = weak.upgrade() {
            apply_state(&ui, &strong.borrow());
        }
    });
    let bind0 = |ui: &LabHostWindow, state: &Rc<RefCell<LabState>>, handler: fn(&mut LabState)| {
        let strong = state.clone();
        let weak = ui.as_weak();
        move || {
            {
                let mut guard = strong.borrow_mut();
                handler(&mut guard);
            }
            if let Some(ui) = weak.upgrade() {
                apply_state(&ui, &strong.borrow());
            }
        }
    };
    ui.on_save_requested(bind0(ui, &state, |s| s.interaction_save()));
    ui.on_compile_requested(bind0(ui, &state, |s| s.interaction_compile()));
    ui.on_new_requested(bind0(ui, &state, |s| s.interaction_simple("new")));
    ui.on_row_menu(bind(ui, &state, |s, i| {
        s.interaction_simple(&format!("menu:{}", i.max(0)));
    }));
    ui.on_trade_picked(bind(ui, &state, |s, i| {
        s.interaction_trade_pick(i);
    }));
    ui.on_run_buy_requested(bind0(ui, &state, |s| s.interaction_simple("runbuy")));
    ui.on_run_sell_requested(bind0(ui, &state, |s| s.interaction_simple("runsell")));
    ui.on_run_all_requested(bind0(ui, &state, |s| s.interaction_simple("runall")));
    ui.on_cmp_side_picked(bind(ui, &state, |s, i| {
        s.interaction_cmpside(i);
    }));
    ui.on_sym_open(bind0(ui, &state, |s| s.interaction_symopen()));
    ui.on_sym_close(bind0(ui, &state, |s| s.interaction_symclose()));
    ui.on_sym_clear(bind0(ui, &state, |s| s.interaction_symclear()));
    ui.on_sym_apply(bind0(ui, &state, |s| s.interaction_symapply()));
    ui.on_sym_all_visible(bind0(ui, &state, |s| s.interaction_symall()));
    ui.on_rank_order_toggled(bind0(ui, &state, |s| s.interaction_ranktoggle()));
    ui.on_export_trades_requested(bind0(ui, &state, |s| {
        s.interaction_simple("exporttrades");
    }));
    let strong = state.clone();
    let weak = ui.as_weak();
    ui.on_rank_picked(move |symbol| {
        {
            let mut guard = strong.borrow_mut();
            guard.interaction_simple(&format!("ranksel:{}", symbol.as_str()));
        }
        if let Some(ui) = weak.upgrade() {
            apply_state(&ui, &strong.borrow());
        }
    });
    // String-carrying editors: local mirror + queued backend commit.
    macro_rules! on_text {
        ($on:ident, $act:expr) => {{
            let strong = state.clone();
            let weak = ui.as_weak();
            ui.$on(move |text| {
                {
                    let mut guard = strong.borrow_mut();
                    $act(&mut guard, text.as_str());
                }
                if let Some(ui) = weak.upgrade() {
                    apply_state(&ui, &strong.borrow());
                }
            });
        }};
    }
    on_text!(on_code_changed, LabState::interaction_codeedit);
    on_text!(on_timeframe_picked, LabState::interaction_timeframe);
    on_text!(on_capital_committed, LabState::interaction_capital);
    on_text!(on_rank_search_changed, LabState::interaction_ranksearch);
    on_text!(on_rankby_picked, LabState::interaction_rankby);
    on_text!(on_trade_filter_changed, LabState::interaction_tradefilter);
    on_text!(on_sym_search_changed, LabState::interaction_symsearch);
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_sym_toggle(move |symbol| {
            {
                let mut guard = strong.borrow_mut();
                guard.interaction_symtoggle(symbol.as_str());
            }
            if let Some(ui) = weak.upgrade() {
                apply_state(&ui, &strong.borrow());
            }
        });
    }
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_param_committed(move |key, text| {
            {
                let mut guard = strong.borrow_mut();
                guard.interaction_param(key.as_str(), text.as_str());
            }
            if let Some(ui) = weak.upgrade() {
                apply_state(&ui, &strong.borrow());
            }
        });
    }
    {
        let strong = state.clone();
        let weak = ui.as_weak();
        ui.on_dates_committed(move |start, end| {
            {
                let mut guard = strong.borrow_mut();
                guard.interaction_dates(start.as_str(), end.as_str());
            }
            if let Some(ui) = weak.upgrade() {
                apply_state(&ui, &strong.borrow());
            }
        });
    }
}

fn view_of<'a>(ptr: *mut LabView) -> Result<&'a mut LabView, i32> {
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
pub extern "C" fn vayren_strategy_lab_abi_version() -> c_uint {
    ABI_VERSION
}

#[no_mangle]
pub extern "C" fn vayren_strategy_lab_view_create(
    width_px: c_uint,
    height_px: c_uint,
    scale_factor: c_float,
) -> *mut LabView {
    match catch_unwind(AssertUnwindSafe(|| {
        LabView::create(width_px, height_px, scale_factor)
    })) {
        Ok(Ok(view)) => Box::into_raw(view),
        _ => std::ptr::null_mut(),
    }
}

#[no_mangle]
pub extern "C" fn vayren_strategy_lab_view_destroy(view: *mut LabView) {
    let _ = catch_unwind(AssertUnwindSafe(|| {
        if !view.is_null() {
            // SAFETY: inverse of `Box::into_raw` in `create`.
            unsafe { drop(Box::from_raw(view)) };
        }
    }));
}

#[no_mangle]
pub extern "C" fn vayren_strategy_lab_view_resize(
    view: *mut LabView,
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
/// `strategy_lab_snapshot_dict` on the Python side; parsed defensively by
/// `lab::apply_snapshot_json`). Returns 0 on success, -4 on invalid JSON.
#[no_mangle]
pub extern "C" fn vayren_strategy_lab_view_set_snapshot(
    view: *mut LabView,
    json_utf8: *const c_char,
) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        let text = cstr(json_utf8)?;
        let value: serde_json::Value = serde_json::from_str(text).map_err(|_| -4)?;
        {
            let mut state = view.state.borrow_mut();
            lab::apply_snapshot_json(&mut state, &value);
        }
        apply_state(&view._ui, &view.state.borrow());
        view.window.request_redraw();
        Ok(0)
    })
}

#[no_mangle]
pub extern "C" fn vayren_strategy_lab_view_tick(view: *mut LabView) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        slint::platform::update_timers_and_animations();
        Ok(0)
    })
}

/// Pop the oldest queued user action into `out_utf8` (`out_len` bytes).
/// Returns bytes written (>0), 0 when the queue is empty, -5 when the buffer
/// is too small (the action stays queued; the host must retry with room).
#[no_mangle]
pub extern "C" fn vayren_strategy_lab_view_next_action(
    view: *mut LabView,
    out_utf8: *mut c_uchar,
    out_len: usize,
) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        if out_utf8.is_null() {
            return Err(-2);
        }
        let action = {
            let mut state = view.state.borrow_mut();
            state.pending_actions.first().cloned()
        };
        let Some(action) = action else { return Ok(0) };
        let bytes = action.as_bytes();
        if bytes.len() > out_len {
            return Err(-5);
        }
        // SAFETY: contract guarantees a writable `out_len`-byte buffer and we
        // checked `bytes.len() <= out_len`.
        let out = unsafe { std::slice::from_raw_parts_mut(out_utf8, bytes.len()) };
        out.copy_from_slice(bytes);
        let mut state = view.state.borrow_mut();
        state.pending_actions.remove(0);
        Ok(bytes.len() as i32)
    })
}

#[no_mangle]
pub extern "C" fn vayren_strategy_lab_view_render(
    view: *mut LabView,
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
    view: *mut LabView,
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
pub extern "C" fn vayren_strategy_lab_view_pointer_move(
    view: *mut LabView,
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
    view: *mut LabView,
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
pub extern "C" fn vayren_strategy_lab_view_pointer_press(
    view: *mut LabView,
    x: c_float,
    y: c_float,
    button: c_int,
) -> c_int {
    pointer_button_event(view, true, x, y, button)
}

#[no_mangle]
pub extern "C" fn vayren_strategy_lab_view_pointer_release(
    view: *mut LabView,
    x: c_float,
    y: c_float,
    button: c_int,
) -> c_int {
    pointer_button_event(view, false, x, y, button)
}

#[no_mangle]
pub extern "C" fn vayren_strategy_lab_view_pointer_leave(view: *mut LabView) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        view.window.dispatch_event(WindowEvent::PointerExited);
        Ok(0)
    })
}

#[no_mangle]
pub extern "C" fn vayren_strategy_lab_view_scroll(
    view: *mut LabView,
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

/// Control-key grammar from the Qt host (`KEY+<qt code>`, `CTRL+<qt code>`).
/// Arrows step the real blotter selection, Enter re-focuses it, Ctrl+S saves
/// with the working buffer, Ctrl+Return runs — the Qt `eventFilter`/
/// shortcut semantics, executed on the backend via the action queue.
fn handle_control_key(view: &LabView, payload: &str) -> bool {
    let (action, save_with_buffer) = match payload {
        // Qt.Key_Up / Key_Down / Key_Return+Enter (blotter eventFilter codes).
        "KEY+16777235" => ("tradeup".to_string(), false),
        "KEY+16777237" => ("tradedown".to_string(), false),
        "KEY+16777220" | "KEY+16777221" => ("tradeenter".to_string(), false),
        "CTRL+83" => (String::new(), true),
        "CTRL+16777220" | "CTRL+16777221" => ("run".to_string(), false),
        _ => return false,
    };
    {
        let mut state = view.state.borrow_mut();
        if save_with_buffer {
            state.interaction_save();
        } else if action == "run" {
            state.interaction_run();
        } else {
            state.interaction_simple(&action);
        }
    }
    apply_state(&view._ui, &view.state.borrow());
    view.window.request_redraw();
    true
}

#[no_mangle]
pub extern "C" fn vayren_strategy_lab_view_key(
    view: *mut LabView,
    text_utf8: *const c_char,
    pressed: c_int,
) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        let text: slint::SharedString = cstr(text_utf8)?.into();
        if pressed != 0 {
            let owned = text.as_str().to_string();
            if (owned.starts_with("KEY+") || owned.starts_with("CTRL+"))
                && handle_control_key(view, &owned)
            {
                return Ok(0);
            }
            view.window.dispatch_event(WindowEvent::KeyPressed { text });
        } else {
            view.window
                .dispatch_event(WindowEvent::KeyReleased { text });
        }
        Ok(0)
    })
}

impl LabView {
    fn create(width_px: u32, height_px: u32, scale_factor: f32) -> Result<Box<Self>, i32> {
        if width_px == 0 || height_px == 0 {
            return Err(-5);
        }
        ensure_platform()?;
        let scale = scale_factor.clamp(MIN_SCALE, MAX_SCALE);
        let window = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
        PENDING_WINDOW.with(|slot| slot.borrow_mut().replace(window.clone()));
        let ui = LabHostWindow::new().map_err(|_| -7)?;
        PENDING_WINDOW.with(|slot| slot.borrow_mut().take());
        let state = Rc::new(RefCell::new(LabState::default()));
        wire_view(&ui, state.clone());
        apply_state(&ui, &state.borrow());
        let mut view = Box::new(LabView {
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
            renderer.render(pixels, stride);
        });
        Ok(painted)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn snapshot_feeds_canonical_state_end_to_end() {
        // No window/platform needed: bridge parsing is pure.
        let mut state = LabState::default();
        let value: serde_json::Value = serde_json::from_str(
            r#"{"strategies":[{"name":"OBR","description":"","tags":[],"version":"1.0",
               "modified":"11 Sep 26","last_backtest":"—","favorite":true}],
               "selected_name":"OBR","mode":"buy","run":"ready","engine_wired":false,
               "config":{"universe":"RELIANCE","timeframe":"15m","dates":"02 Jan → 11 Sep",
               "capital":"₹10,00,000","cost":"0.02% / 0.03%"}}"#,
        )
        .unwrap();
        lab::apply_snapshot_json(&mut state, &value);
        assert_eq!(state.strategies.len(), 1);
        assert_eq!(state.selected, Some(0));
        let view = lab::project(&state);
        assert_eq!(view.name, "OBR");
        assert!(!view.run_enabled); // engine not wired yet: honest RUN state
    }
}
