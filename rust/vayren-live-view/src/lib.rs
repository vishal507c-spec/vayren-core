//! Embeddable native Live view — offscreen Slint host for the legacy shell.
//!
//! Architecture (AI_ENTRY.md §1: Rust+Slint owns ALL native UI state and
//! presentation) — the proven `vayren-portfolio-view` mechanism reused for
//! LIVE, with one addition this surface needs: an ACTION OUT channel.
//!
//! ```text
//! legacy main window (one process, GUI thread)
//!   │  SlintLiveHost (dumb viewport: blits pixels, forwards events,
//!   │    pushes _live_state_provider snapshots, drains action events)
//!   │  C ABI below (plain integers, UTF-8 JSON, RGB bytes — no objects)
//!   ▼
//! THIS crate (Rust owns view-model + interaction state + pixels)
//!   │  live::LiveState / live::project (reused from vayren-shell, single
//!   │    owner — the SAME view-model the native shell binds)
//!   │  LiveHostWindow (hosts the EXISTING verified LiveScreen;
//!   │    ui/live_host.slint is pure pass-through, zero visual delta)
//!   │  MinimalSoftwareWindow (documented Slint offscreen path, no event loop)
//! ```
//!
//! Interaction contract (host mode): UI intents validate fail-closed in the
//! view-model, accepted ones queue as JSON actions
//! (`vayren_live_view_next_event`) that the Python host dispatches to the
//! real `LiveTradingService`; runtime fields then change only when the next
//! backend snapshot confirms them — the UI never pre-applies RUNNING/HALTED.
//!
//! Threading: every C ABI function must be called on the SAME thread that
//! created the view (Slint handles are `!Send`). The legacy host calls from its
//! GUI thread only; violations return an error code, never UB. No threads
//! are spawned here; no legacy headers are needed to build this crate.
//!
//! Error codes (negative = failure, each function documents its own set):
//! `-1` null view handle · `-2` null pointer · `-3` invalid UTF-8 ·
//! `-4` invalid JSON · `-5` bad argument (size/buffer/button) ·
//! `-6` wrong thread · `-7` platform setup failed · `-9` internal panic.

slint::include_modules!();

use std::cell::{Cell, RefCell};
use std::ffi::{c_char, c_float, c_int, c_uchar, c_uint, CStr};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::rc::Rc;

use slint::platform::{
    software_renderer::{MinimalSoftwareWindow, RepaintBufferType},
    Platform, PlatformError, PointerEventButton, WindowAdapter, WindowEvent,
};
use slint::{
    ComponentHandle, LogicalPosition, ModelRc, PhysicalSize, Rgb8Pixel, SharedString, VecModel,
};
use vayren_shell::live::{self, ExecMode, LiveState};

pub const ABI_VERSION: u32 = 1;
const MIN_SCALE: f32 = 0.25;
const MAX_SCALE: f32 = 8.0;

// Window handed to each view at creation. `LiveHostWindow::new()` pulls it
// through `EmbedPlatform::create_window_adapter` synchronously on the
// calling thread, so a thread-local pending slot is sufficient — and it
// statically enforces the single-GUI-thread contract.
thread_local! {
    static PENDING_WINDOW: RefCell<Option<Rc<MinimalSoftwareWindow>>> = const { RefCell::new(None) };
}

struct EmbedPlatform;

impl Platform for EmbedPlatform {
    fn create_window_adapter(&self) -> Result<Rc<dyn WindowAdapter>, PlatformError> {
        // NOTE: `PlatformError` has no generic variant in 1.17, so a missing
        // slot (programmer error: a component created outside `create`)
        // panics loudly here instead of returning a misleading error. The C
        // boundary converts it to `-9` via `catch_unwind`.
        let window: Rc<MinimalSoftwareWindow> = PENDING_WINDOW
            .with(|slot| slot.borrow_mut().take())
            .expect("embed view created without a pending window");
        Ok(window as Rc<dyn WindowAdapter>)
    }
}

