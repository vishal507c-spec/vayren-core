//! Embeddable native Portfolio view — offscreen Slint host for the Qt shell.
//!
//! Architecture (constitution §3: Rust+Slint owns ALL native UI state and
//! presentation):
//!
//! ```text
//! Qt (separate process? NO — same process, Qt main thread only)
//!   │  SlintPortfolioHost (dumb viewport: blits pixels, forwards events)
//!   │  C ABI below (plain integers, UTF-8 JSON, RGB bytes — no objects)
//!   ▼
//! THIS crate (Rust owns view-model + interaction state + pixels)
//!   │  PortfolioState / project() (reused from vayren-shell, single owner)
//!   │  PortfolioHostWindow (hosts the EXISTING verified PortfolioScreen;
//!   │    ui/portfolio_host.slint is pure pass-through, zero visual delta)
//!   │  MinimalSoftwareWindow (documented Slint offscreen path, no event loop)
//! ```
//!
//! Threading: every C ABI function must be called on the SAME thread that
//! created the view (Slint handles are `!Send`). The Qt host calls from its
//! GUI thread only; violations return an error code, never UB. No threads
//! are spawned here; no Qt headers are needed to build this crate.
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
    Platform, PointerEventButton, WindowAdapter, WindowEvent,
};
use slint::{ComponentHandle, LogicalPosition, PhysicalSize, Rgb8Pixel, SharedString};
use vayren_shell::portfolio::{self, PortfolioState};

pub const ABI_VERSION: u32 = 1;
const MIN_SCALE: f32 = 0.25;
const MAX_SCALE: f32 = 8.0;

// Window handed to each view at creation. `PortfolioHostWindow::new()`
// pulls it through `EmbedPlatform::create_window_adapter` synchronously
// on the calling thread, so a thread-local pending slot is sufficient —
// and it statically enforces the single-GUI-thread contract (a second
// thread simply finds no pending window instead of racing one).
thread_local! {
    static PENDING_WINDOW: RefCell<Option<Rc<MinimalSoftwareWindow>>> = const { RefCell::new(None) };
}

struct EmbedPlatform;

