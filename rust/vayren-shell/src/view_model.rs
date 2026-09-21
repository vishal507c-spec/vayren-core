//! Broker panel view-model — pure, headless-testable native UI state
//! (AI_ENTRY.md §1: Rust+Slint owns UI state/interaction logic).
//!
//! The model is a read-only projection of backend facts. It NEVER invents
//! CONNECTED / AUTHENTICATED / LIVE-READY: connection is derived only from a
//! reported health state, and live readiness only from an explicit gate
//! verdict. This is the authoritative native-UI representation the Slint
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
    // ── parity facts (legacy BrokersWorkspace surface, backend-owned) ──
    /// Exact `BrokerStatus` string (button rules key on it, as in legacy).
    pub status_raw: String,
    /// Raw check id → value pairs (the 6-cell health grid source).
    pub checks_raw: Vec<(String, String)>,
    pub account_id: String,
    pub funds_available: Option<f64>,
    pub funds_used: Option<f64>,
    pub funds_total: Option<f64>,
    pub positions_open: Option<i64>,
    pub orders_open: Option<i64>,
    pub last_sync: String,
    /// Pre-masked key fragment from the backend (secrets never cross).
    pub api_key_masked: String,
    /// Saved configuration exists (controls visibility of management UI).
    pub configured: bool,
    /// Backend action affordances (legacy `can_login/can_disconnect/can_refresh`).
    pub can_login: bool,
    pub can_disconnect: bool,
    pub can_refresh: bool,
    pub callback_url: String,
}

impl Default for BrokerPanel {
    fn default() -> Self {
        Self {
            broker_id: String::new(),
            display_name: "NOT CONFIGURED".to_string(),
            environment: Environment::Paper,
            capabilities: Vec::new(),
            health: HealthState::Unknown,
            live_gates_ready: false,
            blockers: vec!["no broker selected".to_string()],
            status_raw: "NOT_CONFIGURED".to_string(),
            checks_raw: Vec::new(),
            account_id: String::new(),
            funds_available: None,
            funds_used: None,
            funds_total: None,
            positions_open: None,
            orders_open: None,
            last_sync: String::new(),
            api_key_masked: String::new(),
            configured: false,
            can_login: false,
            can_disconnect: false,
            can_refresh: false,
            callback_url: String::new(),
        }
    }
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