fn ensure_platform() -> Result<(), i32> {
    match slint::platform::set_platform(Box::new(EmbedPlatform)) {
        Ok(()) => Ok(()),
        // This process already runs Slint (e.g. two hosts sharing one
        // process): our stateless platform is interchangeable, proceed.
        Err(_) => Ok(()),
    }
}

/// One embedded Live view: Slint component + view-model + pixels.
pub struct LiveView {
    window: Rc<MinimalSoftwareWindow>,
    _ui: LiveHostWindow,
    state: Rc<RefCell<LiveState>>,
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

/// Project the LIVE view-model onto the host window. Setter mapping mirrors
/// `shell::apply_live` 1:1 (same view-model, different bind target); if
/// that function changes shape, THIS function must change with it.
fn apply_view(ui: &LiveHostWindow, state: &LiveState) {
    let view = live::project(state);
    ui.set_bar(LiveBar {
        mode: view.bar.mode,
        broker_label: view.bar.broker_label.into(),
        broker_tone: view.bar.broker_tone,
        conn_label: view.bar.conn_label.into(),
        conn_tone: view.bar.conn_tone,
        strategy_label: view.bar.strategy_label.into(),
        strategy_tone: view.bar.strategy_tone,
        risk_label: view.bar.risk_label.into(),
        risk_tone: view.bar.risk_tone,
        recon_label: view.bar.recon_label.into(),
        recon_tone: view.bar.recon_tone,
        exec_label: view.bar.exec_label.into(),
        exec_tone: view.bar.exec_tone,
        halted: view.bar.halted,
        halt_enabled: view.bar.halt_enabled,
        inspector_open: view.bar.inspector_open,
        note: view.bar.note.into(),
        note_tone: view.bar.note_tone,
    });
    ui.set_inspector_open(view.bar.inspector_open);
    ui.set_setup(LiveSetup {
        quantity: view.setup.quantity.into(),
        session_label: view.setup.session_label.into(),
        session_tone: view.setup.session_tone,
        can_start: view.setup.can_start,
        can_stop: view.setup.can_stop,
        can_arm: view.setup.can_arm,
        needs_live_confirm: view.setup.needs_live_confirm,
        confirmed_live: view.setup.confirmed_live,
        symbol_total: view.setup.symbol_total.into(),
    });
    ui.set_strategy_names(strings(view.setup.strategy_names));
    ui.set_strategy_selected(view.setup.strategy_selected);
    ui.set_timeframe_names(strings(view.setup.timeframe_names));
    ui.set_timeframe_selected(view.setup.timeframe_selected);
    ui.set_market(LiveMarket {
        has_data: view.market.has_data,
        state_label: view.market.state_label.into(),
        tone: view.market.tone,
        title: view.market.title.into(),
        detail: view.market.detail.into(),
        header: view.market.header.into(),
    });
    ui.set_candles(model(
        view.candles
            .into_iter()
            .map(|c| LiveCandle {
                x: c.x,
                open: c.open,
                high: c.high,
                low: c.low,
                close: c.close,
                up: c.up,
            })
            .collect::<Vec<_>>(),
    ));
    ui.set_symbols(model(
        view.symbols
            .into_iter()
            .map(|s| LiveSymbolRow {
                real_index: s.real_index,
                name: s.name.into(),
                checked: s.checked,
            })
            .collect::<Vec<_>>(),
    ));
    ui.set_symbol_filter(view.symbol_filter.into());
    ui.set_gates(model(
        view.gates
            .into_iter()
            .map(|g| LiveGate {
                name: g.name.into(),
                status: g.status.into(),
                tone: g.tone,
                reason: g.reason.into(),
            })
            .collect::<Vec<_>>(),
    ));
    ui.set_arm_note(view.arm_note.into());
    ui.set_blockers(strings(view.setup.blockers));
    let kv = |rows: Vec<live::KvRow>| {
        model(
            rows.into_iter()
                .map(|r| LiveKv {
                    key: r.key.into(),
                    value: r.value.into(),
                    tone: r.tone,
                })
                .collect::<Vec<_>>(),
        )
    };
    ui.set_strategy_rows(kv(view.strategy_rows));
    ui.set_position_rows(kv(view.position_rows));
    ui.set_risk_rows(kv(view.risk_rows));
    ui.set_broker_rows(kv(view.broker_rows));
    ui.set_recon_rows(kv(view.recon_rows));
    ui.set_positions(model(
        view.positions
            .into_iter()
            .map(|p| LivePosition {
                symbol: p.symbol.into(),
                side: p.side.into(),
                qty: p.qty.into(),
                entry: p.entry.into(),
                current: p.current.into(),
                pnl: p.pnl.into(),
                pnl_tone: p.pnl_tone,
                status: p.status.into(),
            })
            .collect::<Vec<_>>(),
    ));
    ui.set_orders(model(
        view.orders
            .into_iter()
            .map(|o| LiveOrder {
                order_id: o.order_id.into(),
                strategy: o.strategy.into(),
                symbol: o.symbol.into(),
                side: o.side.into(),
                qty: o.qty.into(),
                order_type: o.order_type.into(),
                price: o.price.into(),
                status: o.status.into(),
                time: o.time.into(),
                broker: o.broker.into(),
                status_tone: o.status_tone,
            })
            .collect::<Vec<_>>(),
    ));
    ui.set_fills(model(
        view.fills
            .into_iter()
            .map(|f| LiveFill {
                time: f.time.into(),
                symbol: f.symbol.into(),
                side: f.side.into(),
                qty: f.qty.into(),
                price: f.price.into(),
                order_id: f.order_id.into(),
                strategy: f.strategy.into(),
                side_tone: f.side_tone,
            })
            .collect::<Vec<_>>(),
    ));
    ui.set_stats(model(
        view.stats
            .into_iter()
            .map(|s| LiveStat {
                label: s.label.into(),
                value: s.value.into(),
                tone: s.tone,
            })
            .collect::<Vec<_>>(),
    ));
    ui.set_events(model(
        view.events
            .into_iter()
            .map(|e| LiveEventRow {
                timestamp: e.timestamp.into(),
                strategy: e.strategy.into(),
                symbol: e.symbol.into(),
                event: e.event.into(),
                status: e.status.into(),
                status_tone: e.status_tone,
            })
            .collect::<Vec<_>>(),
    ));
    ui.set_event_types(strings(view.event_types));
    ui.set_event_type_index(view.event_type_index);
    ui.set_event_filter(view.event_filter.into());
}

fn wire_view(ui: &LiveHostWindow, state: Rc<RefCell<LiveState>>, refresh: Rc<Cell<bool>>) {
    // Mirrors `shell::wire_live`: Slint reports, Rust mutates centrally and
    // re-projects. Accepted runtime intents additionally queue host
    // actions (`state.take_action`) and flag the host to refresh.
    macro_rules! wire_int {
        ($register:ident, $action:expr) => {{
            let strong = state.clone();
            let handle = ui.as_weak();
            let refresh_flag = refresh.clone();
            ui.$register(move |arg: i32| {
                $action(&mut strong.borrow_mut(), arg);
                refresh_flag.set(true);
                if let Some(ui) = handle.upgrade() {
                    apply_view(&ui, &strong.borrow());
                }
            });
        }};
    }
    macro_rules! wire_text {
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
    macro_rules! wire_unit {
        ($register:ident, $action:expr) => {{
            let strong = state.clone();
            let handle = ui.as_weak();
            let refresh_flag = refresh.clone();
            ui.$register(move || {
                $action(&mut strong.borrow_mut());
                // Runtime intents (start/stop/halt/arm/mode) request a
                // prompt snapshot re-pull; consent/inspector re-reads are
                // cheap regardless (harmless).
                refresh_flag.set(true);
                if let Some(ui) = handle.upgrade() {
                    apply_view(&ui, &strong.borrow());
                }
            });
        }};
    }
    wire_int!(on_mode_picked, |s: &mut LiveState, i: i32| {
        s.set_mode(ExecMode::from_kind(i));
    });
    wire_int!(on_symbol_toggled, |s: &mut LiveState, i: i32| {
        s.toggle_symbol(i.max(0) as usize);
    });
    wire_text!(on_strategy_picked, LiveState::select_strategy_value);
    wire_text!(on_timeframe_picked, LiveState::select_timeframe_value);
    wire_text!(on_event_type_picked, LiveState::apply_event_type);
    wire_text!(on_symbol_filter_changed, LiveState::set_symbol_filter);
    wire_text!(on_event_filter_changed, LiveState::set_event_filter);
    wire_text!(on_quantity_edited, LiveState::set_quantity);
    wire_unit!(on_start_requested, LiveState::start);
    wire_unit!(on_stop_requested, LiveState::stop);
    wire_unit!(on_arm_requested, LiveState::arm);
    wire_unit!(on_halt_requested, LiveState::halt);
    wire_unit!(on_confirm_requested, LiveState::confirm_live);
    wire_unit!(on_inspector_toggled, LiveState::toggle_inspector);
    // CONFIGURE BROKER is navigation intent — the legacy host routes it to the
    // BROKERS workspace; no broker logic lives in this surface.
    {
        let strong = state.clone();
        ui.on_configure_broker(move || {
            strong
                .borrow_mut()
                .push_host_action(serde_json::json!({"action": "configure_broker"}));
        });
    }
}

impl LiveView {
    fn create(width_px: u32, height_px: u32, scale_factor: f32) -> Result<Box<Self>, i32> {
        if width_px == 0 || height_px == 0 {
            return Err(-5);
        }
        ensure_platform()?;
        let scale = scale_factor.clamp(MIN_SCALE, MAX_SCALE);
        let window = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
        PENDING_WINDOW.with(|slot| slot.borrow_mut().replace(window.clone()));
        let ui = LiveHostWindow::new().map_err(|_| -7)?;
        // Drain our own pending slot if codegen took another path (never
        // silently leak a window into a later view).
        PENDING_WINDOW.with(|slot| slot.borrow_mut().take());
        let state = Rc::new(RefCell::new(LiveState::default()));
        let refresh = Rc::new(Cell::new(false));
        wire_view(&ui, state.clone(), refresh.clone());
        apply_view(&ui, &state.borrow());
        let mut view = Box::new(LiveView {
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
        // View size in Slint logical units — the ONLY size the tier layout
        // may derive from (never the Window's own metrics).
        self._ui
            .set_view_width(self.width_px as f32 / self.scale_factor);
        self._ui
            .set_view_height(self.height_px as f32 / self.scale_factor);
        self.window.request_redraw();
    }

    fn button(value: i32) -> Result<PointerEventButton, i32> {
        match value {
            0 => Ok(PointerEventButton::Left),
            1 => Ok(PointerEventButton::Right),
            2 => Ok(PointerEventButton::Middle),
            _ => Err(-5),
        }
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

fn view_of<'a>(ptr: *mut LiveView) -> Result<&'a mut LiveView, i32> {
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
    unsafe { CStr::from_ptr(ptr) }.to_str().map_err(|_| -3)
}

/// Run `action`, converting panics to `-9`.
///
/// Slint component state is full of interior mutability, so closures over a
/// view are never `UnwindSafe` by default. The assertion here is sound
/// because a caught panic POISONS the view: the host contract requires
/// destroying the view after any `-9` (documented on the C ABI), so the
/// inconsistent state is never observed again.
fn guard<F>(action: F) -> i32
where
    F: FnOnce() -> Result<i32, i32>,
{
    match catch_unwind(AssertUnwindSafe(action)) {
        Ok(result) => result.unwrap_or_else(|code| code),
        Err(_) => -9,
    }
}

/// ABI handshake version. The host refuses any library reporting otherwise.
#[no_mangle]
pub extern "C" fn vayren_live_abi_version() -> c_uint {
    ABI_VERSION
}

/// Create an embedded view. Physical pixels; `scale_factor` maps them to
/// logical Slint units. Returns null on failure (never a half-built view).
#[no_mangle]
pub extern "C" fn vayren_live_view_create(
    width_px: c_uint,
    height_px: c_uint,
    scale_factor: c_float,
) -> *mut LiveView {
    match catch_unwind(AssertUnwindSafe(|| {
        LiveView::create(width_px, height_px, scale_factor)
    })) {
        Ok(Ok(view)) => Box::into_raw(view),
        _ => std::ptr::null_mut(),
    }
}

/// Destroy a view created by `create`. Null is a no-op.
#[no_mangle]
pub extern "C" fn vayren_live_view_destroy(view: *mut LiveView) {
    let _ = catch_unwind(AssertUnwindSafe(|| {
        if !view.is_null() {
            // SAFETY: inverse of `Box::into_raw` in `create`.
            unsafe { drop(Box::from_raw(view)) };
        }
    }));
}

/// Resize (physical pixels) and/or change scale. Returns 0 on success.
#[no_mangle]
pub extern "C" fn vayren_live_view_resize(
    view: *mut LiveView,
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

/// Replace the backend snapshot from bridge JSON (the
/// `_live_state_provider` schema — see `live::LiveState::apply_snapshot`).
/// Unknown keys ignored; mistyped keys degrade honestly. Returns 0.
#[no_mangle]
pub extern "C" fn vayren_live_view_set_snapshot(
    view: *mut LiveView,
    json_utf8: *const c_char,
) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        let text = cstr(json_utf8)?;
        let value: serde_json::Value = serde_json::from_str(text).map_err(|_| -4)?;
        {
            let mut state = view.state.borrow_mut();
            state.apply_snapshot(&value);
        }
        apply_view(&view._ui, &view.state.borrow());
        view.window.request_redraw();
        Ok(0)
    })
}

/// Pop the next accepted UI action (JSON) into `out_buf` (NUL-terminated
/// UTF-8). Returns the byte length written (0 = queue empty, `-5` = buffer
/// too small — the event is kept for a larger buffer).
#[no_mangle]
pub extern "C" fn vayren_live_view_next_event(
    view: *mut LiveView,
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

/// Pump Slint timers/animations. Call regularly from the host pump (cheap).
/// Returns 0 on success.
#[no_mangle]
pub extern "C" fn vayren_live_view_tick(view: *mut LiveView) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        slint::platform::update_timers_and_animations();
        Ok(0)
    })
}

/// Render into the caller buffer (`width*height*3` RGB bytes, physical size
/// given at create/resize). Returns 1 when pixels were painted, 0 when the
/// frame was clean (buffer untouched — keep showing the previous frame).
#[no_mangle]
pub extern "C" fn vayren_live_view_render(
    view: *mut LiveView,
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
    view: *mut LiveView,
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

/// Pointer moved (logical units, legacy logical coordinates map 1:1).
#[no_mangle]
pub extern "C" fn vayren_live_view_pointer_move(
    view: *mut LiveView,
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
    view: *mut LiveView,
    pressed: bool,
    x: c_float,
    y: c_float,
    button: c_int,
) -> c_int {
    let resolved = match LiveView::button(button) {
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

/// Pointer button press. `button`: 0 left, 1 right, 2 middle.
#[no_mangle]
pub extern "C" fn vayren_live_view_pointer_press(
    view: *mut LiveView,
    x: c_float,
    y: c_float,
    button: c_int,
) -> c_int {
    pointer_button(view, true, x, y, button)
}

/// Pointer button release. `button`: 0 left, 1 right, 2 middle.
#[no_mangle]
pub extern "C" fn vayren_live_view_pointer_release(
    view: *mut LiveView,
    x: c_float,
    y: c_float,
    button: c_int,
) -> c_int {
    pointer_button(view, false, x, y, button)
}

/// Pointer left the view (clears hover state).
#[no_mangle]
pub extern "C" fn vayren_live_view_pointer_leave(view: *mut LiveView) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        view.window.dispatch_event(WindowEvent::PointerExited);
        Ok(0)
    })
}

/// Mouse wheel. Deltas are logical pixels (positive = down/right).
#[no_mangle]
pub extern "C" fn vayren_live_view_scroll(
    view: *mut LiveView,
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

/// Key event. `text_utf8` is the printable text ("" for pure modifiers).
/// `pressed`: nonzero = press, zero = release.
#[no_mangle]
pub extern "C" fn vayren_live_view_key(
    view: *mut LiveView,
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

/// Nonzero when a UI intent was accepted since the last ack: the host
/// should re-pull backend state and push a fresh snapshot promptly.
#[no_mangle]
pub extern "C" fn vayren_live_view_refresh_requested(view: *mut LiveView) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        Ok(i32::from(view.refresh_requested.get()))
    })
}

/// Clear the refresh-requested flag after re-pushing state.
#[no_mangle]
pub extern "C" fn vayren_live_view_ack_refresh(view: *mut LiveView) -> c_int {
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

    const IDLE: &str = r#"{
        "mode": "PAPER",
        "session_status": "STOPPED",
        "broker": {"name": "NOT CONFIGURED", "connected": null, "reason": "no live venue adapter"},
        "gates": [{"name": "BROKER_ADAPTER_READY", "status": "NOT READY", "reason": "live gate off"}],
        "can_arm": false,
        "can_halt": false,
        "start_blockers": ["no strategy selected"],
        "kill": {"halted": false},
        "positions": [], "orders": [], "fills": [], "events": [],
        "available_strategies": ["OBR"], "available_symbols": ["TEST"],
        "selected_symbols": [], "available_timeframes": ["15m"],
        "selected_timeframe": "15m", "quantity": 10.0,
        "market_symbol": "", "market_timeframe": "15m", "market_bars": null
    }"#;

