//! VAYREN native UI binary — Rust + Slint application shell.
//!
//! The shell owns navigation centrally (Rust state) and binds the broker
//! panel strictly to the `BrokerPanel` view-model: connection, capability
//! and LIVE-readiness text all come from backend facts, never from UI
//! inference. Run with `cargo run -p vayren-shell`.

use slint::ComponentHandle;
use vayren_shell::shell;
use vayren_shell::ShellScreen;

fn main() -> Result<(), slint::PlatformError> {
    let ui = vayren_shell::AppWindow::new()?;
    shell::wire(&ui);
    shell::apply(&ui, &shell::demo_snapshot());
    shell::select(&ui, ShellScreen::Broker);
    ui.run()
}
