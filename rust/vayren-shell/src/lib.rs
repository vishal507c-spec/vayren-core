//! VAYREN native UI shell (Rust + Slint). The view-model and shell state are
//! pure and headless-tested; the binary in `main.rs` binds them to the Slint
//! component defined in `ui/app.slint`.

pub mod chart_demo;
pub mod lab;
pub mod live;
pub mod market;
pub mod market_download;
pub mod portfolio;
pub mod research_state;
pub mod shell;
pub mod view_model;
pub mod viewport;

slint::include_modules!();

pub use lab::{LabState, RunState};
pub use live::LiveState;
pub use portfolio::PortfolioState;
pub use research_state::{ResearchRun, ResearchState};

/// Headless responsive harness (separate Slint compilation unit so the
/// harness Window gets Rust bindings). Used by tests/render_matrix.rs for
/// pixel renders; never instantiated in production.
pub mod research_harness_ui {
    #![allow(dead_code)]
    include!(concat!(env!("OUT_DIR"), "/research_harness.rs"));
}

/// Headless LIVE responsive harness (separate Slint compilation unit, tests
/// only — never instantiated in production).
#[cfg(test)]
pub mod live_harness_ui {
    #![allow(dead_code)]
    include!(concat!(env!("OUT_DIR"), "/live_harness.rs"));
}

pub use view_model::{BrokerPanel, CapabilityRow, CapabilityStatus, Environment, HealthState};
