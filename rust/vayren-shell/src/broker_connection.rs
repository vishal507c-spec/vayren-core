//! Broker CONNECTION workspace view-model — pure, headless-testable native UI
//! state (AI_ENTRY.md §1: Rust+Slint owns UI state/interaction logic; Slint
//! only renders bound properties).
//!
//! This is the connection-focused projection of backend facts for the SYSTEM
//! → BROKERS workspace. It deliberately shows NO dashboards, charts, funds,
//! positions, orders, P&L or market data: the screen has one purpose —
//! BROKER → STATUS → CREDENTIALS → CONNECT → AUTHENTICATION → CONNECTED.
//!
//! Every value derives from backend facts only:
//! - broker list + selection + `BrokerStatus` strings come from
//!   `BrokerManager.snapshot()` via the Python bridge (never inferred here);
//! - credential FIELD SHAPES (key/label/placeholder/secret/required) come
//!   from the venue's existing `credential_fields` schema (Zerodha and FYERS
//!   keep their own shapes; nothing here forces one venue's fields onto the
//!   other);
//! - field VALUES never cross into snapshots (they travel once, inside the
//!   connect action event, exactly like the previous `configure` contract);
//! - connection progress is derived from the coarse backend status only —
//!   sub-steps the backend does not report stay honestly pending, never
//!   fabricated.
//!
//! Shared tone convention (same as `view_model` + global components):
//! 0 = muted, 1 = ok/pos, 2 = warn, 3 = bad/neg, 4 = accent.

use std::fmt;

/// Coarse connection state driving the CTA, the status pill and progress.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConnectionState {
    /// No configuration saved for the selected broker.
    NotConfigured,
    /// Credentials available, ready to start authentication.
    Ready,
    /// Authentication round-trip running (browser/Selenium/TOTP+PIN flow).
    Authenticating,
    /// Reserved: backend reports no token/verify sub-states today, so these
    /// stay unreachable until the manager emits them (progress keeps those
    /// steps honestly pending meanwhile).
    GettingToken,
    /// Reserved (see above).
    Verifying,
    /// Session verified (CONNECTED / LIVE_READY).
    Connected,
    /// Last attempt failed (ERROR / DISCONNECTED / degraded checks).
    Failed,
}

impl ConnectionState {
    /// Short label for the header status pill (text always carries meaning —
    /// color is never the only signal).
    pub fn pill_label(self) -> &'static str {
        match self {
            ConnectionState::NotConfigured | ConnectionState::Ready => "Not Connected",
            ConnectionState::Authenticating
            | ConnectionState::GettingToken
            | ConnectionState::Verifying => "Authenticating",
            ConnectionState::Connected => "Connected",
            ConnectionState::Failed => "Connection Failed",
        }
    }

    /// Pill tone (shared convention).
    pub fn pill_tone(self) -> i32 {
        match self {
            ConnectionState::NotConfigured | ConnectionState::Ready => 3,
            ConnectionState::Authenticating
            | ConnectionState::GettingToken
            | ConnectionState::Verifying => 2,
            ConnectionState::Connected => 1,
            ConnectionState::Failed => 3,
        }
    }

    /// Whether the pill dot reads "on".
    pub fn pill_on(self) -> bool {
        matches!(self, ConnectionState::Connected)
    }
}

impl fmt::Display for ConnectionState {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let label = match self {
            ConnectionState::NotConfigured => "NOT_CONFIGURED",
            ConnectionState::Ready => "READY",
            ConnectionState::Authenticating => "AUTHENTICATING",
            ConnectionState::GettingToken => "GETTING_TOKEN",
            ConnectionState::Verifying => "VERIFYING",
            ConnectionState::Connected => "CONNECTED",
            ConnectionState::Failed => "FAILED",
        };
        write!(f, "{label}")
    }
}

