//! Embeddable native System view — offscreen Slint host for the legacy shell.
//!
//! Architecture (AI_ENTRY.md §1: Rust+Slint owns ALL native UI state and
//! presentation) — the proven `vayren-portfolio-view` / `vayren-live-view`
//! mechanism reused for SYSTEM, minus the action channel: the hosted
//! `BrokerPanel` is a read-only status surface (no callbacks exist on it),
//! so this crate only feeds backend snapshots and paints pixels.
//!
//! ```text
//! legacy main window (one process, GUI thread)
//!   │  SlintSystemHost (dumb viewport: blits pixels, forwards events,
//!   │    pushes broker snapshots)
//!   │  C ABI below (plain integers, UTF-8 JSON, RGB bytes — no objects)
//!   ▼
//! THIS crate (Rust owns view-model + pixels)
//!   │  view_model::BrokerPanel (reused from vayren-shell, single owner —
//!   │    the SAME view-model the native shell binds)
//!   │  SystemHostWindow (hosts the EXISTING verified BrokerPanel;
//!   │    ui/system_host.slint is pure pass-through, zero visual delta)
//!   │  MinimalSoftwareWindow (documented Slint offscreen path, no event loop)
//! ```
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
use slint::{LogicalPosition, PhysicalSize, Rgb8Pixel, SharedString, VecModel};
use vayren_shell::shell::env_kind;
use vayren_shell::view_model::{BrokerPanel, Environment, HealthState};
use vayren_shell::broker_connection::{BrokerWorkspace, ConnectionState};

pub const ABI_VERSION: u32 = 2;
const MIN_SCALE: f32 = 0.25;
const MAX_SCALE: f32 = 8.0;

// Window handed to each view at creation. `SystemHostWindow::new()` pulls
// it through `EmbedPlatform::create_window_adapter` synchronously on the
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
        // This process already runs Slint (e.g. hosts sharing one process):
        // our stateless platform is interchangeable, proceed.
        Err(_) => Ok(()),
    }
}

/// One embedded System view: Slint component + view-model + pixels.
pub struct SystemView {
    window: Rc<MinimalSoftwareWindow>,
    _ui: SystemHostWindow,
    panel: BrokerPanel,
    field_keys: Rc<RefCell<Vec<String>>>,
    actions: Rc<RefCell<std::collections::VecDeque<String>>>,
    refresh_requested: Rc<Cell<bool>>,
    refresh_busy: Rc<Cell<bool>>,
    width_px: u32,
    height_px: u32,
    scale_factor: f32,
    thread: std::thread::ThreadId,
}