    const RUNNING: &str = r#"{
        "mode": "PAPER",
        "session_status": "RUNNING",
        "broker": {"name": "paper", "connected": true},
        "gates": [],
        "can_arm": false,
        "can_halt": true,
        "start_blockers": [],
        "kill": {"halted": false},
        "positions": [{"symbol": "TEST", "side": "LONG", "quantity": 10,
                       "entry_price": 100.0, "current_price": 110.0,
                       "pnl": 100.0, "status": "OPEN"}],
        "position": null,
        "orders": [{"order_id": "c1", "symbol": "TEST", "side": "BUY",
                    "quantity": 10, "type": "LIMIT", "price": 100.0,
                    "status": "OPEN", "time": "t", "strategy": "OBR", "broker": "paper"}],
        "fills": [{"time": "t", "symbol": "TEST", "side": "BUY",
                   "quantity": 10, "price": 100.0, "order_id": "c1", "strategy": "OBR"}],
        "pnl": {"realized": 0.0, "unrealized": 100.0, "exposure": 1100.0,
                "orders": 1, "fills": 1, "wins": 1, "losses": 0},
        "risk": {"status": "READY", "limits": [], "decisions": []},
        "reconciliation": {"status": "CLEAN", "positions": 1, "orders": 1,
                           "last_check": "", "mismatches": [], "blocks_live": false},
        "events": [{"timestamp": "t", "strategy": "OBR", "symbol": "TEST",
                    "event": "ORDER_FILL", "status": "ok"}],
        "available_strategies": ["OBR"], "available_symbols": ["TEST"],
        "selected_symbols": ["TEST"], "available_timeframes": ["15m"],
        "selected_timeframe": "15m", "quantity": 10.0,
        "market_symbol": "TEST", "market_timeframe": "15m",
        "market_bars": [{"o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5}]
    }"#;

    fn make_view() -> *mut LiveView {
        let view = vayren_live_view_create(900, 640, 1.0);
        assert!(!view.is_null());
        view
    }

    fn snapshot(view: *mut LiveView, text: &str) {
        let c = std::ffi::CString::new(text).unwrap();
        assert_eq!(vayren_live_view_set_snapshot(view, c.as_ptr()), 0);
    }

    fn with_view<R, F: FnOnce(&mut LiveView) -> R>(view: *mut LiveView, f: F) -> R {
        // SAFETY: non-null handle from create, same (test) thread.
        f(unsafe { &mut *view })
    }

    #[test]
    fn lifecycle_renders_and_pops_varied_pixels() {
        let view = make_view();
        snapshot(view, RUNNING);
        let mut buffer = vec![0x7Fu8; 900 * 640 * 3];
        assert_eq!(vayren_live_view_tick(view), 0);
        assert_eq!(
            vayren_live_view_render(view, buffer.as_mut_ptr(), buffer.len()),
            1
        );
        let first = buffer[0];
        assert!(
            buffer.iter().any(|b| *b != first),
            "rendered frame must contain varied pixels"
        );
        // Clean frame afterwards: nothing repainted.
        buffer.fill(0x7F);
        assert_eq!(
            vayren_live_view_render(view, buffer.as_mut_ptr(), buffer.len()),
            0
        );
        vayren_live_view_destroy(view);
    }

    #[test]
    fn blocked_start_emits_no_event_running_stop_does() {
        let view = make_view();
        snapshot(view, IDLE);
        with_view(view, |v| v.state.borrow_mut().start());
        assert!(with_view(view, |v| v.state.borrow_mut().take_action()).is_none());
        // The rejected request changed no runtime state either.
        assert!(with_view(view, |v| {
            matches!(v.state.borrow().session, live::SessionStatus::Stopped)
        }));

        snapshot(view, RUNNING);
        with_view(view, |v| v.state.borrow_mut().stop());
        let action = with_view(view, |v| v.state.borrow_mut().take_action());
        let parsed: serde_json::Value = serde_json::from_str(&action.unwrap()).unwrap();
        assert_eq!(parsed["action"], "stop");
        // The STOP request did not fake a session transition either.
        assert!(with_view(view, |v| {
            matches!(v.state.borrow().session, live::SessionStatus::Running)
        }));
        vayren_live_view_destroy(view);
    }

    #[test]
    fn rejects_bad_input_fail_closed() {
        assert_eq!(
            vayren_live_view_render(std::ptr::null_mut(), std::ptr::null_mut(), 0),
            -1
        );
        let view = make_view();
        let bad = std::ffi::CString::new("{nope").unwrap();
        assert_eq!(vayren_live_view_set_snapshot(view, bad.as_ptr()), -4);
        assert_eq!(vayren_live_view_set_snapshot(view, std::ptr::null()), -2);
        assert_eq!(vayren_live_view_resize(view, 0, 600, 1.0), -5);
        assert_eq!(vayren_live_view_pointer_press(view, 1.0, 1.0, 9), -5);
        let mut tiny = vec![0u8; 8];
        assert_eq!(
            vayren_live_view_render(view, tiny.as_mut_ptr(), tiny.len()),
            -5
        );
        vayren_live_view_destroy(view);
        vayren_live_view_destroy(std::ptr::null_mut());
    }

    #[test]
    fn next_event_drains_and_roundtrips() {
        let view = make_view();
        snapshot(view, RUNNING);
        let mut buf = [0u8; 256];
        // Empty queue → 0.
        assert_eq!(
            vayren_live_view_next_event(view, buf.as_mut_ptr(), buf.len()),
            0
        );
        with_view(view, |v| v.state.borrow_mut().halt()); // can_halt true (RUNNING)
        let len = vayren_live_view_next_event(view, buf.as_mut_ptr(), buf.len());
        assert!(len > 0, "halt accepted → one queued event");
        let parsed: serde_json::Value = serde_json::from_slice(&buf[..len as usize]).unwrap();
        assert_eq!(parsed["action"], "halt");
        // Forwarded intent did not self-apply the kill switch.
        assert!(!with_view(view, |v| v.state.borrow().kill_halted));
        assert_eq!(
            vayren_live_view_next_event(view, buf.as_mut_ptr(), buf.len()),
            0
        );
        // Buffer too small keeps the event for the next drain.
        with_view(view, |v| {
            v.state
                .borrow_mut()
                .push_host_action(serde_json::json!({"action": "stop", "pad": "x".repeat(40)}))
        });
        let mut tiny = [0u8; 8];
        assert_eq!(
            vayren_live_view_next_event(view, tiny.as_mut_ptr(), tiny.len()),
            -5
        );
        let len = vayren_live_view_next_event(view, buf.as_mut_ptr(), buf.len());
        assert!(len > 0);
        assert_eq!(vayren_live_view_ack_refresh(view), 0);
        assert_eq!(vayren_live_view_refresh_requested(view), 0);
        vayren_live_view_destroy(view);
    }
}
