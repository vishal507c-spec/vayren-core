//! Shell state + projection logic — pure, headless-testable native UI state
//! (constitution §3: Rust owns UI state/interaction logic; Slint only
//! renders bound properties).
//!
//! The shell owns the active screen centrally (single state source, mission
//! §14). Workspace content projection binds the [`BrokerPanel`] view-model
//! to Slint properties: every displayed value comes from backend facts,
//! never from UI inference.

use crate::view_model::{BrokerPanel, CapabilityStatus, Environment};
use crate::{AppWindow, CapabilityRowView, ShellScreen};
use slint::ComponentHandle;
use slint::Model;

/// Title text for a navigation target (status bar + pending card).
pub fn screen_title(screen: ShellScreen) -> &'static str {
    match screen {
        ShellScreen::Chart => "CHART",
        ShellScreen::Lab => "STRATEGY LAB",
        ShellScreen::Research => "RESEARCH",
        ShellScreen::Portfolio => "PORTFOLIO",
        ShellScreen::Live => "LIVE",
        ShellScreen::Broker => "BROKER",
        ShellScreen::Data => "DATA",
    }
}

/// Only the broker panel has a native surface so far (UI migration order:
/// shell + navigation first; each workspace migrates in its own slice).
pub fn screen_pending(screen: ShellScreen) -> bool {
    !matches!(screen, ShellScreen::Broker)
}

/// Environment badge kind: 0 = paper, 1 = sandbox, 2 = live.
pub fn env_kind(environment: Environment) -> i32 {
    match environment {
        Environment::Paper => 0,
        Environment::Sandbox => 1,
        Environment::Live => 2,
    }
}

/// Capability status kind: 0 = supported, 1 = not supported, 2 = not configured.
fn status_kind(status: CapabilityStatus) -> i32 {
    match status {
        CapabilityStatus::Supported => 0,
        CapabilityStatus::NotSupported => 1,
        CapabilityStatus::NotConfigured => 2,
    }
}

/// Project the backend view-model into the Slint capability-row model.
pub fn capability_rows(panel: &BrokerPanel) -> Vec<CapabilityRowView> {
    panel
        .capabilities
        .iter()
        .map(|row| CapabilityRowView {
            id: row.id.clone().into(),
            label: row.status.label().into(),
            kind: status_kind(row.status),
        })
        .collect()
}

/// Bind the broker panel view-model to the UI properties.
pub fn apply(ui: &AppWindow, panel: &BrokerPanel) {
    ui.set_broker_name(panel.display_name.clone().into());
    ui.set_broker_id(panel.broker_id.clone().into());
    ui.set_env_kind(env_kind(panel.environment));
    ui.set_env_label(panel.environment.label().into());
    ui.set_health_label(panel.health.label().into());
    ui.set_health_connected(panel.connected());
    ui.set_live_ready(panel.live_ready());
    ui.set_capability_rows(std::rc::Rc::new(slint::VecModel::from(capability_rows(panel))).into());
    ui.set_blockers(
        std::rc::Rc::new(slint::VecModel::from(
            panel
                .blockers
                .iter()
                .map(|b| b.clone().into())
                .collect::<Vec<_>>(),
        ))
        .into(),
    );
}

/// Switch the active screen (single shell state source).
pub fn select(ui: &AppWindow, screen: ShellScreen) {
    ui.set_active_screen(screen);
    ui.set_screen_title(screen_title(screen).into());
    ui.set_screen_pending(screen_pending(screen));
}

/// Register the navigation handler (call once per window).
pub fn wire(ui: &AppWindow) {
    let handle = ui.as_weak();
    ui.on_nav_selected(move |screen| {
        if let Some(ui) = handle.upgrade() {
            select(&ui, screen);
        }
    });
}

/// Representative backend-fed snapshot (paper venue, healthy, gates not
/// green). Real wiring replaces this with the live BrokerPanel.
pub fn demo_snapshot() -> BrokerPanel {
    BrokerPanel {
        broker_id: "paper".into(),
        display_name: "Paper".into(),
        environment: Environment::Paper,
        capabilities: vec![
            crate::view_model::CapabilityRow {
                id: "orders.market".into(),
                status: CapabilityStatus::Supported,
            },
            crate::view_model::CapabilityRow {
                id: "account.funds".into(),
                status: CapabilityStatus::Supported,
            },
            crate::view_model::CapabilityRow {
                id: "orders.modify".into(),
                status: CapabilityStatus::NotSupported,
            },
        ],
        health: crate::view_model::HealthState::Connected,
        live_gates_ready: false,
        blockers: vec!["LIVE broker is not configured".into()],
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Once;

    fn init_backend() {
        static ONCE: Once = Once::new();
        ONCE.call_once(i_slint_backend_testing::init_no_event_loop);
    }

    fn row(model: &slint::ModelRc<CapabilityRowView>, index: usize) -> CapabilityRowView {
        model
            .row_data(index)
            .unwrap_or_else(|| panic!("missing row {index}"))
    }

    #[test]
    fn capability_projection_maps_status_kinds() {
        let panel = demo_snapshot();
        let rows = capability_rows(&panel);
        assert_eq!(rows.len(), 3);
        assert_eq!(rows[0].id, "orders.market");
        assert_eq!(rows[0].label, "SUPPORTED");
        assert_eq!(rows[0].kind, 0);
        assert_eq!(rows[2].label, "NOT_SUPPORTED");
        assert_eq!(rows[2].kind, 1);
    }

    #[test]
    fn ui_bindings_and_navigation() {
        // Slint's testing backend owns process-global platform/event-loop
        // state: all window-instantiation assertions run in this one test,
        // sequentially.
        init_backend();

        let ui = AppWindow::new().unwrap();
        apply(&ui, &demo_snapshot());
        assert_eq!(ui.get_broker_name(), "Paper");
        assert_eq!(ui.get_broker_id(), "paper");
        assert_eq!(ui.get_env_label(), "PAPER");
        assert_eq!(ui.get_env_kind(), 0);
        assert_eq!(ui.get_health_label(), "CONNECTED");
        assert!(ui.get_health_connected());
        // paper venue is never live-ready, regardless of gate state
        assert!(!ui.get_live_ready());
        let rows = ui.get_capability_rows();
        assert_eq!(rows.row_count(), 3);
        assert_eq!(row(&rows, 1).id, "account.funds");
        let blockers = ui.get_blockers();
        assert_eq!(blockers.row_count(), 1);
        assert_eq!(
            blockers.row_data(0).unwrap(),
            "LIVE broker is not configured"
        );

        select(&ui, ShellScreen::Broker);
        assert_eq!(ui.get_active_screen(), ShellScreen::Broker);
        assert_eq!(ui.get_screen_title(), "BROKER");
        assert!(!ui.get_screen_pending());
        select(&ui, ShellScreen::Lab);
        assert_eq!(ui.get_active_screen(), ShellScreen::Lab);
        assert_eq!(ui.get_screen_title(), "STRATEGY LAB");
        // non-migrated screens render their honest migration state
        assert!(ui.get_screen_pending());
        select(&ui, ShellScreen::Broker);
        assert!(!ui.get_screen_pending());

        wire(&ui);
        ui.invoke_nav_selected(ShellScreen::Research);
        assert_eq!(ui.get_active_screen(), ShellScreen::Research);
        assert_eq!(ui.get_screen_title(), "RESEARCH");
        assert!(ui.get_screen_pending());
        ui.invoke_nav_selected(ShellScreen::Broker);
        assert_eq!(ui.get_active_screen(), ShellScreen::Broker);
        assert!(!ui.get_screen_pending());
    }
}
