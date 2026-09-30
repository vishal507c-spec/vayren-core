//! VAYREN shared domain & view-model state.
//! Pure, headless-testable UI state (AI_ENTRY.md §1: Rust owns domain/view-model logic).
//! Decoupled from Slint rendering and the application shell.

pub mod broker_connection;
pub mod lab;
pub mod live;
pub mod market;
pub mod market_download;
pub mod portfolio;
pub mod research_state;
pub mod view_model;
pub mod viewport;

pub use broker_connection::{BrokerWorkspace, ConnectionState};
pub use lab::{LabState, RunState, Tone};
pub use live::LiveState;
pub use portfolio::PortfolioState;
pub use research_state::{ResearchRun, ResearchState};
pub use view_model::{
    env_kind, BrokerPanel, CapabilityRow, CapabilityStatus, Environment, HealthState,
};
pub use viewport::ChartViewportZoom;
