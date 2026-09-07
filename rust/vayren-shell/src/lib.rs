//! VAYREN native UI shell (Rust + egui). The view-model is pure and
//! headless-tested; the binary in `main.rs` binds it to egui widgets.

pub mod view_model;

pub use view_model::{BrokerPanel, CapabilityRow, CapabilityStatus, Environment, HealthState};