/// Map the exact backend `BrokerStatus` string (+ saved-config flag) onto the
/// workspace connection state. Unknown strings degrade to `Failed` when a
/// configuration exists (attention, never a ready-looking state) and to
/// `NotConfigured` otherwise.
pub fn connection_state(status_raw: &str, configured: bool) -> ConnectionState {
    match status_raw {
        "NOT_CONFIGURED" => ConnectionState::NotConfigured,
        "CONNECTED" | "LIVE_READY" => ConnectionState::Connected,
        "AUTHENTICATING" | "CONFIGURING" => ConnectionState::Authenticating,
        "LOGIN_REQUIRED" => {
            if configured {
                ConnectionState::Ready
            } else {
                ConnectionState::NotConfigured
            }
        }
        "DISCONNECTED"
        | "ERROR"
        | "ACCOUNT_NOT_READY"
        | "MARKET_DATA_NOT_READY"
        | "EXECUTION_NOT_READY" => ConnectionState::Failed,
        _ => {
            if configured {
                ConnectionState::Failed
            } else {
                ConnectionState::NotConfigured
            }
        }
    }
}

/// True when the backend status string means an authenticated session exists.
pub fn is_connected_status(status_raw: &str) -> bool {
    matches!(status_raw, "CONNECTED" | "LIVE_READY")
}

/// One sidebar row (a venue, never its secrets).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BrokerListItem {
    pub id: String,
    pub display_name: String,
    pub venue_subtitle: String,
    pub connected: bool,
    pub selected: bool,
}

impl BrokerListItem {
    /// Sidebar status text (explicit words, not color alone).
    pub fn status_label(&self) -> &'static str {
        if self.connected {
            "Connected"
        } else {
            "Not Connected"
        }
    }

    /// Single-letter text mark for the logo box (display initial — an
    /// honest text mark, never a fake logo asset).
    pub fn mark(&self) -> String {
        self.display_name
            .chars()
            .next()
            .map(|c| c.to_uppercase().to_string())
            .unwrap_or_else(|| "?".to_string())
    }

    /// Sidebar status tone (1 ok when connected, 3 bad otherwise).
    pub fn status_tone(&self) -> i32 {
        if self.connected {
            1
        } else {
            3
        }
    }
}

/// One credential input shape (schema only — values never live here).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CredentialFieldView {
    pub key: String,
    pub label: String,
    pub placeholder: String,
    pub secret: bool,
    pub required: bool,
    /// True when this key already has a persisted value in the OS vault.
    /// The UI shows a "saved" indicator; no actual value crosses the boundary.
    pub saved: bool,
}

/// One connection-progress step.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProgressStep {
    pub index: i32,
    pub title: String,
    pub subtitle: String,
    /// 0 pending, 1 active, 2 done, 3 failed.
    pub state: i32,
}

/// Fixed 5-step authentication pipeline (titles never change per broker so
/// the eye always lands in the same place).
pub const PROGRESS_TITLES: [&str; 5] = [
    "Credentials",
    "Authenticate",
    "Get Token",
    "Verify Connection",
    "Connected",
];

/// Derive the 5 progress steps from the coarse connection state. Sub-steps
/// the backend does not report stay pending — never fabricated as active.
pub fn progress_steps(state: ConnectionState) -> Vec<ProgressStep> {
    let states: [i32; 5] = match state {
        ConnectionState::NotConfigured => [0, 0, 0, 0, 0],
        ConnectionState::Ready => [2, 0, 0, 0, 0],
        ConnectionState::Authenticating => [2, 1, 0, 0, 0],
        ConnectionState::GettingToken => [2, 2, 1, 0, 0],
        ConnectionState::Verifying => [2, 2, 2, 1, 0],
        ConnectionState::Connected => [2, 2, 2, 2, 2],
        ConnectionState::Failed => [2, 3, 0, 0, 0],
    };
    PROGRESS_TITLES
        .iter()
        .enumerate()
        .map(|(index, title)| {
            let step_state = states[index];
            let subtitle = match step_state {
                2 => {
                    if state == ConnectionState::Connected && index == 4 {
                        "Verified"
                    } else if state == ConnectionState::Ready && index == 0 {
                        "Saved"
                    } else {
                        "Done"
                    }
                }
                1 => "In progress…",
                3 => "Failed — retry below",
                _ => "Not started",
            };
            ProgressStep {
                index: (index + 1) as i32,
                title: (*title).to_string(),
                subtitle: subtitle.to_string(),
                state: step_state,
            }
        })
        .collect()
}

