//! Embeddable native Research view — offscreen Slint host for the Qt shell.
//!
//! Architecture (constitution §3: Rust+Slint owns ALL native UI state and
//! presentation) — the proven `vayren-live-view` mechanism reused for
//! RESEARCH. The Python host (app.services.slint_research_host) blits these
//! pixels into the main VAYREN window, forwards input, pushes ResearchService
//! snapshots, and dispatches queued UI intents back to the engine:
//!
//! ```text
//! Qt main window ── SlintResearchHost (viewport) ── C ABI ── THIS crate
//!   state: vayren_shell::research_state::ResearchState (single owner — the
//!   SAME view-model the native shell binds); ui/research_host.slint hosts
//!   the EXISTING verified ResearchScreen with zero visual delta.
//! ```
//!
//! Threading: every C ABI function must run on the thread that created the
//! view (Slint handles are `!Send`); violations return an error code.
//! Error codes: `-1` null handle · `-2` null ptr · `-3` bad UTF-8 ·
//! `-4` bad JSON · `-5` bad arg · `-6` wrong thread · `-7` platform ·
//! `-9` panic (destroy the view after one).

slint::include_modules!();

use std::cell::{Cell, RefCell};
use std::ffi::{c_char, c_float, c_int, c_uchar, c_uint, CStr};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::rc::Rc;

use slint::platform::{
    software_renderer::{MinimalSoftwareWindow, RepaintBufferType},
    Platform, PlatformError, PointerEventButton, WindowAdapter, WindowEvent,
};
use slint::{ComponentHandle, LogicalPosition, ModelRc, PhysicalSize, SharedString, VecModel};
use vayren_shell::research_state::{self, ResearchState};

pub const ABI_VERSION: u32 = 1;
const MIN_SCALE: f32 = 0.25;
const MAX_SCALE: f32 = 8.0;

thread_local! {
    static PENDING_WINDOW: RefCell<Option<Rc<MinimalSoftwareWindow>>> = const { RefCell::new(None) };
}

struct EmbedPlatform;

impl Platform for EmbedPlatform {
    fn create_window_adapter(&self) -> Result<Rc<dyn WindowAdapter>, PlatformError> {
        let window: Rc<MinimalSoftwareWindow> = PENDING_WINDOW
            .with(|slot| slot.borrow_mut().take())
            .expect("embed view created without a pending window");
        Ok(window as Rc<dyn WindowAdapter>)
    }
}

fn ensure_platform() -> Result<(), i32> {
    // Interchangeable stateless platform: an already-set one is fine.
    let _ = slint::platform::set_platform(Box::new(EmbedPlatform));
    Ok(())
}

pub struct ResearchView {
    window: Rc<MinimalSoftwareWindow>,
    _ui: ResearchHostWindow,
    state: Rc<RefCell<ResearchState>>,
    refresh_requested: Rc<Cell<bool>>,
    width_px: u32,
    height_px: u32,
    scale_factor: f32,
    thread: std::thread::ThreadId,
}

fn model<T: Clone + 'static>(rows: Vec<T>) -> ModelRc<T> {
    Rc::new(VecModel::from(rows)).into()
}

fn strings(values: Vec<String>) -> ModelRc<SharedString> {
    model(
        values
            .into_iter()
            .map(SharedString::from)
            .collect::<Vec<_>>(),
    )
}

