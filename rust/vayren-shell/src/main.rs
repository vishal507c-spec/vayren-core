//! VAYREN native UI binary — renders the broker/UBL status panel with egui.
//!
//! The renderer binds strictly to the `BrokerPanel` view-model: connection,
//! capability and LIVE-readiness text all come from backend facts, never from
//! UI inference. Run with `cargo run -p vayren-shell`.

use eframe::egui;
use vayren_shell::view_model::{
    BrokerPanel, CapabilityRow, CapabilityStatus, Environment, HealthState,
};

struct ShellApp {
    panel: BrokerPanel,
}

impl ShellApp {
    fn demo() -> Self {
        // A representative backend-fed snapshot (paper venue, healthy, gates
        // not green). Real wiring replaces this with the live BrokerPanel.
        Self {
            panel: BrokerPanel {
                broker_id: "paper".into(),
                display_name: "Paper".into(),
                environment: Environment::Paper,
                capabilities: vec![
                    CapabilityRow {
                        id: "orders.market".into(),
                        status: CapabilityStatus::Supported,
                    },
                    CapabilityRow {
                        id: "account.funds".into(),
                        status: CapabilityStatus::Supported,
                    },
                    CapabilityRow {
                        id: "orders.modify".into(),
                        status: CapabilityStatus::NotSupported,
                    },
                ],
                health: HealthState::Connected,
                live_gates_ready: false,
                blockers: vec!["LIVE broker is not configured".into()],
            },
        }
    }
}

impl eframe::App for ShellApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        egui::CentralPanel::default().show(ctx, |ui| {
            ui.heading(&self.panel.display_name);
            ui.label(format!("Environment: {}", self.panel.environment.label()));
            let health = self.panel.health.label();
            let connected = if self.panel.connected() {
                " (connected)"
            } else {
                ""
            };
            ui.label(format!("Health: {health}{connected}"));
            let ready = if self.panel.live_ready() {
                "READY"
            } else {
                "NOT READY"
            };
            ui.colored_label(
                if self.panel.live_ready() {
                    egui::Color32::GREEN
                } else {
                    egui::Color32::LIGHT_RED
                },
                format!("LIVE: {ready}"),
            );
            ui.separator();
            ui.label("Capabilities");
            for row in &self.panel.capabilities {
                ui.label(format!("  {} — {}", row.id, row.status.label()));
            }
            if !self.panel.blockers.is_empty() {
                ui.separator();
                ui.label("Blockers");
                for blocker in &self.panel.blockers {
                    ui.colored_label(egui::Color32::LIGHT_YELLOW, format!("  {blocker}"));
                }
            }
        });
    }
}

fn main() -> eframe::Result {
    let options = eframe::NativeOptions::default();
    eframe::run_native(
        "VAYREN — Broker",
        options,
        Box::new(|_cc| Ok(Box::new(ShellApp::demo()))),
    )
}