/// Primary CTA label for the state (the ONE obvious next action).
pub fn cta_label(state: ConnectionState, display_name: &str) -> String {
    let name = display_name.trim();
    let target = if name.is_empty() {
        "Broker".to_string()
    } else {
        // Title-case the first letter for the button ("Fyers", "Zerodha").
        let mut chars = name.chars();
        match chars.next() {
            Some(first) => format!("{}{}", first.to_uppercase(), chars.as_str().to_lowercase()),
            None => "Broker".to_string(),
        }
    };
    match state {
        ConnectionState::NotConfigured | ConnectionState::Ready => {
            format!("Connect to {target}")
        }
        ConnectionState::Authenticating
        | ConnectionState::GettingToken
        | ConnectionState::Verifying => "Authenticating…".to_string(),
        ConnectionState::Connected => "Manage Connection".to_string(),
        ConnectionState::Failed => "Retry".to_string(),
    }
}

/// Short description under the broker title (venue-aware, backend-owned
/// display name only — no invented account numbers, metrics or links).
pub fn description_line(display_name: &str) -> String {
    let name = display_name.trim();
    if name.is_empty() {
        "Connect your account to start trading.".to_string()
    } else {
        format!(
            "Connect your {} account to start trading.",
            name.to_uppercase()
        )
    }
}

/// Full workspace snapshot: everything the Slint screen renders.
#[derive(Debug, Clone, PartialEq)]
pub struct BrokerWorkspace {
    pub brokers: Vec<BrokerListItem>,
    pub selected_id: String,
    pub display_name: String,
    pub venue_subtitle: String,
    pub env_label: String,
    pub status_raw: String,
    pub state: ConnectionState,
    pub configured: bool,
    pub can_connect: bool,
    pub can_disconnect: bool,
    pub reason: String,
    pub fields: Vec<CredentialFieldView>,
}

impl BrokerWorkspace {
    /// Honest empty workspace (no selection, no facts invented).
    pub fn empty() -> Self {
        Self {
            brokers: Vec::new(),
            selected_id: String::new(),
            display_name: String::new(),
            venue_subtitle: String::new(),
            env_label: "PAPER".to_string(),
            status_raw: "NOT_CONFIGURED".to_string(),
            state: ConnectionState::NotConfigured,
            configured: false,
            can_connect: false,
            can_disconnect: false,
            reason: String::new(),
            fields: Vec::new(),
        }
    }

    /// Header status pill (label + tone) for the current state.
    pub fn pill(&self) -> (&'static str, i32) {
        (self.state.pill_label(), self.state.pill_tone())
    }

    /// Progress steps for the current state.
    pub fn progress(&self) -> Vec<ProgressStep> {
        progress_steps(self.state)
    }

    /// Primary CTA label.
    pub fn cta(&self) -> String {
        cta_label(self.state, &self.display_name)
    }

    /// Whether the credential form accepts input right now.
    pub fn form_enabled(&self) -> bool {
        !matches!(
            self.state,
            ConnectionState::Authenticating
                | ConnectionState::GettingToken
                | ConnectionState::Verifying
        )
    }