    /// Build from the legacy host's bridge JSON (the `system_snapshot_dict`
    /// schema: single selected broker, flat facts). Defensive: missing or
    /// mistyped keys degrade to honest absence — never invented state.
    /// Unknown environment strings fall back to the PAPER default (the app
    /// invariant); unknown health strings stay UNKNOWN.
    pub fn from_json(v: &serde_json::Value) -> Self {
        let str_of = |key: &str| -> String {
            match v.get(key) {
                Some(serde_json::Value::String(s)) => s.clone(),
                Some(serde_json::Value::Number(n)) => n.to_string(),
                _ => String::new(),
            }
        };
        let environment = match str_of("environment").as_str() {
            "live" => Environment::Live,
            "sandbox" => Environment::Sandbox,
            _ => Environment::Paper,
        };
        let health = match str_of("health").as_str() {
            "DISCONNECTED" => HealthState::Disconnected,
            "CONNECTING" => HealthState::Connecting,
            "CONNECTED" => HealthState::Connected,
            "DEGRADED" => HealthState::Degraded,
            "AUTH_REQUIRED" => HealthState::AuthRequired,
            "AUTH_FAILED" => HealthState::AuthFailed,
            "RATE_LIMITED" => HealthState::RateLimited,
            _ => HealthState::Unknown,
        };
        let capabilities = v
            .get("capabilities")
            .and_then(|c| c.as_array())
            .map(|list| {
                list.iter()
                    .filter_map(|row| {
                        let id = match row.get("id") {
                            Some(serde_json::Value::String(s)) => s.clone(),
                            _ => return None,
                        };
                        // Display labels ride alongside only for
                        // debuggability; the view-model owns the status
                        // vocabulary (see CapabilityStatus).
                        let kind = row.get("kind").and_then(|k| k.as_i64()).unwrap_or(1);
                        let status = match kind {
                            0 => CapabilityStatus::Supported,
                            2 => CapabilityStatus::NotConfigured,
                            _ => CapabilityStatus::NotSupported,
                        };
                        Some(CapabilityRow { id, status })
                    })
                    .collect()
            })
            .unwrap_or_default();
        let blockers = v
            .get("blockers")
            .and_then(|b| b.as_array())
            .map(|list| {
                list.iter()
                    .filter_map(|b| b.as_str().map(str::to_string))
                    .collect()
            })
            .unwrap_or_default();
        BrokerPanel {
            broker_id: str_of("broker_id"),
            display_name: str_of("display_name"),
            environment,
            capabilities,
            health,
            live_gates_ready: v
                .get("live_ready")
                .and_then(|l| l.as_bool())
                .unwrap_or(false),
            blockers,
            status_raw: str_of("status_raw"),
            checks_raw: v
                .get("checks")
                .and_then(|c| c.as_object())
                .map(|map| {
                    let mut keys: Vec<&String> = map.keys().collect();
                    keys.sort();
                    keys.into_iter()
                        .map(|k| (k.clone(), value_text(&map[k])))
                        .collect()
                })
                .unwrap_or_default(),
            account_id: str_of("account_id"),
            funds_available: v.get("funds_available").and_then(|x| x.as_f64()),
            funds_used: v.get("funds_used").and_then(|x| x.as_f64()),
            funds_total: v.get("funds_total").and_then(|x| x.as_f64()),
            positions_open: v.get("positions_open").and_then(|x| x.as_i64()),
            orders_open: v.get("orders_open").and_then(|x| x.as_i64()),
            last_sync: str_of("last_sync"),
            api_key_masked: str_of("api_key_masked"),
            configured: v
                .get("configured")
                .and_then(|x| x.as_bool())
                .unwrap_or(false),
            can_login: v
                .get("can_login")
                .and_then(|x| x.as_bool())
                .unwrap_or(false),
            can_disconnect: v
                .get("can_disconnect")
                .and_then(|x| x.as_bool())
                .unwrap_or(false),
            can_refresh: v
                .get("can_refresh")
                .and_then(|x| x.as_bool())
                .unwrap_or(false),
            callback_url: str_of("callback_url"),
        }
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

// ── parity projection (legacy BrokersWorkspace card rules, display only) ───────
// Mirrors `_STATUS_META`, `_CHECK_ROWS`, `render_card` button/state rules
// and `_format_inr` exactly. No backend logic: every branch reads backend
// facts. Shared badge tones: 0 muted, 1 ok, 2 warn, 3 bad.

/// Fixed capability grid rows (legacy `_CHECK_ROWS` order).
pub const CHECK_ROWS: [(&str, &str); 6] = [
    ("connection", "CONNECTION"),
    ("account", "ACCOUNT"),
    ("funds", "FUNDS"),
    ("positions", "POSITIONS"),
    ("orders", "ORDERS"),
    ("market_data", "MARKET DATA"),
];

/// (glyph, label, explanation, tone) per exact backend status string.
fn status_meta(status: &str) -> (String, String, String, i32) {
    let (glyph, label, explanation, tone) = match status {
        "NOT_CONFIGURED" => (
            "○",
            "NOT CONFIGURED",
            "Connect a broker to enable trading and market data.",
            0,
        ),
        "CONFIGURING" => ("◐", "CONFIGURING", "Saving configuration…", 2),
        "LOGIN_REQUIRED" => (
            "○",
            "LOGIN REQUIRED",
            "Session expired — log in to reconnect.",
            2,
        ),
        "AUTHENTICATING" => (
            "◐",
            "CONNECTING",
            "Waiting for the broker login in your browser…",
            2,
        ),
        "CONNECTED" | "LIVE_READY" => ("●", "CONNECTED", "Broker connection is healthy.", 1),
        "DISCONNECTED" => (
            "○",
            "DISCONNECTED",
            "Connection lost — reconnect to resume trading.",
            3,
        ),
        "ERROR" => (
            "⚠",
            "ERROR",
            "Connection failed — check details and retry.",
            3,
        ),
        "ACCOUNT_NOT_READY" | "MARKET_DATA_NOT_READY" | "EXECUTION_NOT_READY" => (
            "◐",
            "DEGRADED",
            "Connected with limited capabilities — see health below.",
            2,
        ),
        _ => ("○", status, "Status unavailable.", 0),
    };
    (
        glyph.to_string(),
        label.to_string(),
        explanation.to_string(),
        tone,
    )
}

fn is_connected_set(status: &str) -> bool {
    matches!(status, "CONNECTED" | "LIVE_READY")
}

fn is_degraded_set(status: &str) -> bool {
    matches!(
        status,
        "ACCOUNT_NOT_READY" | "MARKET_DATA_NOT_READY" | "EXECUTION_NOT_READY"
    )
}

fn is_working(status: &str) -> bool {
    matches!(status, "AUTHENTICATING" | "CONFIGURING")
}

/// Honest scalar rendering for bridge values (mirrors ui_kit.text).
fn value_text(v: &serde_json::Value) -> String {
    match v {
        serde_json::Value::Null => String::new(),
        serde_json::Value::Bool(b) => {
            if *b {
                "YES".to_string()
            } else {
                "NO".to_string()
            }
        }
        serde_json::Value::Number(n) => n.to_string(),
        serde_json::Value::String(s) => s.clone(),
        serde_json::Value::Array(_) | serde_json::Value::Object(_) => String::new(),
    }
}

/// Compact rupee rendering (legacy `_format_inr`): grouped, 0dp, never zero.
fn inr(value: f64) -> String {
    let negative = value < 0.0;
    let digits = format!("{:.0}", value.abs());
    let mut grouped = String::new();
    for (i, ch) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i) % 3 == 0 {
            grouped.push(',');
        }
        grouped.push(ch);
    }
    format!("{}₹{}", if negative { "-" } else { "" }, grouped)
}