/// Project the Research view-model onto the host window. Setter mapping
/// mirrors `shell::apply_research` 1:1 (same view-model, other bind target).
#[allow(clippy::too_many_lines)]
fn apply_view(ui: &ResearchHostWindow, state: &ResearchState) {
    let view = research_state::project(state);
    let results = state.results.clone().unwrap_or_default();
    ui.set_strategy_name(view.strategy_name.into());
    ui.set_strategy_version(view.strategy_version.into());
    ui.set_experiment_id(view.experiment_id.into());
    ui.set_context_line(view.context_line.into());
    ui.set_state_label(view.state_label.into());
    ui.set_state_tone(view.state_tone);
    ui.set_has_strategy(view.has_strategy);
    ui.set_show_results(view.show_results);
    ui.set_stale(view.stale);
    ui.set_run_enabled(view.run_enabled);
    ui.set_run_blocked_reason(view.run_blocked_reason.into());
    ui.set_cancel_visible(view.cancel_visible);
    ui.set_create_visible(view.create_visible);
    ui.set_create_error(view.create_error.into());
    ui.set_hypothesis(view.hypothesis.into());
    ui.set_question(view.question.into());
    ui.set_effect(view.effect.into());
    ui.set_signal_filter(view.signal_filter.into());
    ui.set_signal_count_line(view.signal_count_line.into());
    ui.set_trade_count_line(view.trade_count_line.into());
    ui.set_empty_title(view.empty_title.into());
    ui.set_empty_detail(view.empty_detail.into());
    ui.set_conclusion(view.conclusion.into());
    ui.set_validation_status(view.validation_status.into());
    ui.set_validation_summary(view.validation_summary.into());
    ui.set_fingerprint_line(view.fingerprint_line.into());
    ui.set_strategy_count(view.strategy_count.into());
    ui.set_experiment_count(view.experiment_count.into());
    ui.set_tab(view.tab as i32);
    ui.set_inspector_open(view.inspector_open);
    ui.set_narrow_view(view.narrow_view as i32);
    ui.set_log_expanded(view.log_expanded);
    ui.set_log_status(view.log_status.into());
    ui.set_strategies(model(
        view.strategies
            .into_iter()
            .map(|s| ResearchStrategyRow {
                name: s.name.into(),
                description: s.description.into(),
                version: s.version.into(),
                selected: s.selected,
            })
            .collect::<Vec<_>>(),
    ));
    ui.set_experiments(model(
        view.experiments
            .into_iter()
            .map(|e| ResearchExperimentRow {
                id: e.id.into(),
                strategy: e.strategy.into(),
                status: e.status.into(),
                status_tone: e.status_tone,
                selected: e.selected,
            })
            .collect::<Vec<_>>(),
    ));
    ui.set_config_groups(model(
        view.config_groups
            .into_iter()
            .map(|g| ResearchConfigGroup {
                title: g.title.into(),
                hint: g.hint.into(),
                fields: model(
                    g.fields
                        .into_iter()
                        .map(|f| ResearchField {
                            key: f.key.into(),
                            label: f.label.into(),
                            value: f.value.into(),
                            kind: f.kind,
                            options: strings(f.options),
                            selected: f.selected as i32,
                        })
                        .collect::<Vec<_>>(),
                ),
            })
            .collect::<Vec<_>>(),
    ));
    ui.set_evidence_verdict(view.evidence_verdict.into());
    ui.set_evidence_tone(view.evidence_tone);
    ui.set_evidence_grade(view.evidence_grade.into());
    ui.set_evidence_why(model(
        view.evidence_why
            .into_iter()
            .map(|w| ResearchEvidenceWhy {
                kind: w.kind.into(),
                text: w.text.into(),
            })
            .collect::<Vec<_>>(),
    ));
    ui.set_evidence_dims(model(
        view.evidence_dims
            .into_iter()
            .map(|d| ResearchEvidenceDim {
                name: d.name.into(),
                status: d.status.into(),
                tone: d.tone,
                detail: d.detail.into(),
            })
            .collect::<Vec<_>>(),
    ));
    let kv_of = |rows: Vec<research_state::KvView>| {
        model(
            rows.into_iter()
                .map(|r| ResearchKv {
                    label: r.label.into(),
                    value: r.value.into(),
                })
                .collect::<Vec<_>>(),
        )
    };
    ui.set_stats_rows(kv_of(view.stats_rows));
    ui.set_montecarlo_rows(kv_of(view.montecarlo_rows));
    ui.set_benchmark_rows(kv_of(view.benchmark_rows));
    ui.set_inspector_groups(model(
        view.inspector_groups
            .into_iter()
            .map(|g| ResearchKvGroup {
                title: g.title.into(),
                rows: model(
                    g.rows
                        .into_iter()
                        .map(|r| ResearchKv {
                            label: r.label.into(),
                            value: r.value.into(),
                        })
                        .collect::<Vec<_>>(),
                ),
            })
            .collect::<Vec<_>>(),
    ));
    ui.set_hypothesis_open(view.hypothesis_open);
    ui.set_why_open(view.why_open);
    ui.set_technical_open(view.technical_open);
    ui.set_show_next_steps(view.show_next_steps);
    ui.set_metrics(model(
        view.metrics
            .into_iter()
            .map(|m| ResearchMetric {
                label: m.label.into(),
                value: m.value.into(),
                tone: m.tone,
                emphasized: m.emphasized,
            })
            .collect::<Vec<_>>(),
    ));
    let shown_signals: Vec<research_state::SignalRow> = if view.show_results {
        state.filtered_signals()
    } else {
        Vec::new()
    };
    ui.set_signals(model(
        shown_signals
            .into_iter()
            .map(|s| ResearchSignalRow {
                time: s.time.into(),
                symbol: s.symbol.into(),
                tf: s.tf.into(),
                side: s.side.into(),
                price: s.price.into(),
                event: s.event.into(),
                strategy: s.strategy.into(),
                exp: s.exp.into(),
            })
            .collect::<Vec<_>>(),
    ));
    ui.set_trades(model(
        results
            .trades
            .into_iter()
            .map(|t| ResearchTradeRow {
                no: t.no.into(),
                entry: t.entry.into(),
                exit: t.exit.into(),
                side: t.side.into(),
                qty: t.qty.into(),
                pnl: t.pnl.into(),
                reason: t.reason.into(),
                hold: t.hold.into(),
                pnl_tone: t.pnl_tone.kind(),
            })
            .collect::<Vec<_>>(),
    ));
    ui.set_robustness(model(
        results
            .robustness
            .into_iter()
            .map(|r| ResearchRobustRow {
                test: r.test.into(),
                input: r.input.into(),
                stability: r.stability.into(),
                stability_tone: r.stability_tone.badge(),
                evidence: r.evidence.into(),
            })
            .collect::<Vec<_>>(),
    ));
    let kv = |rows: Vec<research_state::KvRow>| {
        model(
            rows.into_iter()
                .map(|r| ResearchKv {
                    label: r.label.into(),
                    value: r.value.into(),
                })
                .collect::<Vec<_>>(),
        )
    };
    ui.set_validation_rows(kv(results.validation_rows));
    ui.set_quality_rows(kv(results.quality_rows));
    // Empty results keep the inspector useful with live-form facts only.
    let inspector_rows = if state.results.is_some() {
        results.inspector_rows
    } else {
        state.default_inspector_rows()
    };
    ui.set_inspector_rows(kv(inspector_rows));
    ui.set_report_sections(kv(results.report_sections));
    ui.set_compare_rows(model(
        results
            .compare_rows
            .into_iter()
            .map(|c| ResearchCompareRow {
                exp: c.exp.into(),
                strategy: c.strategy.into(),
                status: c.status.into(),
                detail: c.detail.into(),
            })
            .collect::<Vec<_>>(),
    ));
    ui.set_compare_verdict(results.compare_verdict.into());
    ui.set_log_lines(strings(view.log_lines));
}