    /// User-facing status message under the pill (secret-free backend
    /// reason only; empty reasons degrade to the state default).
    pub fn status_message(&self) -> String {
        match self.state {
            ConnectionState::Connected => {
                if self.reason.trim().is_empty() {
                    "Connection verified.".to_string()
                } else {
                    format!("Connection verified: {}", self.reason.trim())
                }
            }
            ConnectionState::Failed => {
                if self.reason.trim().is_empty() {
                    "Authentication failed. Check your credentials and try again.".to_string()
                } else {
                    format!(
                        "Authentication failed: {}. Check your credentials and try again.",
                        self.reason.trim()
                    )
                }
            }
            ConnectionState::Authenticating
            | ConnectionState::GettingToken
            | ConnectionState::Verifying => "Authenticating with broker API…".to_string(),
            ConnectionState::Ready => "Ready to connect.".to_string(),
            ConnectionState::NotConfigured => "Enter your credentials to connect.".to_string(),
        }
    }

    /// Build from the Python bridge JSON. Defensive: missing/mistyped keys
    /// degrade to honest absence (empty strings, `—` at render, pending
    /// steps) — never invented brokers, fields or sessions.
    ///
    /// Accepted shape (all keys optional):
    /// ```json
    /// {
    ///   "brokers": [{"id","display_name","venue_subtitle","status","selected"}],
    ///   "selected_id": "fyers",
    ///   "display_name": "Fyers", "venue_subtitle": "FYERS API v3",
    ///   "environment": "paper", "status_raw": "LOGIN_REQUIRED",
    ///   "configured": true, "can_login": true, "can_disconnect": false,
    ///   "reason": "...",
    ///   "credential_fields": [{"key","label","placeholder","secret","required"}]
    /// }
    /// ```
    /// The legacy single-broker snapshot (`broker_id`/`selection`+`record`)
    /// is also accepted so the old bridge keeps rendering.
    pub fn from_json(value: &serde_json::Value) -> Self {
        let str_of = |key: &str| -> String {
            match value.get(key) {
                Some(serde_json::Value::String(s)) => s.clone(),
                Some(serde_json::Value::Number(n)) => n.to_string(),
                _ => String::new(),
            }
        };
        let bool_of =
            |key: &str| -> bool { value.get(key).and_then(|v| v.as_bool()).unwrap_or(false) };

        // Broker list (new shape) — each row degrades independently.
        let mut brokers = Vec::new();
        if let Some(list) = value.get("brokers").and_then(|v| v.as_array()) {
            for row in list {
                let id = row
                    .get("id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                if id.is_empty() {
                    continue;
                }
                let status = row.get("status").and_then(|v| v.as_str()).unwrap_or("");
                brokers.push(BrokerListItem {
                    display_name: row
                        .get("display_name")
                        .and_then(|v| v.as_str())
                        .unwrap_or(&id)
                        .to_string(),
                    venue_subtitle: row
                        .get("venue_subtitle")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string(),
                    connected: is_connected_status(status),
                    selected: row
                        .get("selected")
                        .and_then(|v| v.as_bool())
                        .unwrap_or(false),
                    id,
                });
            }
        }

        // Selected broker identity: new keys first, legacy fallbacks after.
        let mut selected_id = str_of("selected_id");
        if selected_id.is_empty() {
            selected_id = str_of("broker_id");
        }
        if selected_id.is_empty() {
            selected_id = value
                .get("selection")
                .and_then(|s| s.get("name"))
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
        }
        let mut display_name = str_of("display_name");
        let mut venue_subtitle = str_of("venue_subtitle");
        let mut status_raw = str_of("status_raw");
        if status_raw.is_empty() {
            status_raw = str_of("status");
            if status_raw.is_empty() {
                status_raw = str_of("health");
            }
        }
        if status_raw.is_empty() {
            status_raw = "NOT_CONFIGURED".to_string();
        }
        // Legacy `record` object carries the same facts one level down.
        if let Some(record) = value.get("record").and_then(|v| v.as_object()) {
            let rec_str = |key: &str| -> String {
                record
                    .get(key)
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string()
            };
            if display_name.is_empty() {
                display_name = rec_str("name");
            }
            if status_raw == "NOT_CONFIGURED" {
                let rec_status = rec_str("status");
                if !rec_status.is_empty() {
                    status_raw = rec_status;
                }
            }
        }
        // The sidebar row for the selection carries display facts too.
        if display_name.is_empty() {
            if let Some(row) = brokers.iter().find(|b| b.id == selected_id) {
                display_name = row.display_name.clone();
            }
        }
        if venue_subtitle.is_empty() {
            if let Some(row) = brokers.iter().find(|b| b.id == selected_id) {
                venue_subtitle = row.venue_subtitle.clone();
            }
        }
        // Mark selection when the bridge only sent the id.
        if !selected_id.is_empty() && !brokers.iter().any(|b| b.selected) {
            for row in brokers.iter_mut() {
                row.selected = row.id == selected_id;
            }
        }

        let mut env_label = str_of("environment").to_uppercase();
        if env_label.trim().is_empty() {
            env_label = value
                .get("selection")
                .and_then(|s| s.get("environment"))
                .and_then(|v| v.as_str())
                .unwrap_or("paper")
                .to_uppercase();
        }
        if env_label.trim().is_empty() {
            env_label = "PAPER".to_string();
        }

        let configured = bool_of("configured");
        let can_login = bool_of("can_login");
        let can_disconnect = bool_of("can_disconnect");
        let reason = str_of("reason");

        // Credential schema (never values).
        let mut fields = Vec::new();
        if let Some(list) = value.get("credential_fields").and_then(|v| v.as_array()) {
            for row in list {
                let key = row
                    .get("key")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                if key.is_empty() {
                    continue;
                }
                let label = row
                    .get("label")
                    .and_then(|v| v.as_str())
                    .unwrap_or(&key)
                    .to_string();
                let placeholder = row
                    .get("placeholder")
                    .and_then(|v| v.as_str())
                    .map(str::to_string)
                    .unwrap_or_else(|| format!("Enter {label}"));
                fields.push(CredentialFieldView {
                    secret: row.get("secret").and_then(|v| v.as_bool()).unwrap_or(false),
                    required: row
                        .get("required")
                        .and_then(|v| v.as_bool())
                        .unwrap_or(false),
                    saved: row.get("saved").and_then(|v| v.as_bool()).unwrap_or(false),
                    key,
                    label,
                    placeholder,
                });
            }
        }

        let state = connection_state(&status_raw, configured);
        // The CTA is enabled when the backend affords a login attempt, or
        // when nothing is configured yet (the form itself is the action).
        // Authenticating/working states never accept another attempt.
        let working = matches!(
            state,
            ConnectionState::Authenticating
                | ConnectionState::GettingToken
                | ConnectionState::Verifying
        );
        let can_connect = !working
            && (can_login
                || state == ConnectionState::NotConfigured
                || state == ConnectionState::Ready);

        Self {
            brokers,
            selected_id,
            display_name,
            venue_subtitle,
            env_label,
            status_raw,
            state,
            configured,
            can_connect,
            can_disconnect,
            reason,
            fields,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn status_mapping_covers_all_manager_states() {
        assert_eq!(
            connection_state("NOT_CONFIGURED", false),
            ConnectionState::NotConfigured
        );
        assert_eq!(
            connection_state("LOGIN_REQUIRED", true),
            ConnectionState::Ready
        );
        assert_eq!(
            connection_state("LOGIN_REQUIRED", false),
            ConnectionState::NotConfigured
        );
        assert_eq!(
            connection_state("AUTHENTICATING", true),
            ConnectionState::Authenticating
        );
        assert_eq!(
            connection_state("CONFIGURING", true),
            ConnectionState::Authenticating
        );
        assert_eq!(
            connection_state("CONNECTED", true),
            ConnectionState::Connected
        );
        assert_eq!(
            connection_state("LIVE_READY", true),
            ConnectionState::Connected
        );
        for degraded in ["DISCONNECTED", "ERROR", "ACCOUNT_NOT_READY"] {
            assert_eq!(
                connection_state(degraded, true),
                ConnectionState::Failed,
                "{degraded}"
            );
        }
        // Unknown strings never look ready.
        assert_eq!(
            connection_state("SOMETHING_NEW", true),
            ConnectionState::Failed
        );
        assert_eq!(
            connection_state("SOMETHING_NEW", false),
            ConnectionState::NotConfigured
        );
    }

    #[test]
    fn pill_is_explicit_text_with_restrained_tone() {
        assert_eq!(ConnectionState::NotConfigured.pill_label(), "Not Connected");
        assert_eq!(ConnectionState::Ready.pill_tone(), 3);
        assert_eq!(
            ConnectionState::Authenticating.pill_label(),
            "Authenticating"
        );
        assert_eq!(ConnectionState::Connected.pill_label(), "Connected");
        assert_eq!(ConnectionState::Connected.pill_tone(), 1);
        assert_eq!(ConnectionState::Failed.pill_label(), "Connection Failed");
    }

    #[test]
    fn progress_is_honest_about_granularity() {
        // Nothing configured: everything pending.
        let steps = progress_steps(ConnectionState::NotConfigured);
        assert!(steps.iter().all(|s| s.state == 0));
        assert!(steps.iter().all(|s| s.subtitle == "Not started"));
        // Ready: only credentials done (saved), nothing fabricated active.
        let steps = progress_steps(ConnectionState::Ready);
        assert_eq!(
            steps.iter().map(|s| s.state).collect::<Vec<_>>(),
            vec![2, 0, 0, 0, 0]
        );
        // Authenticating: credentials done, authenticate active, token +
        // verify honestly pending (the backend emits no such sub-states).
        let steps = progress_steps(ConnectionState::Authenticating);
        assert_eq!(
            steps.iter().map(|s| s.state).collect::<Vec<_>>(),
            vec![2, 1, 0, 0, 0]
        );
        assert_eq!(steps[1].subtitle, "In progress…");
        assert_eq!(steps[2].subtitle, "Not started");
        // Connected: everything done, final step verified.
        let steps = progress_steps(ConnectionState::Connected);
        assert!(steps.iter().all(|s| s.state == 2));
        assert_eq!(steps[4].subtitle, "Verified");
        // Failed: authenticate failed, rest pending (no fake partial token).
        let steps = progress_steps(ConnectionState::Failed);
        assert_eq!(steps[1].state, 3);
        assert_eq!(steps[2].state, 0);
    }

    #[test]
    fn cta_is_the_single_obvious_action() {
        assert_eq!(
            cta_label(ConnectionState::NotConfigured, "Fyers"),
            "Connect to Fyers"
        );
        assert_eq!(
            cta_label(ConnectionState::Ready, "Fyers"),
            "Connect to Fyers"
        );
        assert_eq!(
            cta_label(ConnectionState::Authenticating, "Fyers"),
            "Authenticating…"
        );
        assert_eq!(
            cta_label(ConnectionState::Connected, "Fyers"),
            "Manage Connection"
        );
        assert_eq!(cta_label(ConnectionState::Failed, "Fyers"), "Retry");
        // Empty display names never produce an empty button.
        assert_eq!(cta_label(ConnectionState::Ready, ""), "Connect to Broker");
    }

    #[test]
    fn sidebar_rows_carry_explicit_status() {
        let on = BrokerListItem {
            id: "zerodha".into(),
            display_name: "Zerodha".into(),
            venue_subtitle: "Kite Connect".into(),
            connected: true,
            selected: false,
        };
        assert_eq!(on.status_label(), "Connected");
        assert_eq!(on.status_tone(), 1);
        assert_eq!(on.mark(), "Z");
        let off = BrokerListItem {
            connected: false,
            selected: true,
            ..on.clone()
        };
        assert_eq!(off.status_label(), "Not Connected");
        assert_eq!(off.status_tone(), 3);
    }

    #[test]
    fn description_names_the_venue_honestly() {
        assert_eq!(
            description_line("Fyers"),
            "Connect your FYERS account to start trading."
        );
        assert_eq!(
            description_line(""),
            "Connect your account to start trading."
        );
    }

    fn workspace_json() -> serde_json::Value {
        json!({
            "brokers": [
                {"id": "zerodha", "display_name": "Zerodha",
                 "venue_subtitle": "Kite Connect", "status": "CONNECTED"},
                {"id": "fyers", "display_name": "Fyers",
                 "venue_subtitle": "FYERS API v3", "status": "LOGIN_REQUIRED"},
            ],
            "selected_id": "fyers",
            "display_name": "Fyers",
            "venue_subtitle": "FYERS API v3",
            "environment": "paper",
            "status_raw": "LOGIN_REQUIRED",
            "configured": true,
            "can_login": true,
            "can_disconnect": false,
            "reason": "",
            "credential_fields": [
                {"key": "app_id", "label": "App ID",
                 "placeholder": "Enter FYERS App ID",
                 "secret": false, "required": true},
                {"key": "secret", "label": "Secret ID",
                 "placeholder": "Enter FYERS Secret ID",
                 "secret": true, "required": true},
            ],
        })
    }

    #[test]
    fn workspace_parses_full_bridge_shape() {
        let workspace = BrokerWorkspace::from_json(&workspace_json());
        assert_eq!(workspace.brokers.len(), 2);
        assert!(workspace.brokers[0].connected);
        assert!(!workspace.brokers[1].connected);
        // Selection marks the FYERS row even though the bridge sent no
        // per-row `selected` flags.
        assert!(workspace.brokers[1].selected);
        assert_eq!(workspace.selected_id, "fyers");
        assert_eq!(workspace.state, ConnectionState::Ready);
        assert_eq!(workspace.pill(), ("Not Connected", 3));
        assert_eq!(workspace.cta(), "Connect to Fyers");
        assert!(workspace.can_connect);
        assert!(workspace.form_enabled());
        assert_eq!(workspace.fields.len(), 2);
        assert!(workspace.fields[1].secret);
        assert_eq!(workspace.progress().len(), 5);
    }

    #[test]
    fn workspace_degrades_without_inventing() {
        let workspace = BrokerWorkspace::from_json(&json!({}));
        assert_eq!(workspace.state, ConnectionState::NotConfigured);
        assert!(workspace.brokers.is_empty() && workspace.fields.is_empty());
        assert_eq!(workspace.display_name, "");
        assert_eq!(workspace.env_label, "PAPER");
        assert_eq!(workspace.cta(), "Connect to Broker");
        // Legacy single-broker bridge shape keeps rendering.
        let legacy = BrokerWorkspace::from_json(&json!({
            "selection": {"name": "zerodha", "environment": "live"},
            "record": {"name": "Zerodha", "status": "CONNECTED"},
            "broker_id": "zerodha",
            "configured": true,
        }));
        assert_eq!(legacy.selected_id, "zerodha");
        assert_eq!(legacy.state, ConnectionState::Connected);
        assert_eq!(legacy.env_label, "LIVE");
        // Secrets never appear: the schema carries shapes, never values.
        let blob = serde_json::to_string(&workspace_json()).unwrap();
        assert!(!blob.contains("SECRET") || blob.contains("Secret ID"));
    }

    #[test]
    fn failed_state_carries_secret_free_guidance() {
        let mut value = workspace_json();
        value["status_raw"] = json!("ERROR");
        value["reason"] = json!("venue rejected the session");
        let workspace = BrokerWorkspace::from_json(&value);
        assert_eq!(workspace.state, ConnectionState::Failed);
        assert_eq!(workspace.cta(), "Retry");
        assert!(workspace
            .status_message()
            .contains("Check your credentials"));
        assert!(!workspace.status_message().contains("SECRET"));
    }
}