impl Platform for EmbedPlatform {
    fn create_window_adapter(&self) -> Result<Rc<dyn WindowAdapter>, slint::PlatformError> {
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

/// One embedded Portfolio view: Slint component + view-model + pixels.
pub struct PortfolioView {
    window: Rc<MinimalSoftwareWindow>,
    _ui: PortfolioHostWindow,
    state: Rc<RefCell<PortfolioState>>,
    refresh_requested: Rc<Cell<bool>>,
    width_px: u32,
    height_px: u32,
    scale_factor: f32,
    thread: std::thread::ThreadId,
}

fn apply_state(ui: &PortfolioHostWindow, state: &PortfolioState) {
    // Setter mapping mirrors `shell::apply_portfolio` 1:1 (same view-model,
    // different bind target). If that function changes shape, THIS function
    // must change with it — both bind `portfolio::project()` output.
    let view = portfolio::project(state);
    ui.set_account_title(view.account_title.into());
    ui.set_account_tone(view.account_tone.badge());
    ui.set_account_info(view.account_info.into());
    ui.set_account_detail(view.account_detail.into());
    ui.set_updated_label(state.updated_label.clone().into());
    ui.set_kpis_primary(
        Rc::new(slint::VecModel::from(
            view.kpis_primary
                .into_iter()
                .map(|k| PortfolioKpi {
                    label: k.label.into(),
                    value: k.value.into(),
                    tone: k.tone.badge(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_kpis_secondary(
        Rc::new(slint::VecModel::from(
            view.kpis_secondary
                .into_iter()
                .map(|k| PortfolioKpi {
                    label: k.label.into(),
                    value: k.value.into(),
                    tone: k.tone.badge(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_perf_has_data(view.perf_has_data);
    ui.set_perf_trades(view.perf_trades.into());
    ui.set_perf_wins(view.perf_wins.into());
    ui.set_perf_losses(view.perf_losses.into());
    ui.set_perf_winrate(view.perf_winrate.into());
    ui.set_gates(
        Rc::new(slint::VecModel::from(
            view.gates
                .into_iter()
                .map(|g| PortfolioGate {
                    label: g.label.into(),
                    status: g.status.into(),
                    tone: g.tone.badge(),
                    detail: g.detail.into(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_positions_count(view.positions_count.into());
    ui.set_has_positions(!view.positions.is_empty());
    ui.set_positions(
        Rc::new(slint::VecModel::from(
            view.positions
                .into_iter()
                .map(|p| PortfolioPosition {
                    symbol: p.symbol.into(),
                    side: p.side.into(),
                    qty: p.qty.into(),
                    avg: p.avg.into(),
                    ltp: p.ltp.into(),
                    value: p.value.into(),
                    pnl: p.pnl.into(),
                    pnl_tone: p.pnl_tone.badge(),
                    alloc: p.alloc.into(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_positions_empty_title(view.positions_empty_title.into());
    ui.set_positions_empty_detail(view.positions_empty_detail.into());
    ui.set_selected_position(state.selected_position.map(|i| i as i32).unwrap_or(-1));
    ui.set_allocation(
        Rc::new(slint::VecModel::from(
            view.allocation
                .into_iter()
                .map(|a| PortfolioAlloc {
                    symbol: a.symbol.into(),
                    pct_label: a.pct_label.into(),
                    pct: a.pct,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_alloc_has_data(view.alloc_has_data);
    ui.set_detail_symbol(view.detail.symbol.into());
    ui.set_detail_side_tone(if view.detail.side.is_empty() { 0 } else { 4 });
    ui.set_detail_side(view.detail.side.into());
    ui.set_detail_qty(view.detail.qty.into());
    ui.set_detail_avg(view.detail.avg.into());
    ui.set_detail_value(view.detail.value.into());
    ui.set_detail_pnl(view.detail.pnl.into());
    ui.set_detail_pnl_tone(view.detail.pnl_tone.badge());
    ui.set_detail_alloc(view.detail.alloc.into());
    ui.set_orders_tab(view.orders_tab as i32);
    ui.set_orders(
        Rc::new(slint::VecModel::from(
            view.orders
                .into_iter()
                .map(|o| PortfolioOrder {
                    id: o.id.into(),
                    time: o.time.into(),
                    symbol: o.symbol.into(),
                    side: o.side.into(),
                    qty: o.qty.into(),
                    status: o.status.into(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_fills(
        Rc::new(slint::VecModel::from(
            view.fills
                .into_iter()
                .map(|f| PortfolioFill {
                    time: f.time.into(),
                    symbol: f.symbol.into(),
                    side: f.side.into(),
                    qty: f.qty.into(),
                    price: f.price.into(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_active_has_rows(view.active_has_rows);
    ui.set_active_empty_title(view.active_empty_title.into());
    ui.set_active_empty_detail(view.active_empty_detail.into());
    ui.set_health_label(view.health_label.into());
    ui.set_health_tone(view.health_tone.badge());
    ui.set_health_note(view.health_note.into());
    ui.set_risk_rows(
        Rc::new(slint::VecModel::from(
            view.risk_rows
                .into_iter()
                .map(|r| PortfolioRisk {
                    label: r.label.into(),
                    value: r.value.into(),
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_risk_has_data(view.risk_has_data);
    ui.set_risk_empty_detail(view.risk_empty_detail.into());
}

fn wire_view(
    ui: &PortfolioHostWindow,
    state: Rc<RefCell<PortfolioState>>,
    refresh: Rc<Cell<bool>>,
) {
    // Mirrors `shell::wire_portfolio`: Slint reports, Rust mutates centrally.
    let strong = state.clone();
    let handle = ui.as_weak();
    let refresh_flag = refresh.clone();
    ui.on_refresh_requested(move || {
        refresh_flag.set(true);
        if let Some(ui) = handle.upgrade() {
            apply_state(&ui, &strong.borrow());
        }
    });
    let strong = state.clone();
    let handle = ui.as_weak();
    ui.on_orders_tab_picked(move |tab| {
        strong.borrow_mut().set_orders_tab(tab.max(0) as usize);
        if let Some(ui) = handle.upgrade() {
            apply_state(&ui, &strong.borrow());
        }
    });
    let strong = state.clone();
    let handle = ui.as_weak();
    ui.on_position_picked(move |index| {
        let _ = strong.borrow_mut().select_position(index.max(0) as usize);
        if let Some(ui) = handle.upgrade() {
            apply_state(&ui, &strong.borrow());
        }
    });
}

impl PortfolioView {
    fn create(width_px: u32, height_px: u32, scale_factor: f32) -> Result<Box<Self>, i32> {
        if width_px == 0 || height_px == 0 {
            return Err(-5);
        }
        ensure_platform()?;
        let scale = scale_factor.clamp(MIN_SCALE, MAX_SCALE);
        let window = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
        PENDING_WINDOW.with(|slot| slot.borrow_mut().replace(window.clone()));
        let ui = PortfolioHostWindow::new().map_err(|_| -7)?;
        // Drain our own pending slot if codegen took another path (never
        // silently leak a window into a later view).
        PENDING_WINDOW.with(|slot| slot.borrow_mut().take());
        let state = Rc::new(RefCell::new(PortfolioState::default()));
        let refresh = Rc::new(Cell::new(false));
        wire_view(&ui, state.clone(), refresh.clone());
        apply_state(&ui, &state.borrow());
        let mut view = Box::new(PortfolioView {
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

fn view_of<'a>(ptr: *mut PortfolioView) -> Result<&'a mut PortfolioView, i32> {
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
pub extern "C" fn vayren_portfolio_abi_version() -> c_uint {
    ABI_VERSION
}

/// Create an embedded view. Physical pixels; `scale_factor` maps them to
/// logical Slint units. Returns null on failure (check the code path, never
/// a half-built view).
#[no_mangle]
pub extern "C" fn vayren_portfolio_view_create(
    width_px: c_uint,
    height_px: c_uint,
    scale_factor: c_float,
) -> *mut PortfolioView {
    match catch_unwind(AssertUnwindSafe(|| {
        PortfolioView::create(width_px, height_px, scale_factor)
    })) {
        Ok(Ok(view)) => Box::into_raw(view),
        _ => std::ptr::null_mut(),
    }
}

/// Destroy a view created by `create`. Null is a no-op.
#[no_mangle]
pub extern "C" fn vayren_portfolio_view_destroy(view: *mut PortfolioView) {
    let _ = catch_unwind(AssertUnwindSafe(|| {
        if !view.is_null() {
            // SAFETY: inverse of `Box::into_raw` in `create`.
            unsafe { drop(Box::from_raw(view)) };
        }
    }));
}

/// Resize (physical pixels) and/or change scale. Returns 0 on success.
#[no_mangle]
pub extern "C" fn vayren_portfolio_view_resize(
    view: *mut PortfolioView,
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

/// Replace the backend snapshot from bridge JSON (see
/// `PortfolioSnapshot::from_json`). Unknown keys ignored. Returns 0.
#[no_mangle]
pub extern "C" fn vayren_portfolio_view_set_snapshot(
    view: *mut PortfolioView,
    json_utf8: *const c_char,
) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        let text = cstr(json_utf8)?;
        let value: serde_json::Value = serde_json::from_str(text).map_err(|_| -4)?;
        let snapshot = portfolio::PortfolioSnapshot::from_json(&value);
        // Refresh stamp rides along as a plain string key (display-only,
        // set by the bridge — never part of financial state).
        let stamp = value
            .get("updated_label")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        {
            let mut state = view.state.borrow_mut();
            state.snapshot = snapshot;
            if !stamp.is_empty() {
                state.updated_label = stamp;
            }
        }
        apply_state(&view._ui, &view.state.borrow());
        view.window.request_redraw();
        Ok(0)
    })
}

/// Pump Slint timers/animations. Call regularly from the host pump (cheap).
/// Returns 0 on success.
#[no_mangle]
pub extern "C" fn vayren_portfolio_view_tick(view: *mut PortfolioView) -> c_int {
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
pub extern "C" fn vayren_portfolio_view_render(
    view: *mut PortfolioView,
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
    view: *mut PortfolioView,
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

/// Pointer moved (logical units, Qt logical coordinates map 1:1).
#[no_mangle]
pub extern "C" fn vayren_portfolio_view_pointer_move(
    view: *mut PortfolioView,
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
    view: *mut PortfolioView,
    pressed: bool,
    x: c_float,
    y: c_float,
    button: c_int,
) -> c_int {
    let resolved = match PortfolioView::button(button) {
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
pub extern "C" fn vayren_portfolio_view_pointer_press(
    view: *mut PortfolioView,
    x: c_float,
    y: c_float,
    button: c_int,
) -> c_int {
    pointer_button(view, true, x, y, button)
}

/// Pointer button release. `button`: 0 left, 1 right, 2 middle.
#[no_mangle]
pub extern "C" fn vayren_portfolio_view_pointer_release(
    view: *mut PortfolioView,
    x: c_float,
    y: c_float,
    button: c_int,
) -> c_int {
    pointer_button(view, false, x, y, button)
}

/// Pointer left the view (clears hover state).
#[no_mangle]
pub extern "C" fn vayren_portfolio_view_pointer_leave(view: *mut PortfolioView) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        view.window.dispatch_event(WindowEvent::PointerExited);
        Ok(0)
    })
}

/// Mouse wheel. Deltas are logical pixels (positive = down/right).
#[no_mangle]
pub extern "C" fn vayren_portfolio_view_scroll(
    view: *mut PortfolioView,
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
pub extern "C" fn vayren_portfolio_view_key(
    view: *mut PortfolioView,
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

/// Nonzero when the Slint refresh action fired since the last ack: the host
/// should re-pull backend state and push a fresh snapshot.
#[no_mangle]
pub extern "C" fn vayren_portfolio_view_refresh_requested(view: *mut PortfolioView) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        Ok(i32::from(view.refresh_requested.get()))
    })
}

/// Clear the refresh-requested flag after re-pushing state.
#[no_mangle]
pub extern "C" fn vayren_portfolio_view_ack_refresh(view: *mut PortfolioView) -> c_int {
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

    const FUNDED: &str = r#"{
        "broker": {"name": "paper", "environment": "paper",
                   "connected": true, "status": "CONNECTED"},
        "funds": {"equity": 100000.0, "available": 80000.0, "used": 20000.0},
        "position": {"symbol": "TEST", "side": "LONG", "quantity": 10,
                     "avg_price": 100.0, "current_price": 110.0,
                     "exposure": 1100.0, "unrealized": 100.0},
        "orders": [{"order_id": "c1", "symbol": "TEST", "side": "BUY",
                    "quantity": 10, "status": "FILLED"}],
        "fills": [{"time": "t", "symbol": "TEST", "quantity": 10, "price": 100.0}],
        "pnl": {"total": 100.0, "unrealized": 100.0, "realized": 0.0,
                "wins": 1, "losses": 0},
        "risk": {"status": "READY"},
        "reconciliation": {"status": "CLEAN"},
        "lifecycle": "RUNNING",
        "mode": "PAPER"
    }"#;

    fn make_view() -> *mut PortfolioView {
        let view = vayren_portfolio_view_create(800, 600, 1.0);
        assert!(!view.is_null());
        view
    }

    #[test]
    fn lifecycle_renders_funded_snapshot() {
        let view = make_view();
        let text = std::ffi::CString::new(FUNDED).unwrap();
        assert_eq!(vayren_portfolio_view_set_snapshot(view, text.as_ptr()), 0);
        // Physical 800x600 RGB buffer, pre-filled with a sentinel color.
        let mut buffer = vec![0x7Fu8; 800 * 600 * 3];
        assert_eq!(vayren_portfolio_view_tick(view), 0);
        let painted = vayren_portfolio_view_render(view, buffer.as_mut_ptr(), buffer.len());
        assert_eq!(painted, 1);
        // Real content painted: buffer is no longer uniform.
        let first = buffer[0];
        assert!(
            buffer.iter().any(|b| *b != first),
            "rendered frame must contain varied pixels"
        );
        // Clean frame afterwards: nothing repainted, buffer untouched.
        buffer.fill(0x7F);
        assert_eq!(
            vayren_portfolio_view_render(view, buffer.as_mut_ptr(), buffer.len()),
            0
        );
        assert!(buffer.iter().all(|b| *b == 0x7F));
        vayren_portfolio_view_destroy(view);
    }

    #[test]
    fn rejects_bad_input_fail_closed() {
        assert_eq!(
            vayren_portfolio_view_render(std::ptr::null_mut(), std::ptr::null_mut(), 0),
            -1
        );
        let view = make_view();
        let bad = std::ffi::CString::new("{nope").unwrap();
        assert_eq!(vayren_portfolio_view_set_snapshot(view, bad.as_ptr()), -4);
        assert_eq!(
            vayren_portfolio_view_set_snapshot(view, std::ptr::null()),
            -2
        );
        assert_eq!(vayren_portfolio_view_resize(view, 0, 600, 1.0), -5);
        assert_eq!(vayren_portfolio_view_pointer_press(view, 1.0, 1.0, 9), -5);
        let mut tiny = vec![0u8; 8];
        assert_eq!(
            vayren_portfolio_view_render(view, tiny.as_mut_ptr(), tiny.len()),
            -5
        );
        vayren_portfolio_view_destroy(view);
        vayren_portfolio_view_destroy(std::ptr::null_mut());
    }

    #[test]
    fn refresh_flag_roundtrip() {
        let view = make_view();
        assert_eq!(vayren_portfolio_view_refresh_requested(view), 0);
        vayren_portfolio_view_ack_refresh(view);
        assert_eq!(vayren_portfolio_view_refresh_requested(view), 0);
        vayren_portfolio_view_destroy(view);
    }
}
