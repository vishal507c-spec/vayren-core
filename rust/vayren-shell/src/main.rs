//! VAYREN native UI binary — Rust + Slint application shell.
//!
//! The shell owns navigation centrally (Rust state) and binds the broker
//! panel strictly to the `BrokerPanel` view-model: connection, capability
//! and LIVE-readiness text all come from backend facts, never from UI
//! inference. Run with `cargo run -p vayren-shell`.

use slint::ComponentHandle;
use std::cell::RefCell;
use std::rc::Rc;
use vayren_shell::shell;
use vayren_shell::ShellScreen;

fn main() -> Result<(), slint::PlatformError> {
    let ui = vayren_shell::AppWindow::new()?;
    let zoom = Rc::new(RefCell::new(
        vayren_shell::viewport::ChartViewportZoom::default(),
    ));
    shell::wire(&ui);
    shell::wire_zoom(&ui, zoom.clone());
    shell::apply(&ui, &shell::demo_snapshot());
    shell::apply_zoom(&ui, &zoom.borrow());
    shell::select(&ui, ShellScreen::Broker);
    ui.run()
}