fn wire_view(ui: &ResearchHostWindow, state: Rc<RefCell<ResearchState>>, refresh: Rc<Cell<bool>>) {
    macro_rules! wire_local_int {
        ($register:ident, $action:expr) => {{
            let strong = state.clone();
            let handle = ui.as_weak();
            ui.$register(move |arg: i32| {
                $action(&mut strong.borrow_mut(), arg);
                if let Some(ui) = handle.upgrade() {
                    apply_view(&ui, &strong.borrow());
                }
            });
        }};
    }
    macro_rules! wire_local_text {
        ($register:ident, $action:expr) => {{
            let strong = state.clone();
            let handle = ui.as_weak();
            ui.$register(move |text: SharedString| {
                $action(&mut strong.borrow_mut(), text.as_str());
                if let Some(ui) = handle.upgrade() {
                    apply_view(&ui, &strong.borrow());
                }
            });
        }};
    }
    wire_local_int!(on_tab_picked, |s: &mut ResearchState, i: i32| {
        s.set_tab(i.max(0) as usize);
    });
    wire_local_int!(on_narrow_view_picked, |s: &mut ResearchState, i: i32| {
        s.set_narrow_view(i.max(0) as usize);
    });
    wire_local_text!(on_signal_filter_changed, ResearchState::set_signal_filter);
    wire_local_text!(on_hypothesis_edited, ResearchState::set_hypothesis);
    wire_local_text!(on_question_edited, ResearchState::set_question);
    wire_local_text!(on_effect_edited, ResearchState::set_effect);
    {
        let strong = state.clone();
        let handle = ui.as_weak();
        ui.on_config_edited(move |key: SharedString, text: SharedString| {
            let _ = strong.borrow_mut().edit_field(key.as_str(), text.as_str());
            if let Some(ui) = handle.upgrade() {
                apply_view(&ui, &strong.borrow());
            }
        });
    }
    {
        let strong = state.clone();
        let handle = ui.as_weak();
        ui.on_config_option(move |key: SharedString, value: SharedString| {
            let mut state = strong.borrow_mut();
            match key.as_str() {
                "timeframe" => {
                    if let Some(i) = research_state::TIMEFRAME_OPTIONS
                        .iter()
                        .position(|o| *o == value.as_str())
                    {
                        let _ = state.set_timeframe(i);
                    }
                }
                "side" => {
                    if let Some(i) = research_state::SIDE_OPTIONS
                        .iter()
                        .position(|o| *o == value.as_str())
                    {
                        let _ = state.set_side(i);
                    }
                }
                "direction" => {
                    if let Some(i) = research_state::DIRECTION_OPTIONS
                        .iter()
                        .position(|o| *o == value.as_str())
                    {
                        state.set_direction(i);
                    }
                }
                _ => {}
            }
            drop(state);
            if let Some(ui) = handle.upgrade() {
                apply_view(&ui, &strong.borrow());
            }
        });
    }
    // Strategy + experiment selection: local view state, plus a select
    // action so the host can fetch the chosen experiment's bundle.
    {
        let strong = state.clone();
        let handle = ui.as_weak();
        ui.on_strategy_picked(move |i: i32| {
            strong.borrow_mut().select_strategy(i.max(0) as usize);
            if let Some(ui) = handle.upgrade() {
                apply_view(&ui, &strong.borrow());
            }
        });
    }
    {
        let strong = state.clone();
        let handle = ui.as_weak();
        let refresh_flag = refresh.clone();
        ui.on_experiment_picked(move |i: i32| {
            let mut state = strong.borrow_mut();
            if state.pick_experiment(i.max(0) as usize) {
                let id = state
                    .selected_experiment
                    .and_then(|k| state.experiments.get(k))
                    .map(|e| e.id.clone());
                if let Some(id) = id {
                    state.push_host_action(serde_json::json!({"action": "select", "id": id}));
                    refresh_flag.set(true);
                }
            }
            drop(state);
            if let Some(ui) = handle.upgrade() {
                apply_view(&ui, &strong.borrow());
            }
        });
    }
    // Create / Run / Cancel: validated locally for instant feedback; the
    // authoritative engine call is the queued action.
    {
        let strong = state.clone();
        let handle = ui.as_weak();
        let refresh_flag = refresh.clone();
        ui.on_create_pressed(move || {
            let mut state = strong.borrow_mut();
            let payload = state.host_config_payload();
            if state.create() {
                state.push_host_action(serde_json::json!({"action": "create", "config": payload}));
                refresh_flag.set(true);
            }
            drop(state);
            if let Some(ui) = handle.upgrade() {
                apply_view(&ui, &strong.borrow());
            }
        });
    }
    {
        let strong = state.clone();
        let handle = ui.as_weak();
        let refresh_flag = refresh.clone();
        ui.on_run_pressed(move || {
            let mut state = strong.borrow_mut();
            let payload = state.host_config_payload();
            if state.start_run() {
                // Optimistic: the host snapshot re-states truth every poll.
                state.push_host_action(serde_json::json!({"action": "run", "config": payload}));
                refresh_flag.set(true);
            }
            drop(state);
            if let Some(ui) = handle.upgrade() {
                apply_view(&ui, &strong.borrow());
            }
        });
    }
    {
        let strong = state.clone();
        let handle = ui.as_weak();
        let refresh_flag = refresh.clone();
        ui.on_cancel_pressed(move || {
            let mut state = strong.borrow_mut();
            if state.cancel_run() {
                let id = state
                    .results
                    .as_ref()
                    .map(|r| r.experiment_id.clone())
                    .unwrap_or_default();
                state.push_host_action(serde_json::json!({"action": "cancel", "id": id}));
                refresh_flag.set(true);
            }
            drop(state);
            if let Some(ui) = handle.upgrade() {
                apply_view(&ui, &strong.borrow());
            }
        });
    }
    let strong = state.clone();
    let handle = ui.as_weak();
    ui.on_inspector_toggled(move || {
        strong.borrow_mut().toggle_inspector();
        if let Some(ui) = handle.upgrade() {
            apply_view(&ui, &strong.borrow());
        }
    });
    macro_rules! wire_local_toggle {
        ($register:ident, $action:expr) => {{
            let strong = state.clone();
            let handle = ui.as_weak();
            ui.$register(move || {
                $action(&mut strong.borrow_mut());
                if let Some(ui) = handle.upgrade() {
                    apply_view(&ui, &strong.borrow());
                }
            });
        }};
    }
    wire_local_toggle!(on_hypothesis_toggled, ResearchState::toggle_hypothesis);
    wire_local_toggle!(on_why_toggled, ResearchState::toggle_why);
    wire_local_toggle!(on_technical_toggled, ResearchState::toggle_technical);
}

