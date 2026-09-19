//! VAYREN native UI binary — Rust + Slint application shell.
//!
//! The shell owns navigation centrally (Rust state) and binds the broker
//! panel strictly to the `BrokerPanel` view-model: connection, capability
//! and LIVE-readiness text all come from backend facts, never from UI
//! inference. Run with `cargo run -p vayren-shell`.

use slint::ComponentHandle;
use std::cell::RefCell;
use std::rc::Rc;
use vayren_shell::research_state;
use vayren_shell::shell;

fn main() -> Result<(), slint::PlatformError> {
    let ui = vayren_shell::AppWindow::new()?;
    let zoom = Rc::new(RefCell::new(
        vayren_shell::viewport::ChartViewportZoom::default(),
    ));
    let lab_state = Rc::new(RefCell::new(
        // VISUAL-CHECK fixtures are reachable only via the explicit CLI flag
        // and are watermarked in the UI; the normal path never shows results
        // without the engine bridge.
        match std::env::args()
            .find_map(|a| a.strip_prefix("--visual-check=").map(str::to_owned))
            .as_deref()
        {
            Some(kind) => {
                let fixture = shell::visual_fixture(kind);
                ui.set_visual_check_label(
                    format!("VISUAL CHECK — DEMO DATA ({kind}) — NOT REAL RESULTS").into(),
                );
                fixture
            }
            None => shell::demo_lab_state(),
        },
    ));
    let portfolio_state = Rc::new(RefCell::new(shell::demo_portfolio_state()));
    // Research starts from the honest empty state; when VAYREN_RESEARCH_SNAPSHOT
    // points at engine output, the executed bundle presents instead — still
    // read-only engine facts, never UI inference.
    let research_state = Rc::new(RefCell::new(research_state::demo_research_state()));
    if let Some(bundle) = research_state::load_snapshot_from_env() {
        research_state::apply_snapshot(&mut research_state.borrow_mut(), bundle);
    }
    // LIVE starts from the honest static readiness facts of this workstation
    // (`--check-live` shape): no venue adapter, PAPER default, empty
    // execution tables. The bridge replaces the snapshot, never the UI.
    let live_state = Rc::new(RefCell::new(shell::demo_live_state()));
    shell::wire(&ui);
    shell::wire_zoom(&ui, zoom.clone());
    shell::wire_lab(&ui, lab_state.clone());
    shell::wire_portfolio(&ui, portfolio_state.clone());
    shell::wire_research(&ui, research_state.clone());
    shell::wire_live(&ui, live_state.clone());
    shell::apply(&ui, &shell::demo_snapshot());
    shell::apply_connection(&ui, &shell::demo_connection_workspace());
    shell::apply_zoom(&ui, &zoom.borrow());
    shell::apply_lab(&ui, &lab_state.borrow());
    shell::apply_portfolio(&ui, &portfolio_state.borrow());
    shell::apply_research(&ui, &research_state.borrow());
    shell::apply_live(&ui, &live_state.borrow());
    // Cross-runtime entry point: `--screen <name>` (e.g. from the Qt nav
    // bridge) preselects that screen; default stays Lab.
    let initial = shell::initial_screen(&std::env::args().skip(1).collect::<Vec<_>>());
    shell::select(&ui, initial);
    ui.run()
}