/// All display-ready card facts (labels, tones, button states).
#[derive(Debug, Clone, PartialEq)]
pub struct BrokerCardView {
    pub status_glyph: String,
    pub status_label: String,
    pub status_tone: i32,
    pub status_explanation: String,
    pub reason_line: String,
    pub account_id: String,
    pub conn_label: String,
    pub conn_tone: i32,
    pub last_sync: String,
    pub health_summary: String,
    pub health_summary_tone: i32,
    pub check_rows: Vec<CheckCell>,
    pub funds_line: String,
    pub funds_sub: String,
    pub positions_line: String,
    pub positions_sub: String,
    pub orders_line: String,
    pub orders_sub: String,
    pub credentials_line: String,
    pub callback_display: String,
    pub refresh_visible: bool,
    pub refresh_label: String,
    pub refresh_enabled: bool,
    pub refresh_primary: bool,
    pub login_label: String,
    pub login_enabled: bool,
    pub login_primary: bool,
    pub configure_label: String,
    pub disconnect_visible: bool,
    pub disconnect_enabled: bool,
    pub remove_visible: bool,
    pub working: bool,
}

/// One fixed health-grid cell.
#[derive(Debug, Clone, PartialEq)]
pub struct CheckCell {
    pub key: String,
    pub label: String,
    pub value: String,
    pub tone: i32,
}