impl ResearchView {
    fn create(width_px: u32, height_px: u32, scale_factor: f32) -> Result<Box<Self>, i32> {
        if width_px == 0 || height_px == 0 {
            return Err(-5);
        }
        ensure_platform()?;
        let scale = scale_factor.clamp(MIN_SCALE, MAX_SCALE);
        let window = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
        PENDING_WINDOW.with(|slot| slot.borrow_mut().replace(window.clone()));
        let ui = ResearchHostWindow::new().map_err(|_| -7)?;
        PENDING_WINDOW.with(|slot| slot.borrow_mut().take());
        let state = Rc::new(RefCell::new(ResearchState::default()));
        let refresh = Rc::new(Cell::new(false));
        wire_view(&ui, state.clone(), refresh.clone());
        apply_view(&ui, &state.borrow());
        let mut view = Box::new(ResearchView {
            window,
            _ui: ui,
            state,
            refresh_requested: refresh,
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
        self._ui
            .set_view_width(self.width_px as f32 / self.scale_factor);
        self._ui
            .set_view_height(self.height_px as f32 / self.scale_factor);
        self.window.request_redraw();
    }

    fn render_into(&self, out: &mut [u8]) -> Result<bool, i32> {
        use slint::Rgb8Pixel;
        let need = self.width_px as usize * self.height_px as usize * 3;
        if out.len() < need {
            return Err(-5);
        }
        // SAFETY: Rgb8Pixel is three contiguous bytes; caller guarantees a
        // live exclusive `out` of at least `need`.
        let pixels =
            unsafe { std::slice::from_raw_parts_mut(out.as_mut_ptr() as *mut Rgb8Pixel, need / 3) };
        let stride = self.width_px as usize;
        let painted = self.window.draw_if_needed(|renderer| {
            renderer.render(pixels, stride);
        });
        Ok(painted)
    }
}

fn view_of<'a>(ptr: *mut ResearchView) -> Result<&'a mut ResearchView, i32> {
    if ptr.is_null() {
        return Err(-1);
    }
    // SAFETY: non-null handles come only from `create` and live until
    // `destroy`; entry points check thread affinity.
    Ok(unsafe { &mut *ptr })
}

fn cstr(ptr: *const c_char) -> Result<&'static str, i32> {
    if ptr.is_null() {
        return Err(-2);
    }
    // SAFETY: contract requires valid NUL-terminated UTF-8.
    unsafe { CStr::from_ptr(ptr) }.to_str().map_err(|_| -3)
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
pub extern "C" fn vayren_research_abi_version() -> c_uint {
    ABI_VERSION
}

#[no_mangle]
pub extern "C" fn vayren_research_view_create(
    width_px: c_uint,
    height_px: c_uint,
    scale_factor: c_float,
) -> *mut ResearchView {
    match catch_unwind(AssertUnwindSafe(|| {
        ResearchView::create(width_px, height_px, scale_factor)
    })) {
        Ok(Ok(view)) => Box::into_raw(view),
        _ => std::ptr::null_mut(),
    }
}

#[no_mangle]
pub extern "C" fn vayren_research_view_destroy(view: *mut ResearchView) {
    let _ = catch_unwind(AssertUnwindSafe(|| {
        if !view.is_null() {
            // SAFETY: inverse of `Box::into_raw`.
            unsafe { drop(Box::from_raw(view)) };
        }
    }));
}

#[no_mangle]
pub extern "C" fn vayren_research_view_resize(
    view: *mut ResearchView,
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

/// Replace backend facts from the bridge snapshot (see
/// `research_state::ResearchState::apply_host_snapshot` for the schema).
#[no_mangle]
pub extern "C" fn vayren_research_view_set_snapshot(
    view: *mut ResearchView,
    json_utf8: *const c_char,
) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        let text = cstr(json_utf8)?;
        let value: serde_json::Value = serde_json::from_str(text).map_err(|_| -4)?;
        {
            let mut state = view.state.borrow_mut();
            state.apply_host_snapshot(&value);
        }
        apply_view(&view._ui, &view.state.borrow());
        view.window.request_redraw();
        Ok(0)
    })
}

