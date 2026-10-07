//! VAYREN native UI shell (Rust + Slint). The view-model and shell state are
//! pure and headless-tested; the binary in `main.rs` binds them to the Slint
//! component defined in `ui/app.slint`.

pub use vayren_domain::broker_connection;
pub use vayren_domain::lab;
pub use vayren_domain::live;
pub use vayren_domain::market;
pub use vayren_domain::market_download;
pub use vayren_domain::portfolio;
pub use vayren_domain::research_state;
pub use vayren_domain::view_model;
pub use vayren_domain::viewport;

pub mod chart_demo;
#[cfg(test)]
pub mod perf;
#[cfg(test)]
pub mod perf_table;
#[cfg(test)]
pub mod perf_trades;
pub mod python_bridge;
pub mod remote_live;
pub mod remote_source;
pub mod shell;

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

pub use broker_connection::{BrokerWorkspace, ConnectionState};
pub use view_model::{BrokerPanel, CapabilityRow, CapabilityStatus, Environment, HealthState};