impl BrokerPanel {
    /// Project backend facts onto display strings + control states.
    /// `refresh_busy` is the transient SYNCING feedback (set on the refresh
    /// intent, cleared by the next applied snapshot — same contract as legacy).
    pub fn project_card(&self, refresh_busy: bool) -> BrokerCardView {
        let status = self.status_raw.as_str();
        let (glyph, label, explanation, tone) = status_meta(status);
        let reason = self
            .blockers
            .iter()
            .find(|b| !b.trim().is_empty())
            .cloned()
            .unwrap_or_default();
        let reason_line = if status == "NOT_CONFIGURED"
            || reason.is_empty()
            || reason.to_lowercase().contains(&explanation.to_lowercase())
        {
            explanation.to_string()
        } else {
            format!("{explanation} {reason}")
        };
        let working = is_working(status);
        let connected_set = is_connected_set(status);
        let degraded = is_degraded_set(status);

        let checks: std::collections::HashMap<&str, &str> = self
            .checks_raw
            .iter()
            .map(|(k, v)| (k.as_str(), v.as_str()))
            .collect();
        let (conn_label, conn_tone) = if connected_set {
            let failed: Vec<&&str> = checks
                .values()
                .filter(|v| !v.is_empty() && **v != "READY")
                .collect();
            if failed.is_empty() {
                ("Healthy".to_string(), 1)
            } else {
                ("Degraded".to_string(), 2)
            }
        } else if degraded {
            ("Degraded".to_string(), 2)
        } else if working {
            ("Working…".to_string(), 2)
        } else if matches!(status, "DISCONNECTED" | "ERROR") {
            ("Down".to_string(), 3)
        } else {
            ("Not connected".to_string(), 0)
        };

        let values: Vec<&str> = CHECK_ROWS
            .iter()
            .map(|(key, _)| checks.get(key).copied().unwrap_or(""))
            .collect();
        let health_summary = if status == "NOT_CONFIGURED" {
            ("— NOT CONNECTED".to_string(), 0)
        } else if !values.iter().any(|v| !v.is_empty()) {
            ("— NO DATA".to_string(), 0)
        } else if values.iter().all(|v| v.is_empty() || *v == "READY") {
            ("✓ HEALTHY".to_string(), 1)
        } else {
            ("⚠ PARTIAL".to_string(), 2)
        };
        let check_rows = CHECK_ROWS
            .iter()
            .map(|(key, title)| {
                let raw = checks.get(key).copied().unwrap_or("");
                let (value, tone) = if raw.is_empty() {
                    ("— Unavailable".to_string(), 0)
                } else if raw == "READY" {
                    ("✓ Ready".to_string(), 1)
                } else if raw.starts_with("FAILED") {
                    ("⚠ Check failed".to_string(), 3)
                } else {
                    (raw.to_string(), 0)
                };
                CheckCell {
                    key: (*key).to_string(),
                    label: (*title).to_string(),
                    value,
                    tone,
                }
            })
            .collect();

        let (funds_line, funds_sub) =
            match (self.funds_available, self.funds_used, self.funds_total) {
                (None, None, None) => ("—".to_string(), "Not available".to_string()),
                (available, used, total) => {
                    let line = available.map(inr).unwrap_or_else(|| "—".to_string());
                    let mut parts = Vec::new();
                    if let Some(u) = used {
                        parts.push(format!("Used {}", inr(u)));
                    }
                    if let Some(t) = total {
                        parts.push(format!("Total {}", inr(t)));
                    }
                    (line, parts.join(" · "))
                }
            };
        let (positions_line, positions_sub) = match self.positions_open {
            None => ("—".to_string(), "Not available".to_string()),
            Some(0) => ("0".to_string(), "No open positions".to_string()),
            Some(n) => (n.to_string(), format!("{n} open")),
        };
        let (orders_line, orders_sub) = match self.orders_open {
            None => ("—".to_string(), "No data".to_string()),
            Some(0) => ("0".to_string(), "No open orders".to_string()),
            Some(n) => (n.to_string(), format!("{n} open")),
        };
        let credentials_line = if self.configured && !self.api_key_masked.is_empty() {
            format!(
                "Credentials  ● Securely configured · {}",
                self.api_key_masked
            )
        } else if self.configured {
            "Credentials  ● Configured".to_string()
        } else {
            "Credentials  ○ Not configured".to_string()
        };
        let callback_display = self
            .callback_url
            .trim_start_matches("https://")
            .trim_start_matches("http://")
            .to_string();

        let name = self.display_name.to_uppercase();
        let (login_label, login_enabled, login_primary) = if status == "NOT_CONFIGURED" {
            (format!("CONNECT {name}"), true, true)
        } else if working {
            ("CONNECTING…".to_string(), false, false)
        } else if connected_set {
            ("RE-AUTHENTICATE".to_string(), self.can_login, false)
        } else if degraded {
            ("RECONNECT".to_string(), self.can_login, false)
        } else {
            (format!("CONNECT {name}"), self.can_login, true)
        };
        let (refresh_label, refresh_enabled) = if refresh_busy {
            ("SYNCING…".to_string(), false)
        } else {
            ("REFRESH".to_string(), self.can_refresh && !working)
        };
        BrokerCardView {
            status_glyph: glyph.to_string(),
            status_label: label.to_string(),
            status_tone: tone,
            status_explanation: explanation.to_string(),
            reason_line,
            account_id: if self.account_id.trim().is_empty() {
                "—".to_string()
            } else {
                self.account_id.clone()
            },
            conn_label,
            conn_tone,
            last_sync: if self.last_sync.trim().is_empty() {
                "—".to_string()
            } else {
                self.last_sync.clone()
            },
            health_summary: health_summary.0,
            health_summary_tone: health_summary.1,
            check_rows,
            funds_line,
            funds_sub,
            positions_line,
            positions_sub,
            orders_line,
            orders_sub,
            credentials_line,
            callback_display: if callback_display.is_empty() {
                "—".to_string()
            } else {
                callback_display
            },
            refresh_visible: self.configured,
            refresh_label,
            refresh_enabled,
            refresh_primary: connected_set || degraded,
            login_label,
            login_enabled,
            login_primary,
            configure_label: if self.configured {
                "SETTINGS".to_string()
            } else {
                "CONFIGURE".to_string()
            },
            disconnect_visible: self.configured,
            disconnect_enabled: self.can_disconnect,
            remove_visible: self.configured,
            working,
        }
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
            ..Default::default()
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

    fn card(status: &str) -> BrokerPanel {
        BrokerPanel {
            status_raw: status.to_string(),
            ..panel(Environment::Paper, HealthState::Unknown, false)
        }
    }

    #[test]
    fn card_status_meta_matches_legacy_vocabulary() {
        let view = card("CONNECTED").project_card(false);
        assert_eq!(view.status_glyph, "●");
        assert_eq!(view.status_label, "CONNECTED");
        assert_eq!(view.status_tone, 1);
        assert_eq!(view.conn_label, "Healthy");
        assert_eq!(view.conn_tone, 1);
        let view = card("LOGIN_REQUIRED").project_card(false);
        assert_eq!(view.status_label, "LOGIN REQUIRED");
        assert_eq!(view.status_tone, 2);
        assert_eq!(view.conn_label, "Not connected");
        let view = card("DISCONNECTED").project_card(false);
        assert_eq!(view.conn_label, "Down");
        assert_eq!(view.conn_tone, 3);
        let view = card("EXECUTION_NOT_READY").project_card(false);
        assert_eq!(view.status_label, "DEGRADED");
        assert_eq!(view.login_label, "RECONNECT");
        // Unknown backend strings never render ready-looking.
        let view = card("SOMETHING_NEW").project_card(false);
        assert_eq!(view.status_tone, 0);
        assert_eq!(view.conn_label, "Not connected");
    }

    #[test]
    fn card_button_rules_follow_backend_facts() {
        // Unconfigured: primary CONNECT + CONFIGURE, no management buttons.
        let view = card("NOT_CONFIGURED").project_card(false);
        assert_eq!(view.login_label, "CONNECT ZERODHA");
        assert!(view.login_enabled && view.login_primary);
        assert_eq!(view.configure_label, "CONFIGURE");
        assert!(!view.refresh_visible && !view.disconnect_visible && !view.remove_visible);
        // Working: inert CONNECTING.
        let view = card("AUTHENTICATING").project_card(false);
        assert_eq!(view.login_label, "CONNECTING…");
        assert!(!view.login_enabled);
        // Connected: re-auth gated by can_login, primary refresh.
        let mut connected = card("CONNECTED");
        connected.configured = true;
        connected.can_login = true;
        connected.can_disconnect = true;
        connected.can_refresh = true;
        let view = connected.project_card(false);
        assert_eq!(view.login_label, "RE-AUTHENTICATE");
        assert!(view.login_enabled && !view.login_primary);
        assert!(view.refresh_visible && view.refresh_enabled && view.refresh_primary);
        assert_eq!(view.configure_label, "SETTINGS");
        assert!(view.disconnect_visible && view.disconnect_enabled && view.remove_visible);
        // Busy refresh overrides the label and disables the button.
        let busy = connected.project_card(true);
        assert_eq!(busy.refresh_label, "SYNCING…");
        assert!(!busy.refresh_enabled);
    }

    #[test]
    fn card_metrics_and_credentials_render_honestly() {
        let mut p = card("CONNECTED");
        p.account_id = "AB1234".into();
        p.funds_available = Some(80000.0);
        p.funds_used = Some(20000.0);
        p.funds_total = Some(100000.0);
        p.positions_open = Some(0);
        p.orders_open = Some(3);
        p.last_sync = "10:00:01".into();
        p.api_key_masked = "AB**12".into();
        p.configured = true;
        let view = p.project_card(false);
        assert_eq!(view.account_id, "AB1234");
        assert_eq!(view.funds_line, "₹80,000");
        assert_eq!(view.funds_sub, "Used ₹20,000 · Total ₹100,000");
        assert_eq!(view.positions_sub, "No open positions");
        assert_eq!(view.orders_line, "3");
        assert!(view.credentials_line.contains("AB**12"));
        assert_eq!(view.last_sync, "10:00:01");
        // Missing numbers never zero-fill.
        let empty = card("NOT_CONFIGURED").project_card(false);
        assert_eq!(empty.funds_line, "—");
        assert_eq!(empty.positions_sub, "Not available");
        assert_eq!(empty.account_id, "—");
        assert!(empty.credentials_line.contains("Not configured"));
        assert_eq!(inr(1234567.0), "₹1,234,567");
    }

    #[test]
    fn card_health_summary_and_grid_follow_checks() {
        let mut p = card("CONNECTED");
        p.checks_raw = vec![
            ("connection".to_string(), "READY".to_string()),
            ("funds".to_string(), "FAILED: timeout".to_string()),
        ];
        let view = p.project_card(false);
        assert_eq!(view.health_summary, "⚠ PARTIAL");
        assert_eq!(view.check_rows.len(), 6);
        let conn = view
            .check_rows
            .iter()
            .find(|c| c.key == "connection")
            .unwrap();
        assert_eq!((conn.value.as_str(), conn.tone), ("✓ Ready", 1));
        let funds = view.check_rows.iter().find(|c| c.key == "funds").unwrap();
        assert_eq!((funds.value.as_str(), funds.tone), ("⚠ Check failed", 3));
        let missing = view.check_rows.iter().find(|c| c.key == "account").unwrap();
        assert_eq!((missing.value.as_str(), missing.tone), ("— Unavailable", 0));
        // Degraded connection display when a check fails while connected.
        assert_eq!(view.conn_label, "Degraded");
    }

    #[test]
    fn from_json_parses_parity_facts_defensively() {
        let panel = BrokerPanel::from_json(&serde_json::json!({
            "broker_id": "zerodha",
            "display_name": "Zerodha",
            "environment": "paper",
            "health": "CONNECTED",
            "status_raw": "CONNECTED",
            "checks": {"connection": "READY"},
            "account_id": "AB1",
            "configured": true,
            "can_login": false,
            "can_refresh": true,
            "callback_url": "https://127.0.0.1:9474/vayren/callback",
        }));
        assert_eq!(panel.status_raw, "CONNECTED");
        assert_eq!(panel.account_id, "AB1");
        assert!(panel.configured && panel.can_refresh && !panel.can_login);
        let view = panel.project_card(false);
        assert_eq!(view.callback_display, "127.0.0.1:9474/vayren/callback");
        assert_eq!(view.login_label, "RE-AUTHENTICATE");
        assert!(!view.login_enabled);
        // Mistyped sections degrade without crashing.
        let broken = BrokerPanel::from_json(&serde_json::json!({"checks": [1, 2]}));
        assert!(broken.checks_raw.is_empty());
        assert_eq!(broken.project_card(false).health_summary, "— NO DATA");
    }
}