#[no_mangle]
pub extern "C" fn vayren_research_view_next_event(
    view: *mut ResearchView,
    out_buf: *mut c_uchar,
    out_len: usize,
) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        if out_buf.is_null() || out_len == 0 {
            return Err(-2);
        }
        let Some(text) = view.state.borrow_mut().take_action() else {
            return Ok(0);
        };
        if text.len() + 1 > out_len {
            view.state.borrow_mut().push_front_action(text);
            return Err(-5);
        }
        // SAFETY: contract guarantees a writable `out_len`-byte buffer.
        let out = unsafe { std::slice::from_raw_parts_mut(out_buf, out_len) };
        out[..text.len()].copy_from_slice(text.as_bytes());
        out[text.len()] = 0;
        Ok(text.len() as i32)
    })
}

#[no_mangle]
pub extern "C" fn vayren_research_view_tick(view: *mut ResearchView) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        slint::platform::update_timers_and_animations();
        Ok(0)
    })
}

#[no_mangle]
pub extern "C" fn vayren_research_view_render(
    view: *mut ResearchView,
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
        Ok(i32::from(view.render_into(out)?))
    })
}

fn pointer_event(
    view: *mut ResearchView,
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
pub extern "C" fn vayren_research_view_pointer_move(
    view: *mut ResearchView,
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

fn pointer_button(
    view: *mut ResearchView,
    pressed: bool,
    x: c_float,
    y: c_float,
    button: c_int,
) -> c_int {
    let resolved = match button {
        0 => PointerEventButton::Left,
        1 => PointerEventButton::Right,
        2 => PointerEventButton::Middle,
        _ => return -5,
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
pub extern "C" fn vayren_research_view_pointer_press(
    view: *mut ResearchView,
    x: c_float,
    y: c_float,
    button: c_int,
) -> c_int {
    pointer_button(view, true, x, y, button)
}

#[no_mangle]
pub extern "C" fn vayren_research_view_pointer_release(
    view: *mut ResearchView,
    x: c_float,
    y: c_float,
    button: c_int,
) -> c_int {
    pointer_button(view, false, x, y, button)
}

#[no_mangle]
pub extern "C" fn vayren_research_view_pointer_leave(view: *mut ResearchView) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        view.window.dispatch_event(WindowEvent::PointerExited);
        Ok(0)
    })
}

#[no_mangle]
pub extern "C" fn vayren_research_view_scroll(
    view: *mut ResearchView,
    x: c_float,
    y: c_float,
    delta_x: c_float,
    delta_y: c_float,
) -> c_int {
    pointer_event(
        view,
        |position| WindowEvent::PointerScrolled {
            position,
            delta_x,
            delta_y,
        },
        x,
        y,
    )
}

#[no_mangle]
pub extern "C" fn vayren_research_view_key(
    view: *mut ResearchView,
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

#[no_mangle]
pub extern "C" fn vayren_research_view_refresh_requested(view: *mut ResearchView) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        Ok(i32::from(view.refresh_requested.get()))
    })
}