/// Project the broker view-model onto the host window. Setter mapping
/// mirrors `shell::apply` 1:1 (same view-model, different bind target); if
/// that function changes shape, THIS function must change with it.
fn apply_view(ui: &SystemHostWindow, panel: &BrokerPanel, refresh_busy: bool) {
    ui.set_broker_name(panel.display_name.clone().into());
    ui.set_broker_id(panel.broker_id.clone().into());
    ui.set_env_kind(env_kind(panel.environment));
    ui.set_env_label(panel.environment.label().into());
    ui.set_health_label(panel.health.label().into());
    ui.set_health_connected(panel.connected());
    ui.set_live_ready(panel.live_ready());
    // Parity card projection (same `project_card` the shell binds).
    // `refresh_busy` is the transient SYNCING feedback (cleared by the
    // next applied snapshot, same contract as legacy).
    let card = panel.project_card(refresh_busy);
    ui.set_status_glyph(card.status_glyph.into());
    ui.set_status_label(card.status_label.into());
    ui.set_status_tone(card.status_tone);
    ui.set_status_explanation(card.status_explanation.into());
    ui.set_reason_line(card.reason_line.into());
    ui.set_account_id(card.account_id.into());
    ui.set_conn_label(card.conn_label.into());
    ui.set_conn_tone(card.conn_tone);
    ui.set_last_sync(card.last_sync.into());
    ui.set_health_summary(card.health_summary.into());
    ui.set_health_summary_tone(card.health_summary_tone);
    ui.set_check_rows(
        Rc::new(VecModel::from(
            card.check_rows
                .into_iter()
                .map(|c| BrokerCheckRow {
                    key: c.key.into(),
                    label: c.label.into(),
                    value: c.value.into(),
                    tone: c.tone,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_funds_line(card.funds_line.into());
    ui.set_funds_sub(card.funds_sub.into());
    ui.set_positions_line(card.positions_line.into());
    ui.set_positions_sub(card.positions_sub.into());
    ui.set_orders_line(card.orders_line.into());
    ui.set_orders_sub(card.orders_sub.into());
    ui.set_credentials_line(card.credentials_line.into());
    ui.set_callback_display(card.callback_display.into());
    ui.set_refresh_visible(card.refresh_visible);
    ui.set_refresh_label(card.refresh_label.into());
    ui.set_refresh_enabled(card.refresh_enabled);
    ui.set_refresh_primary(card.refresh_primary);
    ui.set_login_label(card.login_label.into());
    ui.set_login_enabled(card.login_enabled);
    ui.set_login_primary(card.login_primary);
    ui.set_configure_label(card.configure_label.into());
    ui.set_disconnect_visible(card.disconnect_visible);
    ui.set_disconnect_enabled(card.disconnect_enabled);
    ui.set_remove_visible(card.remove_visible);
    ui.set_blockers(
        Rc::new(VecModel::from(
            panel
                .blockers
                .iter()
                .map(|b| b.clone().into())
                .collect::<Vec<slint::SharedString>>(),
        ))
        .into(),
    );
}

/// Project the CONNECTION workspace view-model onto the host window. Setter
/// mapping mirrors `shell::apply_connection` 1:1 (same view-model,
/// different bind target); if that function changes shape, THIS function
/// must change with it. Returns the workspace field keys in render order so
/// the action drain can zip connect values back onto schema keys.
fn apply_connection_view(ui: &SystemHostWindow, workspace: &BrokerWorkspace) -> Vec<String> {
    ui.set_conn_brokers(
        Rc::new(VecModel::from(
            workspace
                .brokers
                .iter()
                .map(|b| BrokerRowView {
                    id: b.id.clone().into(),
                    display_name: b.display_name.clone().into(),
                    mark: b.mark().into(),
                    venue_subtitle: b.venue_subtitle.clone().into(),
                    status_label: b.status_label().into(),
                    status_tone: b.status_tone(),
                    selected: b.selected,
                    connected: b.connected,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_conn_display_name(workspace.display_name.clone().into());
    ui.set_conn_mark(
        workspace
            .display_name
            .chars()
            .next()
            .map(|c| c.to_uppercase().to_string())
            .unwrap_or_default()
            .into(),
    );
    ui.set_conn_venue_subtitle(workspace.venue_subtitle.clone().into());
    ui.set_conn_env_label(workspace.env_label.clone().into());
    ui.set_conn_description(
        vayren_shell::broker_connection::description_line(&workspace.display_name).into(),
    );
    let (pill_label, pill_tone) = workspace.pill();
    ui.set_conn_pill_label(pill_label.into());
    ui.set_conn_pill_tone(pill_tone);
    ui.set_conn_status_message(workspace.status_message().into());
    ui.set_conn_fields(
        Rc::new(VecModel::from(
            workspace
                .fields
                .iter()
                .map(|f| CredentialFieldView {
                    key: f.key.clone().into(),
                    label: f.label.clone().into(),
                    placeholder: f.placeholder.clone().into(),
                    secret: f.secret,
                    required: f.required,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_conn_progress(
        Rc::new(VecModel::from(
            workspace
                .progress()
                .into_iter()
                .map(|s| ProgressStepView {
                    index: s.index,
                    title: s.title.into(),
                    subtitle: s.subtitle.into(),
                    state: s.state,
                })
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
    ui.set_conn_cta_label(workspace.cta().into());
    ui.set_conn_cta_arrow(!matches!(
        workspace.state,
        ConnectionState::Authenticating
            | ConnectionState::GettingToken
            | ConnectionState::Verifying
            | ConnectionState::Failed
    ));
    ui.set_conn_cta_enabled(workspace.can_connect);
    ui.set_conn_form_enabled(workspace.form_enabled());
    ui.set_conn_is_connected(workspace.state == ConnectionState::Connected);
    ui.set_conn_is_failed(workspace.state == ConnectionState::Failed);
    ui.set_conn_error_message(if workspace.state == ConnectionState::Failed {
        workspace.status_message().into()
    } else {
        "".into()
    });
    ui.set_conn_disconnect_visible(workspace.can_disconnect);
    ui.set_conn_disconnect_enabled(workspace.can_disconnect);
    ui.set_conn_help_caption(
        if workspace.display_name.trim().is_empty() {
            "View setup guide.".into()
        } else {
            format!("View setup guide for {}.", workspace.display_name.trim()).into()
        },
    );
    workspace.fields.iter().map(|f| f.key.clone()).collect()
}

/// Extract the collapsible ops-section facts (system-health grid + event
/// log) from the bridge snapshot. Absent/mistyped keys degrade to empty —
/// the Slint surface then shows its honest empty state (hidden sections).
/// Accepted shape (all optional): `"health_rows": [{"label","value","tone"}]`,
/// `"log_rows": [{"text","tone"}]` with tones on the shared 0–3 convention.
fn parse_ops_extras(value: &serde_json::Value) -> (Vec<HealthRow>, Vec<LogRow>) {
    let health = value
        .get("health_rows")
        .and_then(|v| v.as_array())
        .map(|list| {
            list.iter()
                .filter_map(|row| {
                    let label = row.get("label")?.as_str()?.to_string();
                    Some(HealthRow {
                        label: label.into(),
                        value: row
                            .get("value")
                            .and_then(|v| v.as_str())
                            .unwrap_or("—")
                            .into(),
                        tone: row.get("tone").and_then(|v| v.as_i64()).unwrap_or(0) as i32,
                    })
                })
                .collect()
        })
        .unwrap_or_default();
    let logs = value
        .get("log_rows")
        .and_then(|v| v.as_array())
        .map(|list| {
            list.iter()
                .filter_map(|row| {
                    let text = row.get("text")?.as_str()?.to_string();
                    Some(LogRow {
                        text: text.into(),
                        tone: row.get("tone").and_then(|v| v.as_i64()).unwrap_or(0) as i32,
                    })
                })
                .collect()
        })
        .unwrap_or_default();
    (health, logs)
}

/// Apply ops-section facts to the host window (empty keeps the retained
/// "--"/empty projections the Slint side defaults to).
fn apply_ops_extras(ui: &SystemHostWindow, health: Vec<HealthRow>, logs: Vec<LogRow>) {
    ui.set_health_rows(Rc::new(VecModel::from(health)).into());
    ui.set_log_rows(Rc::new(VecModel::from(logs)).into());
}

/// Wire the panel's action callbacks: Slint reports intent only; the host
/// drains `actions` (`next_event`) and dispatches to the Python
/// `BrokerManager` — the same signal contract the legacy cards carried.
/// `field_keys` holds the workspace schema keys in render order so the
/// connect intent can zip entered values back onto schema keys (values
/// travel once, inside this event, exactly like the previous `configure`
/// contract — snapshots never carry them).
fn wire_view(
    ui: &SystemHostWindow,
    actions: Rc<RefCell<std::collections::VecDeque<String>>>,
    refresh: Rc<Cell<bool>>,
    busy: Rc<Cell<bool>>,
    field_keys: Rc<RefCell<Vec<String>>>,
) {
    macro_rules! wire_action {
        ($register:ident, $payload:expr) => {{
            let queue = actions.clone();
            let refresh_flag = refresh.clone();
            ui.$register(move || {
                if let Ok(text) = serde_json::to_string(&$payload) {
                    queue.borrow_mut().push_back(text);
                }
                refresh_flag.set(true);
            });
        }};
    }
    // REFRESH also starts the transient SYNCING indicator (cleared by the
    // next applied snapshot — the legacy busy-timer replaced by data flow).
    {
        let queue = actions.clone();
        let refresh_flag = refresh.clone();
        let busy_flag = busy.clone();
        ui.on_refresh_requested(move || {
            if let Ok(text) = serde_json::to_string(&serde_json::json!({"action": "refresh"})) {
                queue.borrow_mut().push_back(text);
            }
            busy_flag.set(true);
            refresh_flag.set(true);
        });
    }
    // CONNECT on an unconfigured-but-unknown broker opens the inline form
    // (Slint-local); the emitted events carry manager-validated payloads.
    wire_action!(on_login_requested, serde_json::json!({"action": "login"}));
    wire_action!(
        on_disconnect_requested,
        serde_json::json!({"action": "disconnect"})
    );
    wire_action!(on_remove_requested, serde_json::json!({"action": "remove"}));
    wire_action!(on_copy_callback, serde_json::json!({"action": "copy_url"}));
    wire_action!(on_help_requested, serde_json::json!({"action": "help"}));
    wire_action!(on_add_requested, serde_json::json!({"action": "add"}));
    {
        let queue = actions.clone();
        let refresh_flag = refresh.clone();
        ui.on_select_requested(move |id: slint::SharedString| {
            let payload = serde_json::json!({
                "action": "select",
                "broker_id": id.as_str(),
            });
            if let Ok(text) = serde_json::to_string(&payload) {
                queue.borrow_mut().push_back(text);
            }
            refresh_flag.set(true);
        });
    }
    {
        let queue = actions.clone();
        let refresh_flag = refresh.clone();
        let keys = field_keys.clone();
        ui.on_connect_requested(
            move |v0: slint::SharedString,
                  v1: slint::SharedString,
                  v2: slint::SharedString,
                  v3: slint::SharedString,
                  v4: slint::SharedString,
                  v5: slint::SharedString| {
                // Zip entered values back onto the schema keys captured at
                // the last applied snapshot (values cross once, here).
                let entered = [v0, v1, v2, v3, v4, v5];
                let guard = keys.borrow();
                let mut values = serde_json::Map::new();
                for (key, val) in guard.iter().zip(entered.iter()) {
                    values.insert(key.clone(), serde_json::Value::String(val.to_string()));
                }
                let payload = serde_json::json!({"action": "connect", "values": values});
                if let Ok(text) = serde_json::to_string(&payload) {
                    queue.borrow_mut().push_back(text);
                }
                refresh_flag.set(true);
            },
        );
    }
    {
        let queue = actions.clone();
        let refresh_flag = refresh.clone();
        ui.on_config_saved(
            move |key: slint::SharedString, secret: slint::SharedString| {
                // The secret crosses the FFI boundary exactly once, in this
                // event payload (the legacy `configure_requested` contract).
                let payload = serde_json::json!({
                    "action": "configure",
                    "api_key": key.as_str(),
                    "api_secret": secret.as_str(),
                });
                if let Ok(text) = serde_json::to_string(&payload) {
                    queue.borrow_mut().push_back(text);
                }
                refresh_flag.set(true);
            },
        );
    }
}

impl SystemView {
    fn create(width_px: u32, height_px: u32, scale_factor: f32) -> Result<Box<Self>, i32> {
        if width_px == 0 || height_px == 0 {
            return Err(-5);
        }
        ensure_platform()?;
        let scale = scale_factor.clamp(MIN_SCALE, MAX_SCALE);
        let window = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
        PENDING_WINDOW.with(|slot| slot.borrow_mut().replace(window.clone()));
        let ui = SystemHostWindow::new().map_err(|_| -7)?;
        // Drain our own pending slot if codegen took another path (never
        // silently leak a window into a later view).
        PENDING_WINDOW.with(|slot| slot.borrow_mut().take());
        // Honest empty state until the first backend snapshot arrives.
        let empty = BrokerPanel {
            broker_id: String::new(),
            display_name: "NOT CONFIGURED".to_string(),
            environment: Environment::Paper,
            capabilities: Vec::new(),
            health: HealthState::Unknown,
            live_gates_ready: false,
            blockers: Vec::new(),
            ..Default::default()
        };
        apply_view(&ui, &empty, false);
        apply_connection_view(&ui, &BrokerWorkspace::from_json(&serde_json::Value::Null));
        let actions = Rc::new(RefCell::new(std::collections::VecDeque::new()));
        let refresh = Rc::new(Cell::new(false));
        let busy = Rc::new(Cell::new(false));
        let field_keys = Rc::new(RefCell::new(Vec::<String>::new()));
        wire_view(
            &ui,
            actions.clone(),
            refresh.clone(),
            busy.clone(),
            field_keys.clone(),
        );
        // Ops-section toggles are view-local (legacy dock visibility parity):
        // no event leaves the view for them.
        {
            let weak = ui.as_weak();
            ui.on_health_toggle(move || {
                if let Some(ui) = weak.upgrade() {
                    ui.set_health_open(!ui.get_health_open());
                }
            });
        }
        {
            let weak = ui.as_weak();
            ui.on_log_toggle(move || {
                if let Some(ui) = weak.upgrade() {
                    ui.set_log_open(!ui.get_log_open());
                }
            });
        }
        let mut view = Box::new(SystemView {
            window,
            _ui: ui,
            panel: empty,
            field_keys,
            actions,
            refresh_requested: refresh,
            refresh_busy: busy,
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
        // Scale FIRST: `set_size(PhysicalSize)` converts to logical units
        // with the window's CURRENT scale factor, so the factor must be
        // current before the size lands (otherwise a HiDPI host lays out
        // physical pixels as logical units — 1.5x oversize, right side
        // clipped: pills/CTA/progress cut off).
        self.window.dispatch_event(WindowEvent::ScaleFactorChanged {
            scale_factor: self.scale_factor,
        });
        self.window
            .set_size(PhysicalSize::new(self.width_px, self.height_px));
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

fn view_of<'a>(ptr: *mut SystemView) -> Result<&'a mut SystemView, i32> {
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
pub extern "C" fn vayren_system_abi_version() -> c_uint {
    ABI_VERSION
}

/// Create an embedded view. Physical pixels; `scale_factor` maps them to
/// logical Slint units. Returns null on failure (never a half-built view).
#[no_mangle]
pub extern "C" fn vayren_system_view_create(
    width_px: c_uint,
    height_px: c_uint,
    scale_factor: c_float,
) -> *mut SystemView {
    match catch_unwind(AssertUnwindSafe(|| {
        SystemView::create(width_px, height_px, scale_factor)
    })) {
        Ok(Ok(view)) => Box::into_raw(view),
        _ => std::ptr::null_mut(),
    }
}

/// Destroy a view created by `create`. Null is a no-op.
#[no_mangle]
pub extern "C" fn vayren_system_view_destroy(view: *mut SystemView) {
    let _ = catch_unwind(AssertUnwindSafe(|| {
        if !view.is_null() {
            // SAFETY: inverse of `Box::into_raw` in `create`.
            unsafe { drop(Box::from_raw(view)) };
        }
    }));
}

/// Resize (physical pixels) and/or change scale. Returns 0 on success.
#[no_mangle]
pub extern "C" fn vayren_system_view_resize(
    view: *mut SystemView,
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

/// Replace the broker snapshot from bridge JSON (see
/// `BrokerPanel::from_json`). Unknown keys ignored. The applied snapshot
/// also clears the transient SYNCING indicator (a refresh round-trip
/// completed). Returns 0.
#[no_mangle]
pub extern "C" fn vayren_system_view_set_snapshot(
    view: *mut SystemView,
    json_utf8: *const c_char,
) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        let text = cstr(json_utf8)?;
        let value: serde_json::Value = serde_json::from_str(text).map_err(|_| -4)?;
        let panel = BrokerPanel::from_json(&value);
        view.refresh_busy.set(false);
        apply_view(&view._ui, &panel, view.refresh_busy.get());
        // Connection workspace projection (same snapshot, workspace shape).
        let workspace = BrokerWorkspace::from_json(&value);
        let keys = apply_connection_view(&view._ui, &workspace);
        *view.field_keys.borrow_mut() = keys;
        // Ops-section facts (system health grid + event log) from the same
        // snapshot — absent keys keep the retained "--"/empty projections.
        let (health_rows, log_rows): (Vec<HealthRow>, Vec<LogRow>) = parse_ops_extras(&value);
        apply_ops_extras(&view._ui, health_rows, log_rows);
        view.panel = panel;
        view.window.request_redraw();
        Ok(0)
    })
}

/// Pop the next pending UI action (JSON) into `out_buf` (NUL-terminated
/// UTF-8). Returns the byte length written (0 = queue empty, `-5` = buffer
/// too small — the event is kept for a larger buffer).
#[no_mangle]
pub extern "C" fn vayren_system_view_next_event(
    view: *mut SystemView,
    out_buf: *mut c_uchar,
    out_len: usize,
) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        if out_buf.is_null() || out_len == 0 {
            return Err(-2);
        }
        let Some(text) = view.actions.borrow_mut().pop_front() else {
            return Ok(0);
        };
        if text.len() + 1 > out_len {
            view.actions.borrow_mut().push_front(text);
            return Err(-5);
        }
        // SAFETY: contract guarantees a writable `out_len`-byte buffer.
        let out = unsafe { std::slice::from_raw_parts_mut(out_buf, out_len) };
        out[..text.len()].copy_from_slice(text.as_bytes());
        out[text.len()] = 0;
        Ok(text.len() as i32)
    })
}

/// Nonzero when a UI action was queued since the last ack: the host should
/// dispatch events and re-pull broker state promptly.
#[no_mangle]
pub extern "C" fn vayren_system_view_refresh_requested(view: *mut SystemView) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        Ok(i32::from(view.refresh_requested.get()))
    })
}

/// Clear the refresh-requested flag after re-pushing state.
#[no_mangle]
pub extern "C" fn vayren_system_view_ack_refresh(view: *mut SystemView) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        view.refresh_requested.set(false);
        Ok(0)
    })
}

/// Pump Slint timers/animations. Call regularly from the host pump (cheap).
/// Returns 0 on success.
#[no_mangle]
pub extern "C" fn vayren_system_view_tick(view: *mut SystemView) -> c_int {
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
pub extern "C" fn vayren_system_view_render(
    view: *mut SystemView,
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
    view: *mut SystemView,
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
pub extern "C" fn vayren_system_view_pointer_move(
    view: *mut SystemView,
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
    view: *mut SystemView,
    pressed: bool,
    x: c_float,
    y: c_float,
    button: c_int,
) -> c_int {
    let resolved = match SystemView::button(button) {
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
pub extern "C" fn vayren_system_view_pointer_press(
    view: *mut SystemView,
    x: c_float,
    y: c_float,
    button: c_int,
) -> c_int {
    pointer_button(view, true, x, y, button)
}

/// Pointer button release. `button`: 0 left, 1 right, 2 middle.
#[no_mangle]
pub extern "C" fn vayren_system_view_pointer_release(
    view: *mut SystemView,
    x: c_float,
    y: c_float,
    button: c_int,
) -> c_int {
    pointer_button(view, false, x, y, button)
}

/// Pointer left the view (clears hover state).
#[no_mangle]
pub extern "C" fn vayren_system_view_pointer_leave(view: *mut SystemView) -> c_int {
    guard(|| {
        let view = view_of(view)?;
        view.check_thread()?;
        view.window.dispatch_event(WindowEvent::PointerExited);
        Ok(0)
    })
}

/// Mouse wheel. Deltas are logical pixels (positive = down/right).
#[no_mangle]
pub extern "C" fn vayren_system_view_scroll(
    view: *mut SystemView,
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
pub extern "C" fn vayren_system_view_key(
    view: *mut SystemView,
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

#[cfg(test)]
mod tests {
    use super::*;
    use slint::Model;

    const CONNECTED: &str = r#"{
        "broker_id": "paper",
        "display_name": "Paper",
        "environment": "paper",
        "health": "CONNECTED",
        "status_raw": "CONNECTED",
        "live_ready": false,
        "checks": {"connection": "READY", "funds": "READY"},
        "configured": true,
        "can_login": true,
        "can_disconnect": true,
        "can_refresh": true,
        "callback_url": "http://127.0.0.1:9474/vayren/callback",
        "capabilities": [
            {"id": "orders.market", "label": "SUPPORTED", "kind": 0},
            {"id": "orders.modify", "label": "NOT_SUPPORTED", "kind": 1}
        ],
        "blockers": []
    }"#;

    const UNCONFIGURED: &str = r#"{
        "broker_id": "",
        "display_name": "NOT CONFIGURED",
        "environment": "paper",
        "health": "UNKNOWN",
        "live_ready": false,
        "capabilities": [],
        "blockers": ["no broker selected"]
    }"#;

    fn make_view() -> *mut SystemView {
        let view = vayren_system_view_create(900, 640, 1.0);
        assert!(!view.is_null());
        view
    }

    fn snapshot(view: *mut SystemView, text: &str) {
        let c = std::ffi::CString::new(text).unwrap();
        assert_eq!(vayren_system_view_set_snapshot(view, c.as_ptr()), 0);
    }

    fn with_view<R, F: FnOnce(&mut SystemView) -> R>(view: *mut SystemView, f: F) -> R {
        // SAFETY: non-null handle from create, same (test) thread.
        f(unsafe { &mut *view })
    }

    #[test]
    fn lifecycle_renders_and_pops_varied_pixels() {
        let view = make_view();
        snapshot(view, CONNECTED);
        let mut buffer = vec![0x7Fu8; 900 * 640 * 3];
        assert_eq!(vayren_system_view_tick(view), 0);
        assert_eq!(
            vayren_system_view_render(view, buffer.as_mut_ptr(), buffer.len()),
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
            vayren_system_view_render(view, buffer.as_mut_ptr(), buffer.len()),
            0
        );
        vayren_system_view_destroy(view);
    }

    #[test]
    fn unconfigured_snapshot_stays_honest() {
        let view = make_view();
        snapshot(view, UNCONFIGURED);
        // The blocked CONNECTED verdict must not leak from prior state:
        // a fresh view shows exactly what the snapshot says.
        let panel = BrokerPanel::from_json(
            &serde_json::from_str::<serde_json::Value>(UNCONFIGURED).unwrap(),
        );
        assert!(!panel.connected());
        assert!(!panel.live_ready());
        assert_eq!(panel.health, vayren_shell::view_model::HealthState::Unknown);
        assert_eq!(panel.blockers, vec!["no broker selected".to_string()]);
        vayren_system_view_destroy(view);
    }

    #[test]
    fn parity_card_projects_real_facts() {
        let view = make_view();
        snapshot(view, CONNECTED);
        with_view(view, |v| {
            assert_eq!(v._ui.get_status_label(), "CONNECTED");
            assert_eq!(v._ui.get_conn_label(), "Healthy");
            assert_eq!(v._ui.get_health_summary(), "✓ HEALTHY");
            assert_eq!(v._ui.get_login_label(), "RE-AUTHENTICATE");
            assert!(v._ui.get_login_enabled());
            assert!(v._ui.get_refresh_visible());
            assert_eq!(v._ui.get_configure_label(), "SETTINGS");
            assert_eq!(
                v._ui.get_callback_display(),
                "127.0.0.1:9474/vayren/callback"
            );
            assert_eq!(v._ui.get_check_rows().row_count(), 6);
        });
        vayren_system_view_destroy(view);
    }

    #[test]
    fn ui_actions_drain_and_refresh_flag_roundtrips() {
        let view = make_view();
        snapshot(view, CONNECTED);
        assert_eq!(vayren_system_view_refresh_requested(view), 0);
        let mut buf = [0u8; 256];
        assert_eq!(
            vayren_system_view_next_event(view, buf.as_mut_ptr(), buf.len()),
            0
        );
        with_view(view, |v| {
            let weak = slint::ComponentHandle::as_weak(&v._ui);
            let ui = weak.upgrade().unwrap();
            ui.invoke_login_requested();
            ui.invoke_refresh_requested();
            ui.invoke_config_saved(
                slint::SharedString::from("KEY123"),
                slint::SharedString::from("SECRET456"),
            );
            ui.invoke_remove_requested();
            ui.invoke_copy_callback();
            drop(ui);
        });
        assert_eq!(vayren_system_view_refresh_requested(view), 1);
        let mut seen = Vec::new();
        loop {
            let len = vayren_system_view_next_event(view, buf.as_mut_ptr(), buf.len());
            if len <= 0 {
                break;
            }
            let parsed: serde_json::Value = serde_json::from_slice(&buf[..len as usize]).unwrap();
            seen.push(parsed["action"].as_str().unwrap().to_string());
        }
        assert_eq!(
            seen,
            vec!["login", "refresh", "configure", "remove", "copy_url"]
        );
        // The configure event carries the secret once, as the legacy contract did.
        with_view(view, |v| {
            v.actions.borrow_mut().clear();
            let weak = slint::ComponentHandle::as_weak(&v._ui);
            let ui = weak.upgrade().unwrap();
            ui.invoke_config_saved(
                slint::SharedString::from("K"),
                slint::SharedString::from("S"),
            );
            drop(ui);
        });
        let len = vayren_system_view_next_event(view, buf.as_mut_ptr(), buf.len());
        let text = String::from_utf8(buf[..len as usize].to_vec()).unwrap();
        assert!(text.contains("api_key"));
        assert!(text.contains("api_secret"));
        // A snapshot clears the SYNCING indicator.
        with_view(view, |v| {
            let weak = slint::ComponentHandle::as_weak(&v._ui);
            let ui = weak.upgrade().unwrap();
            ui.invoke_refresh_requested();
            drop(ui);
        });
        snapshot(view, CONNECTED);
        assert_eq!(vayren_system_view_ack_refresh(view), 0);
        assert_eq!(vayren_system_view_refresh_requested(view), 0);
        vayren_system_view_destroy(view);
    }

    #[test]
    fn connection_workspace_projects_broker_facts() {
        let view = make_view();
        snapshot(
            view,
            r#"{
                "brokers": [
                    {"id": "zerodha", "display_name": "Zerodha",
                     "venue_subtitle": "Kite Connect", "status": "CONNECTED"},
                    {"id": "fyers", "display_name": "Fyers",
                     "venue_subtitle": "FYERS API v3", "status": "LOGIN_REQUIRED"}
                ],
                "selected_id": "fyers",
                "display_name": "Fyers",
                "venue_subtitle": "FYERS API v3",
                "environment": "paper",
                "status_raw": "LOGIN_REQUIRED",
                "configured": true,
                "can_login": true,
                "can_disconnect": false,
                "reason": "",
                "credential_fields": [
                    {"key": "app_id", "label": "App ID",
                     "placeholder": "Enter FYERS App ID",
                     "secret": false, "required": true},
                    {"key": "secret", "label": "Secret ID",
                     "placeholder": "Enter FYERS Secret ID",
                     "secret": true, "required": true}
                ],
                "blockers": []
            }"#,
        );
        with_view(view, |v| {
            // Sidebar: both venues, FYERS selected, Zerodha connected.
            assert_eq!(v._ui.get_conn_brokers().row_count(), 2);
            assert_eq!(v._ui.get_conn_display_name(), "Fyers");
            assert_eq!(v._ui.get_conn_venue_subtitle(), "FYERS API v3");
            assert_eq!(v._ui.get_conn_env_label(), "PAPER");
            // Status is explicit text (never color alone).
            assert_eq!(v._ui.get_conn_pill_label(), "Not Connected");
            assert_eq!(v._ui.get_conn_cta_label(), "Connect to Fyers");
            assert!(v._ui.get_conn_cta_enabled());
            assert!(v._ui.get_conn_form_enabled());
            assert!(!v._ui.get_conn_is_connected());
            assert!(!v._ui.get_conn_is_failed());
            assert_eq!(v._ui.get_conn_fields().row_count(), 2);
            assert_eq!(v._ui.get_conn_progress().row_count(), 5);
            // Field keys captured for the connect drain.
            assert_eq!(
                *v.field_keys.borrow(),
                vec!["app_id".to_string(), "secret".to_string()]
            );
        });
        vayren_system_view_destroy(view);
    }

    #[test]
    fn connection_actions_drain_with_schema_keys() {
        let view = make_view();
        snapshot(
            view,
            r#"{
                "brokers": [{"id": "fyers", "display_name": "Fyers",
                             "venue_subtitle": "FYERS API v3",
                             "status": "LOGIN_REQUIRED", "selected": true}],
                "selected_id": "fyers",
                "display_name": "Fyers",
                "venue_subtitle": "FYERS API v3",
                "environment": "paper",
                "status_raw": "LOGIN_REQUIRED",
                "configured": false,
                "can_login": false,
                "credential_fields": [
                    {"key": "app_id", "label": "App ID",
                     "placeholder": "Enter App ID",
                     "secret": false, "required": true},
                    {"key": "secret", "label": "Secret",
                     "placeholder": "Enter Secret",
                     "secret": true, "required": true}
                ],
                "blockers": []
            }"#,
        );
        with_view(view, |v| {
            let weak = slint::ComponentHandle::as_weak(&v._ui);
            let ui = weak.upgrade().unwrap();
            ui.invoke_select_requested(slint::SharedString::from("zerodha"));
            ui.invoke_connect_requested(
                slint::SharedString::from("MYAPP"),
                slint::SharedString::from("S3CR3T"),
                slint::SharedString::from(""),
                slint::SharedString::from(""),
                slint::SharedString::from(""),
                slint::SharedString::from(""),
            );
            ui.invoke_help_requested();
            ui.invoke_add_requested();
            drop(ui);
        });
        let mut buf = [0u8; 1024];
        let mut seen = Vec::new();
        loop {
            let len = vayren_system_view_next_event(view, buf.as_mut_ptr(), buf.len());
            if len <= 0 {
                break;
            }
            let parsed: serde_json::Value = serde_json::from_slice(&buf[..len as usize]).unwrap();
            seen.push(parsed.clone());
        }
        assert_eq!(seen.len(), 4);
        assert_eq!(seen[0]["action"], "select");
        assert_eq!(seen[0]["broker_id"], "zerodha");
        assert_eq!(seen[1]["action"], "connect");
        // Values zip back onto schema keys (never bare positional strings).
        assert_eq!(seen[1]["values"]["app_id"], "MYAPP");
        assert_eq!(seen[1]["values"]["secret"], "S3CR3T");
        assert_eq!(seen[2]["action"], "help");
        assert_eq!(seen[3]["action"], "add");
        vayren_system_view_destroy(view);
    }

    #[test]
    fn rejects_bad_input_fail_closed() {
        assert_eq!(
            vayren_system_view_render(std::ptr::null_mut(), std::ptr::null_mut(), 0),
            -1
        );
        let view = make_view();
        let bad = std::ffi::CString::new("{nope").unwrap();
        assert_eq!(vayren_system_view_set_snapshot(view, bad.as_ptr()), -4);
        assert_eq!(vayren_system_view_set_snapshot(view, std::ptr::null()), -2);
        assert_eq!(vayren_system_view_resize(view, 0, 600, 1.0), -5);
        assert_eq!(vayren_system_view_pointer_press(view, 1.0, 1.0, 9), -5);
        let mut tiny = vec![0u8; 8];
        assert_eq!(
            vayren_system_view_render(view, tiny.as_mut_ptr(), tiny.len()),
            -5
        );
        vayren_system_view_destroy(view);
        vayren_system_view_destroy(std::ptr::null_mut());
    }
}
