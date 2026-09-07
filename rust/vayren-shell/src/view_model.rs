//! Broker panel view-model — pure, headless-testable native UI state
//! (constitution §3: Rust+egui owns UI state/interaction logic).
//!
//! The model is a read-only projection of backend facts. It NEVER invents
//! CONNECTED / AUTHENTICATED / LIVE-READY: connection is derived only from a
//! reported health state, and live readiness only from an explicit gate
//! verdict. This is the authoritative native-UI representation the egui
//! renderer binds to.

use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CapabilityStatus {
    Supported,
    NotSupported,
    NotConfigured,
}

impl CapabilityStatus {
    pub fn label(self) -> &'static str {
        match self {
            CapabilityStatus::Supported => "SUPPORTED",
            CapabilityStatus::NotSupported => "NOT_SUPPORTED",
            CapabilityStatus::NotConfigured => "NOT_CONFIGURED",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HealthState {
    Disconnected,
    Connecting,
    Connected,
    Degraded,
    AuthRequired,
    AuthFailed,
    RateLimited,
    Unknown,
}

impl HealthState {
    pub fn label(self) -> &'static str {
        match self {
            HealthState::Disconnected => "DISCONNECTED",
            HealthState::Connecting => "CONNECTING",
            HealthState::Connected => "CONNECTED",
            HealthState::Degraded => "DEGRADED",
            HealthState::AuthRequired => "AUTH_REQUIRED",
            HealthState::AuthFailed => "AUTH_FAILED",
            HealthState::RateLimited => "RATE_LIMITED",
            HealthState::Unknown => "UNKNOWN",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Environment {
    Paper,
    Sandbox,
    Live,
}

impl Environment {
    pub fn label(self) -> &'static str {
        match self {
            Environment::Paper => "PAPER",
            Environment::Sandbox => "SANDBOX",
            Environment::Live => "LIVE",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CapabilityRow {
    pub id: String,
    pub status: CapabilityStatus,
}

/// Immutable snapshot of the broker panel, fed by the backend.
#[derive(Debug, Clone, PartialEq)]
pub struct BrokerPanel {
    pub broker_id: String,
    pub display_name: String,
    pub environment: Environment,
    pub capabilities: Vec<CapabilityRow>,
    pub health: HealthState,
    /// Explicit backend gate verdict — the ONLY source of LIVE readiness.
    pub live_gates_ready: bool,
    pub blockers: Vec<String>,
}

impl BrokerPanel {
    /// Connection is derived from health only — never assumed.
    pub fn connected(&self) -> bool {
        self.health == HealthState::Connected
    }

    /// LIVE readiness requires BOTH the gate verdict and the LIVE environment;
    /// a healthy paper/sandbox session is never "live ready".
    pub fn live_ready(&self) -> bool {
        self.environment == Environment::Live && self.live_gates_ready
    }

    /// Deterministic text projection (used by the renderer and by tests).
    pub fn summary_lines(&self) -> Vec<String> {
        let mut lines = vec![
            format!("BROKER   {}", self.display_name),
            format!("ENV      {}", self.environment.label()),
            format!(
                "HEALTH   {}{}",
                self.health.label(),
                if self.connected() {
                    ""
                } else {
                    " (not connected)"
                }
            ),
            format!(
                "LIVE     {}",
                if self.live_ready() {
                    "READY"
                } else {
                    "NOT READY"
                }
            ),
        ];
        for row in &self.capabilities {
            lines.push(format!("CAP      {} {}", row.id, row.status.label()));
        }
        for blocker in &self.blockers {
            lines.push(format!("BLOCKER  {blocker}"));
        }
        lines
    }
}

impl fmt::Display for BrokerPanel {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.summary_lines().join("\n"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn panel(environment: Environment, health: HealthState, gates: bool) -> BrokerPanel {
        BrokerPanel {
            broker_id: "zerodha".into(),
            display_name: "Zerodha".into(),
            environment,
            capabilities: vec![
                CapabilityRow {
                    id: "historical_data.candles".into(),
                    status: CapabilityStatus::Supported,
                },
                CapabilityRow {
                    id: "orders.market".into(),
                    status: CapabilityStatus::NotSupported,
                },
            ],
            health,
            live_gates_ready: gates,
            blockers: if gates {
                vec![]
            } else {
                vec!["no live venue adapter".into()]
            },
        }
    }

    #[test]
    fn connection_only_from_health() {
        assert!(panel(Environment::Live, HealthState::Connected, true).connected());
        assert!(!panel(Environment::Live, HealthState::Degraded, true).connected());
        assert!(!panel(Environment::Paper, HealthState::Unknown, false).connected());
    }

    #[test]
    fn live_ready_requires_environment_and_gates() {
        assert!(panel(Environment::Live, HealthState::Connected, true).live_ready());
        // healthy + gates green but NOT live environment -> never live ready
        assert!(!panel(Environment::Sandbox, HealthState::Connected, true).live_ready());
        // live environment but gates failing -> not ready
        assert!(!panel(Environment::Live, HealthState::Connected, false).live_ready());
    }

    #[test]
    fn summary_is_deterministic_and_labels_honestly() {
        let lines = panel(Environment::Paper, HealthState::Disconnected, false).summary_lines();
        let blob = lines.join("\n");
        assert!(blob.contains("PAPER"));
        assert!(blob.contains("DISCONNECTED (not connected)"));
        assert!(blob.contains("LIVE     NOT READY"));
        assert!(blob.contains("historical_data.candles SUPPORTED"));
        assert!(blob.contains("orders.market NOT_SUPPORTED"));
        assert!(blob.contains("no live venue adapter"));
    }

    #[test]
    fn never_claims_ready_without_backend_verdict() {
        let p = panel(Environment::Live, HealthState::Connected, false);
        assert!(!p.live_ready());
        assert!(p.summary_lines().iter().any(|l| l == "LIVE     NOT READY"));
    }
}
