//! VAYREN native UI shell (Rust + Slint). The view-model and shell state are
//! pure and headless-tested; the binary in `main.rs` binds them to the Slint
//! component defined in `ui/app.slint`.

pub mod shell;
pub mod view_model;
pub mod viewport;

slint::include_modules!();

pub use view_model::{BrokerPanel, CapabilityRow, CapabilityStatus, Environment, HealthState};