#[no_mangle]
pub extern "C" fn vayren_research_view_ack_refresh(view: *mut ResearchView) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        view.refresh_requested.set(false);
        Ok(0)
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_view() -> *mut ResearchView {
        let view = vayren_research_view_create(900, 640, 1.0);
        assert!(!view.is_null());
        view
    }

    fn snapshot(view: *mut ResearchView, text: &str) {
        let c = std::ffi::CString::new(text).unwrap();
        assert_eq!(vayren_research_view_set_snapshot(view, c.as_ptr()), 0);
    }

    #[test]
    fn snapshot_renders_real_pixels_and_clean_frames_stay_clean() {
        let view = make_view();
        snapshot(
            view,
            r#"{
                "strategies": [{"name":"OBR","description":"Opening Breakout Strategy","version":"d258c55b"}],
                "experiments": [{"id":"EXP-1","strategy":"OBR","status":"DRAFT"}],
                "selected_id": "EXP-1",
                "status": "DRAFT",
                "log": ["Datasets refreshed"],
                "defaults": {"universe":"NIFTY 500","symbols":"RELIANCE, TCS","timeframe":"5m",
                             "start":"2024-01-01","end":"2024-03-31","side":"BOTH",
                             "capital":"1000000","slippage_pct":"0.02","commission_pct":"0.03",
                             "parameters":"","hypothesis":"edge persists",
                             "research_question":"why?","expected_effect":"+"},
                "bundle": null
            }"#,
        );
        let mut buffer = vec![0x7Fu8; 900 * 640 * 3];
        assert_eq!(vayren_research_view_tick(view), 0);
        assert_eq!(
            vayren_research_view_render(view, buffer.as_mut_ptr(), buffer.len()),
            1
        );
        let first = buffer[0];
        assert!(
            buffer.iter().any(|b| *b != first),
            "rendered frame must contain varied pixels"
        );
        buffer.fill(0x7F);
        assert_eq!(
            vayren_research_view_render(view, buffer.as_mut_ptr(), buffer.len()),
            0
        );
        vayren_research_view_destroy(view);
    }

    #[test]
    fn intents_queue_actions_for_the_host() {
        let view = make_view();
        snapshot(
            view,
            r#"{"strategies":[{"name":"OBR","description":"","version":"1"}],
                "defaults":{"symbols":"RELIANCE","hypothesis":"momentum persists"},
                "status":"NO_EXPERIMENT","bundle":null}"#,
        );
        // SAFETY: handle from create, same thread.
        let v = unsafe { &mut *view };
        v._ui.invoke_create_pressed();
        let mut buf = [0u8; 4096];
        let len = vayren_research_view_next_event(view, buf.as_mut_ptr(), buf.len());
        assert!(len > 0, "create accepted -> queued action");
        let parsed: serde_json::Value = serde_json::from_slice(&buf[..len as usize]).unwrap();
        assert_eq!(parsed["action"], "create");
        assert_eq!(parsed["config"]["symbols"], "RELIANCE");
        assert_eq!(parsed["config"]["hypothesis"], "momentum persists");
        assert_eq!(
            vayren_research_view_next_event(view, buf.as_mut_ptr(), buf.len()),
            0
        );
        assert_eq!(vayren_research_view_refresh_requested(view), 1);
        assert_eq!(vayren_research_view_ack_refresh(view), 0);
        // Rejected intent (no hypothesis yet) queues nothing.
        v.state.borrow_mut().hypothesis.clear();
        v._ui.invoke_create_pressed();
        assert_eq!(
            vayren_research_view_next_event(view, buf.as_mut_ptr(), buf.len()),
            0
        );
        vayren_research_view_destroy(view);
    }

    #[test]
    fn rejects_bad_input_fail_closed() {
        assert_eq!(
            vayren_research_view_render(std::ptr::null_mut(), std::ptr::null_mut(), 0),
            -1
        );
        let view = make_view();
        let bad = std::ffi::CString::new("{nope").unwrap();
        assert_eq!(vayren_research_view_set_snapshot(view, bad.as_ptr()), -4);
        assert_eq!(
            vayren_research_view_set_snapshot(view, std::ptr::null()),
            -2
        );
        assert_eq!(vayren_research_view_resize(view, 0, 600, 1.0), -5);
        assert_eq!(vayren_research_view_pointer_press(view, 1.0, 1.0, 9), -5);
        let mut tiny = vec![0u8; 8];
        assert_eq!(
            vayren_research_view_render(view, tiny.as_mut_ptr(), tiny.len()),
            -5
        );
        vayren_research_view_destroy(view);
        vayren_research_view_destroy(std::ptr::null_mut());
    }
}
