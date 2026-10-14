//! Remote live feed for the graphical client (`--remote-ui`, read-only).
//!
//! The companion smoke probe lives in [`crate::remote_source`] (`--remote`
//! proves handshake + snapshot, then exits). This module goes one step
//! further: it feeds the EXISTING graphical UI from the private EC2 gateway
//! (`wss://…/vayren/v1`, schema v1) so the Windows EXE works as a remote
//! client with zero UI redesign.
//!
//! Steady state per connection:
//! ```text
//! initial_connect: hello → welcome → snapshot → subscribe
//! pump: snapshot | state_update | event | pong …
//! drop: RECONNECTING (backoff) → subscribe{channels, last_seq} → replay | resync
//! ```
//! Rules (non-negotiable, enforced by construction):
//! - The token comes from [`vayren_remote_client::resolve_remote_token`]
//!   (process env, then the OS credential store). It is never hardcoded,
//!   never a CLI flag (process lists are world-readable), never logged, and
//!   dropped right after the `hello` frame on the initial connect; reconnects
//!   re-resolve it instead of retaining it.
//! - Command authority comes from the gateway's own `welcome` role: a
//!   `trade` credential makes START/STOP/HALT live (each command still
//!   re-authorized server-side and refused with its exact reason), a `read`
//!   credential leaves them honestly inert. The gateway authorizes every
//!   command independently — the UI gate is presentation, never authority.
//! - Remote sections are translated into the LOCAL live-snapshot shape and
//!   applied through the existing [`LiveState::apply_snapshot`] — one mapping,
//!   no second projection, no UI change. Unknown stays unknown: absent keys
//!   leave current state untouched, and unreported capital fails closed to
//!   NOT READY instead of rendering assumed funds.
//! - TLS validation is enforced by the transport (rustls + webpki roots);
//!   plain `ws://` is accepted for loopback probes only.

use std::sync::mpsc;
use std::time::Duration;
use vayren_domain::live::{Gate, GateStatus, LiveEvent, LiveState, RiskStatus};
use vayren_remote_client::{
    launch::{bootstrap_backoff, enrollment_note, Enrollment},
    resolve_remote_token, Backoff, ClientError, ClientEvent, RemoteClient,
};

/// Canonical gateway URL + env override live in the launch contract (one
/// source of truth); re-exported here so existing paths keep working.
pub use vayren_remote_client::launch::{REMOTE_DEFAULT_URL, REMOTE_URL_ENV_VAR};

/// Deadline for the initial hello → welcome → snapshot handshake.
pub const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(15);

/// Idle deadline inside the pump; expiry sends a heartbeat `ping`.
pub const PUMP_IDLE_TIMEOUT: Duration = Duration::from_secs(30);

/// Bounded remote-event buffer (oldest dropped first, totals stay truthful).
pub const REMOTE_EVENT_CAP: usize = 200;

/// Name of the synthetic readiness gate carrying the order-stream fact.
pub const ORDER_STREAM_GATE: &str = "ORDER STREAM";

/// Name of the risk line carrying the SL-protection fact.
pub const STOP_PROTECTION_LINE: &str = "STOP PROTECTION";

/// Extract `--remote-ui <url>` / `--remote-ui=<url>` from the binary argv.
/// A bare `--remote-ui` means the default private endpoint.
pub fn remote_ui_url_from_argv(argv: &[String]) -> Option<String> {
    let mut index = 0;
    while index < argv.len() {
        let arg = argv[index].as_str();
        if let Some(value) = arg.strip_prefix("--remote-ui=") {
            return Some(if value.trim().is_empty() {
                remote_url_default()
            } else {
                value.to_string()
            });
        }
        if arg == "--remote-ui" {
            if index + 1 < argv.len() && !argv[index + 1].starts_with("--") {
                return Some(argv[index + 1].clone());
            }
            return Some(remote_url_default());
        }
        index += 1;
    }
    None
}

/// Effective gateway URL: explicit flag value, then env, then the default
/// (canonical rule lives in the launch contract).
fn remote_url_default() -> String {
    vayren_remote_client::launch::default_remote_url()
}

fn section<'a>(sections: &'a serde_json::Value, name: &str) -> &'a serde_json::Value {
    sections.get(name).unwrap_or(&serde_json::Value::Null)
}

fn text_at(sections: &serde_json::Value, section_name: &str, key: &str) -> String {
    section(sections, section_name)
        .get(key)
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string()
}

fn num_at(sections: &serde_json::Value, section_name: &str, key: &str) -> Option<f64> {
    section(sections, section_name)
        .get(key)
        .and_then(|v| v.as_f64())
        .filter(|v| v.is_finite())
}

/// One authoritative broker-session reading shared by the SYSTEM panel and
/// the LIVE screen. The gateway ships the unified EC2 session both as the
/// top-level `broker_session` section (every snapshot, not only after
/// commands) and inside each `brokers[]` row (`session`); both come from the
/// same manager snapshot, so either is the same truth. The legacy `broker`
/// section (a coarse connected flag + reason) is only a fallback for older
/// gateways. Reading two different sections is what once painted SYSTEM
/// "Connected" while LIVE read "expired" from the same snapshot.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BrokerAuthority {
    pub broker_id: String,
    pub display_name: String,
    pub session: serde_json::Value,
    pub field_keys: Vec<String>,
    pub field_labels: Vec<String>,
    pub field_secret: Vec<bool>,
    pub field_required: Vec<bool>,
    pub fields_configured: bool,
}

/// Pending CONNECT input: which EC2 venue the form targets plus the
/// schema key order its values map onto. Values travel only inside the
/// authenticated `connect_broker` command; nothing is stored client-side.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct RemoteBrokerForm {
    pub broker_id: String,
    pub keys: Vec<String>,
}

fn session_text(session: &serde_json::Value, key: &str) -> String {
    session
        .get(key)
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string()
}

fn schema_rows(row: &serde_json::Value) -> Vec<serde_json::Value> {
    row.get("credential_schema")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default()
}

/// Resolve the authoritative session from a gateway snapshot. Prefers the
/// top-level `broker_session` (present on every snapshot from a current
/// gateway), then the matching `brokers[]` row `session`. Returns `None`
/// when neither exists so callers fall back to the legacy section.
pub fn authoritative_broker(sections: &serde_json::Value) -> Option<BrokerAuthority> {
    let empty = serde_json::Value::Null;
    let top = sections.get("broker_session").unwrap_or(&empty);
    let top_obj = top.as_object();
    let top_state = top_obj
        .and_then(|o| o.get("state"))
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let legacy_name = sections
        .get("broker")
        .and_then(|b| b.get("name"))
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let rows: Vec<serde_json::Value> = sections
        .get("brokers")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    // Candidate session: top-level first, then the row matching the active
    // broker name, then a lone row (single-venue snapshots need no match).
    let mut candidate: Option<serde_json::Value> = None;
    let mut candidate_row: Option<serde_json::Value> = None;
    if top_obj.is_some() && (!top_state.is_empty() || session_text(top, "broker_id") != "") {
        candidate = Some(top.clone());
        let id = session_text(top, "broker_id").to_lowercase();
        candidate_row = rows
            .iter()
            .find(|r| {
                r.get("id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_lowercase()
                    == id
            })
            .cloned();
    }
    if candidate.is_none() && !rows.is_empty() {
        let lname = legacy_name.to_lowercase();
        candidate_row = rows
            .iter()
            .find(|r| {
                let id = r
                    .get("id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_lowercase();
                let nm = r
                    .get("name")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_lowercase();
                (!lname.is_empty() && (id == lname || nm == lname))
                    || (lname.is_empty() && rows.len() == 1)
            })
            .cloned();
        // Single-row snapshots carry the only venue even without a name.
        if candidate_row.is_none() && rows.len() == 1 {
            candidate_row = rows.first().cloned();
        }
        if let Some(row) = candidate_row.clone() {
            if let Some(sess) = row.get("session").and_then(|v| v.as_object()) {
                if !sess.is_empty() {
                    candidate = Some(serde_json::Value::Object(sess.clone()));
                }
            }
        }
    }
    let session = candidate?;
    let row = candidate_row.unwrap_or(serde_json::Value::Null);
    let mut broker_id = session_text(&session, "broker_id").to_lowercase();
    if broker_id.is_empty() {
        broker_id = row
            .get("id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_lowercase();
    }
    if broker_id.is_empty() {
        broker_id = legacy_name.to_lowercase();
    }
    if broker_id.is_empty() {
        broker_id = "fyers".to_string();
    }
    let display_name = row
        .get("display_name")
        .or_else(|| row.get("name"))
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string())
        .unwrap_or_else(|| {
            if legacy_name.is_empty() {
                broker_id.to_uppercase()
            } else {
                legacy_name.to_string()
            }
        });
    let mut field_keys = Vec::new();
    let mut field_labels = Vec::new();
    let mut field_secret = Vec::new();
    let mut field_required = Vec::new();
    for field in schema_rows(&row) {
        let key = field.get("key").and_then(|v| v.as_str()).unwrap_or("");
        if key.is_empty() {
            continue;
        }
        field_keys.push(key.to_string());
        field_labels.push(
            field
                .get("label")
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
                .unwrap_or(key)
                .to_string(),
        );
        field_secret.push(
            field
                .get("secret")
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
        );
        field_required.push(
            field
                .get("required")
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
        );
    }
    let fields_configured = row
        .get("configured")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    Some(BrokerAuthority {
        broker_id,
        display_name,
        session,
        field_keys,
        field_labels,
        field_secret,
        field_required,
        fields_configured,
    })
}

/// One shared verdict from a unified session state, so SYSTEM and LIVE can
/// never disagree: the same state maps to the same words, pill, and flags
/// on both screens.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BrokerVerdict {
    pub connected: bool,
    pub failed: bool,
    /// In-progress states (checking/connecting/verifying/…) paint activity
    /// without claiming success or failure.
    pub in_progress: bool,
    pub pill_label: String,
    pub pill_tone: i32,
    pub status_message: String,
    pub description: String,
}

pub fn session_verdict(authority: &BrokerAuthority) -> BrokerVerdict {
    let session = &authority.session;
    let state = session_text(session, "state").to_uppercase();
    let reason = session_text(session, "reason");
    let next_action = session_text(session, "next_action");
    let account_id = session_text(session, "account_id");
    let venue = if authority.display_name.is_empty() {
        authority.broker_id.to_uppercase()
    } else {
        authority.display_name.clone()
    };
    let status_or = |fallback: &str| {
        if !reason.is_empty() {
            reason.clone()
        } else if !next_action.is_empty() {
            next_action.clone()
        } else {
            fallback.to_string()
        }
    };
    match state.as_str() {
        "CONNECTED" => BrokerVerdict {
            connected: true,
            failed: false,
            in_progress: false,
            pill_label: "Connected".to_string(),
            pill_tone: 1,
            status_message: if reason.is_empty() {
                "Connection verified.".to_string()
            } else {
                format!("Connection verified: {reason}")
            },
            description: if account_id.is_empty() {
                "Account connected and verified. Ready to trade.".to_string()
            } else {
                format!("Connected: authenticated as {account_id}")
            },
        },
        "DEGRADED" => BrokerVerdict {
            connected: true,
            failed: false,
            in_progress: false,
            pill_label: "Connected (Degraded)".to_string(),
            pill_tone: 1,
            status_message: status_or("Connected with a degraded leg."),
            description: if next_action.is_empty() {
                format!("{venue} is connected; one connection leg needs attention.")
            } else {
                next_action.clone()
            },
        },
        "CHECKING" | "CONNECTING" | "VERIFYING" | "RECONNECTING" | "RATE_LIMITED"
        | "AUTHENTICATING" | "CONFIGURING" | "CHECKING SESSION" | "RATE LIMITED" => BrokerVerdict {
            connected: false,
            failed: false,
            in_progress: true,
            pill_label: "Connecting…".to_string(),
            pill_tone: 2,
            status_message: status_or("Authentication in progress…"),
            description: if next_action.is_empty() {
                format!("Waiting on {venue}…")
            } else {
                next_action.clone()
            },
        },
        "LOGIN_REQUIRED" => BrokerVerdict {
            connected: false,
            failed: false,
            in_progress: false,
            pill_label: "Login Required".to_string(),
            pill_tone: 2,
            status_message: if next_action.is_empty() {
                status_or("Broker login required.")
            } else {
                next_action.clone()
            },
            description: if reason.is_empty() {
                format!("{venue} saved session is missing or expired.")
            } else {
                reason.clone()
            },
        },
        "DISCONNECTED" => BrokerVerdict {
            connected: false,
            failed: false,
            in_progress: false,
            pill_label: "Not Connected".to_string(),
            pill_tone: 3,
            status_message: status_or("Not connected."),
            description: if next_action.is_empty() {
                format!("Connect {venue} from SYSTEM → BROKERS.")
            } else {
                next_action.clone()
            },
        },
        _ => BrokerVerdict {
            connected: false,
            failed: true,
            in_progress: false,
            pill_label: "Connection Failed".to_string(),
            pill_tone: 3,
            status_message: status_or("Connection failed."),
            description: if next_action.is_empty() {
                "Connection failed. Review the details and retry.".to_string()
            } else {
                next_action.clone()
            },
        },
    }
}

/// Zip form values onto schema keys for `connect_broker`. Empty inputs and
/// saved-value sentinels (•••) are dropped so untouched fields keep the
/// gateway's stored values — a blank retype can never wipe saved secrets.
pub fn zip_broker_credentials(keys: &[String], values: &[String]) -> serde_json::Value {
    let mut map = serde_json::Map::new();
    for (index, key) in keys.iter().enumerate() {
        let raw = values.get(index).map(|s| s.as_str()).unwrap_or("");
        let clean = raw.replace('•', "").trim().to_string();
        if key.is_empty() || clean.is_empty() {
            continue;
        }
        map.insert(key.clone(), serde_json::Value::String(clean));
    }
    serde_json::Value::Object(map)
}

/// Translate gateway sections into the LOCAL live-snapshot shape, then apply
/// through the existing [`LiveState::apply_snapshot`].
///
/// Only keys the gateway actually sent are translated; everything else keeps
/// its current value (unknown remains unknown, never zero-filled). Tables
/// (`orders`/`positions`/`fills`) and blockers replace when present, so a
/// `resync: true` snapshot heals a gappy stream by construction.
pub fn apply_remote_sections(state: &mut LiveState, sections: &serde_json::Value, resync: bool) {
    let mut translated = serde_json::Map::new();

    let mode = text_at(sections, "strategy", "mode");
    if !mode.is_empty() {
        translated.insert("mode".to_string(), serde_json::Value::String(mode));
    }
    let strategy_status = text_at(sections, "strategy", "status").to_uppercase();
    if !strategy_status.is_empty() {
        // Gateway vocabulary (RUNNING/STOPPED/BLOCKED/ERROR) → session
        // lifecycle. BLOCKED is a stopped session with a reason, never a
        // hidden running state.
        let session = match strategy_status.as_str() {
            "RUNNING" => "RUNNING",
            "ERROR" => "ERROR",
            _ => "STOPPED",
        };
        translated.insert(
            "session_status".to_string(),
            serde_json::Value::String(session.to_string()),
        );
        let strat_id = text_at(sections, "strategy", "id");
        let strat_name = if strat_id.is_empty() {
            text_at(sections, "strategy", "name")
        } else {
            strat_id
        };
        if !strat_name.is_empty() {
            translated.insert(
                "strategy".to_string(),
                serde_json::json!({
                    "id": strat_name,
                    "name": strat_name,
                    "status": strategy_status,
                    "mode": text_at(sections, "strategy", "mode")
                }),
            );
        }
    }
    let reason = text_at(sections, "strategy", "reason");
    if !reason.is_empty() {
        translated.insert(
            "status_reason".to_string(),
            serde_json::Value::String(reason),
        );
    }

    if let Some(auth) = authoritative_broker(sections) {
        // Authoritative EC2 session first: the same snapshot SYSTEM reads.
        // Legacy `broker` stays as the fallback below for older gateways.
        let verdict = session_verdict(&auth);
        let state = session_text(&auth.session, "state");
        let reason = session_text(&auth.session, "reason");
        let reason = if reason.is_empty() {
            verdict.status_message.clone()
        } else {
            reason
        };
        translated.insert(
            "broker".to_string(),
            serde_json::json!({
                "name": auth.display_name,
                "account_id": session_text(&auth.session, "account_id"),
                "status": if state.is_empty() { "UNKNOWN".to_string() } else { state },
                "reason": reason,
                "connected": verdict.connected,
                "last_heartbeat": text_at(sections, "broker", "last_sync"),
            }),
        );
    } else if !section(sections, "broker").is_null() {
        let status = text_at(sections, "broker", "status");
        let name = text_at(sections, "broker", "name");
        let bname = if name.is_empty() {
            state.broker.name.clone()
        } else {
            name
        };
        let reason = text_at(sections, "broker", "reason");
        let r_lower = reason.to_lowercase();
        let has_failure = r_lower.contains("timeout")
            || r_lower.contains("timed out")
            || r_lower.contains("unreachable")
            || r_lower.contains("failed")
            || r_lower.contains("rejected")
            || r_lower.contains("expired");
        let is_conn = if has_failure
            || status == "DISCONNECTED"
            || status == "ERROR"
            || status == "CONNECTION FAILED"
        {
            false
        } else {
            sections
                .get("broker")
                .and_then(|b| b.get("connected"))
                .and_then(|c| c.as_bool())
                .unwrap_or(status == "CONNECTED")
        };
        let last_sync = text_at(sections, "broker", "last_sync");
        translated.insert(
            "broker".to_string(),
            serde_json::json!({
                "name": bname,
                "account_id": text_at(sections, "broker", "account_id"),
                "status": status,
                "reason": reason,
                "connected": is_conn,
                "last_heartbeat": last_sync,
            }),
        );
    }
    if !section(sections, "risk").is_null() {
        let status = text_at(sections, "risk", "status");
        translated.insert(
            "risk".to_string(),
            serde_json::json!({"status": if status.is_empty() { "NOT READY".to_string() } else { status }}),
        );
    }
    if !section(sections, "capital").is_null() {
        // Fail closed: a capital block that names no source (or NOT
        // REPORTED) with no funds is mapped onto the broker source with no
        // numbers, so `has_valid_capital` is false and the UI reads NOT
        // READY instead of assuming the default sizing basis.
        let source = text_at(sections, "capital", "capital_source");
        let unknown_source = source.is_empty() || source == "NOT REPORTED";
        let mut capital = serde_json::Map::new();
        capital.insert(
            "source".to_string(),
            serde_json::Value::String(if unknown_source {
                "broker".to_string()
            } else {
                source
            }),
        );
        if !unknown_source {
            if let Some(v) = num_at(sections, "capital", "broker_capital") {
                capital.insert("broker_capital".to_string(), v.into());
            }
            if let Some(v) = num_at(sections, "capital", "available") {
                capital.insert("available_margin".to_string(), v.into());
            }
            let bname = text_at(sections, "capital", "broker_name");
            if !bname.is_empty() {
                capital.insert("broker_name".to_string(), serde_json::Value::String(bname));
            }
            let cstatus = text_at(sections, "capital", "status");
            if !cstatus.is_empty() {
                capital.insert("status".to_string(), serde_json::Value::String(cstatus));
            }
            let clabel = text_at(sections, "capital", "label");
            if !clabel.is_empty() {
                capital.insert("label".to_string(), serde_json::Value::String(clabel));
            }
            let clast = text_at(sections, "capital", "last_updated");
            if !clast.is_empty() {
                capital.insert("last_updated".to_string(), serde_json::Value::String(clast));
            }
        }
        translated.insert("capital".to_string(), serde_json::Value::Object(capital));
    }
    if !section(sections, "reconciliation").is_null() {
        translated.insert(
            "reconciliation".to_string(),
            serde_json::json!({
                "status": text_at(sections, "reconciliation", "status"),
                "blocks_live": section(sections, "reconciliation")
                    .get("blocks_live").and_then(|v| v.as_bool()).unwrap_or(true),
            }),
        );
    }
    if !section(sections, "market").is_null() {
        let status = text_at(sections, "market", "status");
        translated.insert(
            "market_data".to_string(),
            serde_json::json!({
                "status": if status.is_empty() { "NOT REPORTED".to_string() } else { status },
                "exchange": text_at(sections, "market", "exchange"),
                "timeframe": text_at(sections, "market", "timeframe"),
            }),
        );
        let feed = text_at(sections, "market", "feed");
        if !feed.is_empty() {
            translated.insert("feed".to_string(), serde_json::Value::String(feed));
        }
    }
    if let Some(ws_val) = sections.get("websocket").filter(|v| v.is_object()) {
        translated.insert("websocket".to_string(), ws_val.clone());
    } else if !section(sections, "system").is_null() {
        let ws = text_at(sections, "system", "websocket");
        if !ws.is_empty() {
            translated.insert("websocket".to_string(), serde_json::json!({"status": ws}));
        }
    }
    if !section(sections, "system").is_null() {
        if let Some(halted) = section(sections, "system")
            .get("kill_halted")
            .and_then(|v| v.as_bool())
        {
            translated.insert("kill".to_string(), serde_json::json!({"halted": halted}));
        }
    }
    for key in ["orders", "positions", "fills"] {
        if let Some(list) = sections.get(key).and_then(|v| v.as_array()) {
            translated.insert(key.to_string(), serde_json::Value::Array(list.clone()));
        }
    }
    for key in [
        "quotes",
        "events",
        "gates",
        "available_strategies",
        "available_symbols",
        "selected_symbols",
        "available_timeframes",
    ] {
        if let Some(list) = sections.get(key).and_then(|v| v.as_array()) {
            translated.insert(key.to_string(), serde_json::Value::Array(list.clone()));
        }
    }
    if let Some(pnl) = sections.get("pnl").filter(|v| v.is_object()) {
        translated.insert("pnl".to_string(), pnl.clone());
    }
    if let Some(risk_engine) = sections.get("risk_engine").filter(|v| v.is_object()) {
        translated.insert("risk_engine".to_string(), risk_engine.clone());
    }
    let stf = sections
        .get("selected_timeframe")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .or_else(|| {
            sections
                .get("market_timeframe")
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
        })
        .or_else(|| {
            sections
                .get("market")
                .and_then(|m| m.get("timeframe"))
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
        })
        .or_else(|| {
            sections
                .get("strategy")
                .and_then(|m| m.get("timeframe"))
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
        });
    if let Some(stf) = stf {
        translated.insert(
            "selected_timeframe".to_string(),
            serde_json::Value::String(stf.to_string()),
        );
    }
    if !translated.contains_key("available_timeframes") {
        translated.insert(
            "available_timeframes".to_string(),
            serde_json::json!(["1m", "3m", "5m", "15m", "30m", "45m", "1h", "1d"]),
        );
    }
    if let Some(qty) = sections.get("quantity").and_then(|v| v.as_f64()) {
        translated.insert("quantity".to_string(), serde_json::json!(qty));
    }
    if let Some(ss) = sections.get("selected_symbol").and_then(|v| v.as_str()) {
        translated.insert(
            "selected_symbol".to_string(),
            serde_json::Value::String(ss.to_string()),
        );
    }
    if let Some(blockers) = sections.get("blockers").and_then(|v| v.as_array()) {
        // The backend's own start verdict, shown verbatim — it is what
        // decides readiness, not the link's role.
        let honest: Vec<serde_json::Value> = blockers
            .iter()
            .filter_map(|v| v.as_str())
            .map(|s| serde_json::Value::String(s.to_string()))
            .collect();
        translated.insert(
            "start_blockers".to_string(),
            serde_json::Value::Array(honest),
        );
    }

    state.apply_snapshot(&serde_json::Value::Object(translated));
    // Command capability is the ROLE the gateway granted, never an optimistic
    // guess: `bridge_wired` drives `can_start`/`can_stop`/`can_halt`, so a
    // `read` credential must stay false (controls honestly inert) while a
    // `trade` credential becomes true (controls live). The gateway re-checks
    // the role on every command, so this is presentation, not authority.
    state.bridge_wired = state.remote_commands_allowed;
    if !state.host_mode {
        state.host_mode = true;
    }
    if !state.has_valid_capital() {
        state.risk_status = RiskStatus::NotReady;
    }
    if state.strategies.is_empty() {
        state.strategies = vec![
            "EMA Crossover".to_string(),
            "OBR".to_string(),
            "OBR C1C4".to_string(),
            "SMA Crossover".to_string(),
            "RSI Strategy".to_string(),
        ];
    }

    // Order-stream fact rides as a readiness gate (extra gates never affect
    // the five named venue gates; the backend's own `start_blockers` verdict
    // is what gates readiness).
    if !section(sections, "order_stream").is_null() {
        let status = text_at(sections, "order_stream", "status");
        state.gates.retain(|g| g.name != ORDER_STREAM_GATE);
        state.gates.push(Gate {
            name: ORDER_STREAM_GATE.to_string(),
            status: match status.as_str() {
                "CONNECTED" => GateStatus::Ready,
                "DISCONNECTED" => GateStatus::Blocked,
                _ => GateStatus::Unknown,
            },
            reason: text_at(sections, "order_stream", "detail"),
        });
    }
    // SL protection is derived server-side from working STOP orders; surface
    // it as a risk line (the orders table itself already holds the rows).
    if !section(sections, "stops").is_null() {
        let status = text_at(sections, "stops", "status");
        let detail = text_at(sections, "stops", "detail");
        state
            .risk_lines
            .retain(|(name, _)| name != STOP_PROTECTION_LINE);
        if !status.is_empty() {
            state.risk_lines.push((
                STOP_PROTECTION_LINE.to_string(),
                if detail.is_empty() {
                    status
                } else {
                    format!("{status} — {detail}")
                },
            ));
        }
    }
    // A resync means the stream had a gap: drop the local event tail rather
    // than imply a continuity the client did not observe.
    if resync {
        state.events.clear();
    }
}

/// Append one gateway `event` frame to the bounded tail (oldest first out).
pub fn push_remote_event(state: &mut LiveState, name: &str, payload: &serde_json::Value) {
    let str_field = |key: &str| {
        payload
            .get(key)
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string()
    };
    state.events.push(LiveEvent {
        timestamp: str_field("timestamp"),
        strategy: str_field("strategy"),
        symbol: str_field("symbol"),
        event: {
            let detail = str_field("detail");
            if detail.is_empty() {
                name.to_string()
            } else {
                detail
            }
        },
        status: str_field("status"),
        category: name.to_string(),
    });
    while state.events.len() > REMOTE_EVENT_CAP {
        state.events.remove(0);
    }
}

/// Honest pre-snapshot state: the demo defaults claim CONNECTED/STREAMING
/// transports, which would be a fabricated link before the first server
/// snapshot lands. `bridge_wired` stays false until the gateway grants a
/// command-capable role (see [`apply_role`]).
pub fn init_remote_state() -> LiveState {
    let mut state = LiveState::default();
    state.host_mode = true;
    state.bridge_wired = false;
    state.strategies = vec![
        "EMA Crossover".to_string(),
        "OBR".to_string(),
        "OBR C1C4".to_string(),
        "SMA Crossover".to_string(),
        "RSI Strategy".to_string(),
    ];
    // Unknown venue funds ⇒ sizing NOT READY, never the default basis.
    state.capital.source = "broker".to_string();
    state.capital.broker_capital = None;
    state.capital.available_margin = None;
    state.capital.used_margin = None;
    state.capital.configured_capital = None;
    state.risk_status = RiskStatus::NotReady;
    state.websocket.status = "CONNECTING".to_string();
    state.websocket.latency_ms = None;
    state.websocket.channel = String::new();
    state.websocket.timeframe = String::new();
    state.websocket.subscribed_symbols = 0;
    state.websocket.last_tick_time = String::new();
    state.websocket.last_error = "dialling private gateway…".to_string();
    state.market_data.status = "CONNECTING".to_string();
    state.market_data.exchange = String::new();
    state.market_data.timeframe = String::new();
    state.market_data.subscribed_symbols = 0;
    state.market_data.last_tick_time = String::new();
    state.market_data.freshness_age_s = None;
    state.broker.connected = None;
    state.gateway.connected = false;
    state.gateway.status = "CONNECTING".to_string();
    state.gateway.last_error = "dialling private gateway…".to_string();
    state.gateway.latency_ms = None;
    state.gateway.last_heartbeat = String::new();
    state
}

/// Pre-snapshot link words: before the first server snapshot the status line
/// truthfully IS the client link state (CONNECTING → AUTHENTICATING →
/// CONNECTED on success; OFFLINE while the bootstrap waits on a credential
/// or transport; RECONNECTING only after a drop).
const PRE_SNAPSHOT: [&str; 5] = [
    "",
    "CONNECTING",
    "AUTHENTICATING",
    "OFFLINE",
    "RECONNECTING",
];

/// Record a client-link transition. Before the first server snapshot the
/// status line truthfully IS the client link state; afterwards the server
/// facts own `status` and only the counters/notes move.
pub fn mark_link(state: &mut LiveState, label: &str, detail: &str, reconnects: usize) {
    state.websocket.reconnect_count = reconnects;
    if label == "CONNECTED" {
        state.websocket.last_error.clear();
    } else {
        state.websocket.last_error = detail.to_string();
    }
    if PRE_SNAPSHOT.contains(&state.websocket.status.as_str()) {
        state.websocket.status = label.to_string();
    }
    if ["", "CONNECTING", "RECONNECTING"].contains(&state.market_data.status.as_str())
        && label == "CONNECTED"
    {
        // Server facts have not arrived yet; keep the link word until the
        // first snapshot overwrites both cards with EC2-side truth.
        state.market_data.status = label.to_string();
    }

    state.gateway.reconnect_count = reconnects;
    state.gateway.status = label.to_string();
    if label == "CONNECTED" {
        state.gateway.connected = true;
        state.gateway.last_error.clear();
        state.bridge_wired = state.remote_commands_allowed;
    } else {
        state.gateway.connected = false;
        state.gateway.last_error = detail.to_string();
        // A dropped link can carry no command: fail the controls closed until
        // the pump reconnects, so START never offers a request that would sit
        // undelivered in the queue.
        state.bridge_wired = false;
        if state.pending_action.is_some() {
            state.pending_action = None;
            state.pending_since = None;
            state.action_note = Some(format!(
                "gateway link lost ({label}) — request not delivered"
            ));
        }
    }
}

/// Apply the role the gateway granted in `welcome`.
///
/// `trade` makes the trading controls live; `read` (or anything unexpected)
/// leaves them honestly inert. The gateway authorizes every command on its own
/// — this only decides what the UI offers, and a `read` credential still gets
/// a visible refusal rather than a silently dead button.
pub fn apply_role(state: &mut LiveState, role: &str) {
    state.remote_commands_allowed = role == vayren_remote_client::ROLE_TRADE;
    state.bridge_wired = state.remote_commands_allowed;
    state.action_note = if state.remote_commands_allowed {
        None
    } else {
        Some("read-only credential — START/STOP/HALT are disabled on this link".into())
    };
}

/// One UI-thread arrival from the pump thread.
#[derive(Debug)]
pub enum RemoteUiUpdate {
    /// The role the gateway granted (`trade` / `read`). Drives whether the
    /// trading controls are live or honestly inert.
    Role {
        role: String,
    },
    Snapshot {
        sections: serde_json::Value,
        resync: bool,
    },
    StateUpdate {
        sections: serde_json::Value,
    },
    StreamEvent {
        name: String,
        payload: serde_json::Value,
    },
    Link {
        label: String,
        detail: String,
        reconnects: usize,
    },
    Heartbeat {
        rtt_ms: Option<f64>,
        timestamp: String,
    },
    /// The gateway answered a control command (`command_result`): `ok` plus
    /// the reason text when it was refused. This is what clears a pending
    /// START/STOP/HALT and paints the real outcome.
    CommandAck {
        action: String,
        ok: bool,
        detail: String,
        payload: serde_json::Value,
    },
}

/// A control command the UI thread wants the pump thread to send. The pump
/// owns the socket, so intents cross as messages rather than direct calls.
#[derive(Debug, Clone)]
pub struct RemoteCommand {
    pub action: String,
    pub params: serde_json::Value,
}

/// Blocking initial connect: hello → welcome → snapshot → subscribe.
/// Resolves the token inside, drops it before returning, and never logs it.
/// Errors name the STAGE only, never the credential.
pub fn initial_connect(url: &str) -> Result<(RemoteClient, String, serde_json::Value), String> {
    let token = resolve_remote_token().map_err(|err| format!("remote token: {err}"))?;
    let (mut client, welcome, snapshot) = RemoteClient::connect(url, &token, HANDSHAKE_TIMEOUT)
        .map_err(|err| format!("remote connect failed: {err}"))?;
    drop(token);
    let role = match &welcome {
        ClientEvent::Welcome { role, .. } => role.clone(),
        other => return Err(format!("remote handshake: expected welcome, got {other:?}")),
    };
    let sections = match &snapshot {
        ClientEvent::Snapshot { sections, .. } => sections.clone(),
        other => {
            return Err(format!(
                "remote handshake: expected snapshot, got {other:?}"
            ))
        }
    };
    client
        .subscribe(&[], None)
        .map_err(|err| format!("remote subscribe failed: {err}"))?;
    Ok((client, role, sections))
}

/// Pre-connect bootstrap for the packaged client: the window opens FIRST
/// (CONNECTING) and the credential is proven in the background.
///
/// Each attempt re-resolves the token (never retained, never logged) and runs
/// the deadline-bounded [`initial_connect`]. On success the first snapshot is
/// forwarded, the link is marked CONNECTED, and the live client is handed to
/// [`spawn_remote_pump`] — which owns all later reconnects and every command
/// send — before this thread exits. On failure the UI holds OFFLINE with the
/// enrollment or transport note while the loop retries with backoff, so a
/// credential provisioned after first launch (or a gateway restart) connects
/// with zero restart and zero manual step.
fn current_time_str() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let day_secs = (secs + 19800) % 86400; // IST: UTC + 5:30
    let h = day_secs / 3600;
    let m = (day_secs % 3600) / 60;
    let s = day_secs % 60;
    format!("{:02}:{:02}:{:02}", h, m, s)
}

pub fn spawn_remote_bootstrap(
    url: String,
    tx: mpsc::Sender<RemoteUiUpdate>,
    commands: mpsc::Receiver<RemoteCommand>,
) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        let mut backoff = bootstrap_backoff();
        let mut reconnects: usize = 0;
        loop {
            let _ = tx.send(RemoteUiUpdate::Link {
                label: "AUTHENTICATING".to_string(),
                detail: "proving credential...".to_string(),
                reconnects,
            });
            match initial_connect(&url) {
                Ok((client, role, sections)) => {
                    let _ = tx.send(RemoteUiUpdate::Role { role: role.clone() });
                    let _ = tx.send(RemoteUiUpdate::Link {
                        label: "CONNECTED".to_string(),
                        detail: format!("role {role}"),
                        reconnects,
                    });
                    let _ = tx.send(RemoteUiUpdate::Heartbeat {
                        rtt_ms: None,
                        timestamp: current_time_str(),
                    });
                    let _ = tx.send(RemoteUiUpdate::Snapshot {
                        sections,
                        resync: false,
                    });
                    spawn_remote_pump(client, url, tx, commands);
                    return;
                }
                Err(err) => {
                    reconnects += 1;
                    let wait = backoff.next_wait();
                    let detail = if err.starts_with("remote token:") {
                        enrollment_note(Enrollment::Missing).to_string()
                    } else if err.contains("AUTH_FAILED") {
                        enrollment_note(Enrollment::Rejected).to_string()
                    } else {
                        format!("transport down — retrying in {}s", wait.as_secs())
                    };
                    let _ = tx.send(RemoteUiUpdate::Link {
                        label: "OFFLINE".to_string(),
                        detail,
                        reconnects,
                    });
                    std::thread::sleep(wait);
                }
            }
        }
    })
}

/// Own the connected client on a worker thread: pump arrivals to the UI,
/// heartbeat on idle, and reconnect with `subscribe{last_seq}` on drops
/// (the gateway replays missed events or answers `resync: true`).
/// This function never sends a command and never reads the token store
/// except to re-resolve for a fresh `hello` after a drop.
/// Own the connected client on a worker thread: pump arrivals to the UI,
/// send control commands the UI asked for, heartbeat on idle, and reconnect
/// with `subscribe{last_seq}` on drops (the gateway replays missed events or
/// answers `resync: true`). Command sends reuse one monotonic `request_id` per
/// action so a gateway-side retry can never double-apply. The token is
/// re-resolved only for a fresh `hello` after a drop.
pub fn spawn_remote_pump(
    mut client: RemoteClient,
    url: String,
    tx: mpsc::Sender<RemoteUiUpdate>,
    commands: mpsc::Receiver<RemoteCommand>,
) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        let mut backoff = Backoff::new(Duration::from_secs(1), Duration::from_secs(30));
        let mut reconnects: usize = 0;
        // Monotonic per-action counter: a retry reuses the SAME id (the
        // gateway replays its cached result) while a second click gets a new
        // one (it is a genuinely new intent).
        let mut request_seq: u64 = 0;
        let pump = |client: &mut RemoteClient,
                    tx: &mpsc::Sender<RemoteUiUpdate>,
                    commands: &mpsc::Receiver<RemoteCommand>,
                    request_seq: &mut u64| {
            let mut in_flight: std::collections::HashMap<String, String> =
                std::collections::HashMap::new();
            let mut last_ping: Option<std::time::Instant> = None;
            loop {
                // Drain queued commands before blocking on the socket: a START
                // pressed between two frames must not wait for the next one.
                while let Ok(command) = commands.try_recv() {
                    *request_seq += 1;
                    let request_id = format!("ui-{}", *request_seq);
                    if let Err(err) =
                        client.send_command(&command.action, command.params.clone(), &request_id)
                    {
                        let _ = tx.send(RemoteUiUpdate::CommandAck {
                            action: command.action.clone(),
                            ok: false,
                            detail: format!("send failed: {err}"),
                            payload: serde_json::Value::Null,
                        });
                    } else {
                        in_flight.insert(request_id, command.action.clone());
                    }
                }
                match client.next(PUMP_IDLE_TIMEOUT) {
                    Ok(ClientEvent::Snapshot { sections, resync }) => {
                        let _ = tx.send(RemoteUiUpdate::Snapshot { sections, resync });
                        let _ = tx.send(RemoteUiUpdate::Heartbeat {
                            rtt_ms: None,
                            timestamp: current_time_str(),
                        });
                    }
                    Ok(ClientEvent::StateUpdate { sections, .. }) => {
                        let _ = tx.send(RemoteUiUpdate::StateUpdate { sections });
                        let _ = tx.send(RemoteUiUpdate::Heartbeat {
                            rtt_ms: None,
                            timestamp: current_time_str(),
                        });
                    }
                    Ok(ClientEvent::StreamEvent { name, payload, .. }) => {
                        let _ = tx.send(RemoteUiUpdate::StreamEvent { name, payload });
                    }
                    Ok(ClientEvent::Pong) => {
                        let rtt = last_ping
                            .take()
                            .map(|t0| t0.elapsed().as_secs_f64() * 1000.0);
                        let _ = tx.send(RemoteUiUpdate::Heartbeat {
                            rtt_ms: rtt,
                            timestamp: current_time_str(),
                        });
                    }
                    Ok(ClientEvent::CommandResult {
                        request_id,
                        payload,
                    }) => {
                        // The gateway's own verdict on a control command:
                        // `ok` false carries the refusal reasons, which is
                        // what clears the pending action honestly.
                        let req_action = request_id.as_deref().and_then(|id| in_flight.remove(id));
                        let action = payload
                            .get("action")
                            .and_then(|v| v.as_str())
                            .map(|s| s.to_string())
                            .or(req_action)
                            .unwrap_or_default();
                        let ok = payload.get("ok").and_then(|v| v.as_bool()).unwrap_or(false);
                        // The gateway's own verdict keys (`start_blockers` /
                        // `status_reason`); fall back through them in order so
                        // the operator sees the service's exact reason.
                        let detail = if ok {
                            String::new()
                        } else {
                            let join = |key: &str| {
                                payload
                                    .get(key)
                                    .and_then(|v| v.as_array())
                                    .map(|list| {
                                        list.iter()
                                            .filter_map(|v| v.as_str())
                                            .collect::<Vec<_>>()
                                            .join("; ")
                                    })
                                    .unwrap_or_default()
                            };
                            let blockers = join("start_blockers");
                            if !blockers.is_empty() {
                                blockers
                            } else {
                                let reason = payload
                                    .get("status_reason")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("")
                                    .to_string();
                                if !reason.is_empty() {
                                    reason
                                } else {
                                    let err =
                                        payload.get("error").and_then(|v| v.as_str()).unwrap_or("");
                                    if !err.is_empty() {
                                        err.to_string()
                                    } else {
                                        payload
                                            .get("detail")
                                            .and_then(|v| v.as_str())
                                            .unwrap_or("refused by backend")
                                            .to_string()
                                    }
                                }
                            }
                        };
                        let _ = tx.send(RemoteUiUpdate::CommandAck {
                            action,
                            ok,
                            detail,
                            payload,
                        });
                    }
                    Ok(ClientEvent::GatewayError {
                        code,
                        detail,
                        request_id,
                    }) => {
                        // A per-command refusal (NOT_AUTHORIZED, UNKNOWN_COMMAND,
                        // COMMAND_REJECTED…) arrives as an error correlated by
                        // id. Surface it so the control stops looking pending.
                        let action = request_id
                            .as_deref()
                            .and_then(|id| in_flight.remove(id))
                            .unwrap_or_default();
                        let _ = tx.send(RemoteUiUpdate::CommandAck {
                            action,
                            ok: false,
                            detail: format!("{code}: {detail}"),
                            payload: serde_json::Value::Null,
                        });
                        if request_id.is_none() {
                            eprintln!("remote pump: gateway error [{code}]: {detail}");
                        }
                    }
                    Ok(ClientEvent::Closed) => break,
                    Ok(ClientEvent::Welcome { .. }) => {}
                    Err(ClientError::Timeout) => {
                        last_ping = Some(std::time::Instant::now());
                        if client.ping().is_err() {
                            break;
                        }
                    }
                    // A malformed frame is a per-message refusal, not a dead
                    // link — the contract test proves the stream survives it.
                    Err(ClientError::Protocol(err)) => {
                        eprintln!("remote pump: protocol refusal: {err}");
                    }
                    Err(_) => break,
                }
            }
        };
        pump(&mut client, &tx, &commands, &mut request_seq);
        // Reconnect loop: re-resolve the token per attempt (never retained),
        // resume from the last delivered seq, honour resync when asked.
        loop {
            reconnects += 1;
            let wait = backoff.next_wait();
            let _ = tx.send(RemoteUiUpdate::Link {
                label: "RECONNECTING".to_string(),
                detail: format!("link lost — retrying in {}s", wait.as_secs()),
                reconnects,
            });
            std::thread::sleep(wait);
            let last_seq = client.last_seq;
            let token = match resolve_remote_token() {
                Ok(token) => token,
                Err(err) => {
                    eprintln!("remote pump: reconnect without token: {err}");
                    continue;
                }
            };
            match RemoteClient::connect(&url, &token, HANDSHAKE_TIMEOUT) {
                Ok((mut fresh, welcome, _)) => {
                    drop(token);
                    backoff.reset();
                    if fresh.subscribe(&[], last_seq).is_err() {
                        continue;
                    }
                    client = fresh;
                    let _ = tx.send(RemoteUiUpdate::Role {
                        role: match &welcome {
                            ClientEvent::Welcome { role, .. } => role.clone(),
                            _ => String::new(),
                        },
                    });
                    let _ = tx.send(RemoteUiUpdate::Link {
                        label: "CONNECTED".to_string(),
                        detail: "resumed".to_string(),
                        reconnects,
                    });
                    let _ = tx.send(RemoteUiUpdate::Heartbeat {
                        rtt_ms: None,
                        timestamp: current_time_str(),
                    });
                    pump(&mut client, &tx, &commands, &mut request_seq);
                }
                Err(err) => {
                    drop(token);
                    eprintln!("remote pump: reconnect failed: {err}");
                }
            }
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn args(words: &[&str]) -> Vec<String> {
        words.iter().map(|w| w.to_string()).collect()
    }

    fn sections() -> serde_json::Value {
        json!({
            "broker": {"status": "DISCONNECTED", "name": "", "reason": "not started"},
            "market": {"status": "STREAMING", "feed": "live", "exchange": "NSE",
                "timeframe": "15m"},
            "order_stream": {"status": "CONNECTED", "detail": "broker order stream"},
            "strategy": {"status": "BLOCKED", "mode": "PAPER",
                "reason": "no strategy selected"},
            "risk": {"status": "NOT READY", "sizing_status": "BLOCKED",
                "sizing_reason": "no capital"},
            "capital": {"available": null, "effective": null, "max_risk": null,
                "broker_capital": null, "capital_source": "NOT REPORTED"},
            "orders": [], "positions": [], "fills": [],
            "stops": {"status": "NONE", "detail": "no open positions"},
            "reconciliation": {"status": "NOT REPORTED", "blocks_live": true},
            "system": {"execution_ready": false, "execution_reason": "",
                "kill_halted": false, "websocket": "CONNECTED"},
            "blockers": ["no strategy selected"],
            "gates": [],
            "as_of": "2026-10-06T00:00:00",
        })
    }

    #[test]
    fn remote_ui_flag_parses_forms_and_defaults() {
        assert_eq!(
            remote_ui_url_from_argv(&args(&["--remote-ui", "wss://h/vayren/v1"])).as_deref(),
            Some("wss://h/vayren/v1")
        );
        assert_eq!(
            remote_ui_url_from_argv(&args(&["--remote-ui=wss://h/vayren/v1"])).as_deref(),
            Some("wss://h/vayren/v1")
        );
        assert_eq!(remote_ui_url_from_argv(&args(&["--data-dir", "x"])), None);
        // Bare flag falls back to env/default, never to empty.
        let bare = remote_ui_url_from_argv(&args(&["--remote-ui"])).expect("bare flag");
        assert!(!bare.trim().is_empty());
        assert!(bare.starts_with("ws"));
    }

    #[test]
    fn snapshot_maps_server_facts_and_stays_read_only() {
        let mut state = init_remote_state();
        apply_remote_sections(&mut state, &sections(), false);
        assert_eq!(state.broker.status, "DISCONNECTED");
        assert_eq!(state.broker.connected, Some(false));
        assert_eq!(state.mode, vayren_domain::live::ExecMode::Paper);
        // BLOCKED strategy ⇒ stopped session with the backend reason kept.
        assert_eq!(state.session, vayren_domain::live::SessionStatus::Stopped);
        assert_eq!(state.status_reason, "no strategy selected");
        // No role granted yet ⇒ every control stays honestly inert.
        assert!(!state.remote_commands_allowed);
        assert!(!state.bridge_wired);
        assert!(!state.can_start());
        assert!(!state.start_clickable());
        assert_eq!(
            state.backend_start_blockers.as_deref(),
            Some(&["no strategy selected".to_string()][..])
        );
        // Unreported capital fails closed, never assumed.
        assert!(!state.has_valid_capital());
        assert_eq!(state.risk_status, RiskStatus::NotReady);
        // Order stream + SL protection arrive as first-class facts.
        assert!(state
            .gates
            .iter()
            .any(|g| g.name == ORDER_STREAM_GATE && g.status == GateStatus::Ready));
        assert!(state
            .risk_lines
            .iter()
            .any(|(name, _)| name == STOP_PROTECTION_LINE));
        // Market + feed + kill switch map through.
        assert_eq!(state.market_data.status, "STREAMING");
        assert_eq!(state.feed_kind, "live");
        assert!(!state.kill_halted);
        assert_eq!(state.websocket.status, "CONNECTED");
    }

    #[test]
    fn a_dropped_link_fails_the_controls_closed_and_clears_pending() {
        let mut state = init_remote_state();
        apply_role(&mut state, "trade");
        apply_remote_sections(&mut state, &sections(), false);
        assert!(state.bridge_wired);
        state.start();
        assert_eq!(state.pending_action.as_deref(), Some("start"));
        // Link drops mid-request: the command cannot be delivered.
        mark_link(&mut state, "RECONNECTING", "link lost", 1);
        assert!(!state.bridge_wired, "a dead link must not offer START");
        assert!(!state.start_clickable());
        assert!(
            state.pending_action.is_none(),
            "an undelivered request must not strand STARTING…"
        );
        assert!(state.action_note.is_some());
        // Reconnect restores command capability without a new role.
        mark_link(&mut state, "CONNECTED", "resumed", 1);
        assert!(state.bridge_wired);
    }

    #[test]
    fn unknown_sections_leave_state_untouched() {
        let mut state = init_remote_state();
        apply_remote_sections(&mut state, &sections(), false);
        let before = state.clone();
        apply_remote_sections(&mut state, &json!({}), false);
        assert_eq!(state, before);
    }

    #[test]
    fn running_session_maps_without_enabling_controls() {
        let mut state = init_remote_state();
        let mut live = sections();
        live["strategy"] = json!({"status": "RUNNING", "mode": "LIVE", "reason": ""});
        live["broker"] = json!({"status": "CONNECTED", "name": "FYERS", "reason": ""});
        apply_remote_sections(&mut state, &live, false);
        assert_eq!(state.session, vayren_domain::live::SessionStatus::Running);
        assert_eq!(state.broker.connected, Some(true));
        // Without a granted role the controls stay inert even while RUNNING.
        assert!(!state.bridge_wired);
        assert!(!state.can_start());
    }

    #[test]
    fn a_trade_role_makes_the_controls_live_and_a_read_role_does_not() {
        // THE regression: the packaged remote client used to force
        // `bridge_wired = false`, which made `can_start()` permanently false,
        // which disabled the START button — so the control could never be
        // pressed and never reported why. Authority now comes from the
        // gateway's own granted role.
        let mut state = init_remote_state();
        apply_role(&mut state, "trade");
        assert!(state.remote_commands_allowed);
        assert!(state.bridge_wired);

        let mut read_only = init_remote_state();
        apply_role(&mut read_only, "read");
        assert!(!read_only.remote_commands_allowed);
        assert!(
            !read_only.bridge_wired,
            "a read credential must leave trading controls inert"
        );
        assert!(
            read_only.action_note.is_some(),
            "read-only must explain itself instead of looking broken"
        );

        // An unknown/absent role fails closed.
        let mut unknown = init_remote_state();
        apply_role(&mut unknown, "");
        assert!(!unknown.bridge_wired);
    }

    #[test]
    fn granted_role_survives_later_snapshots() {
        let mut state = init_remote_state();
        apply_role(&mut state, "trade");
        apply_remote_sections(&mut state, &sections(), false);
        assert!(
            state.bridge_wired,
            "a later snapshot must not revoke the granted role"
        );
        // …and readiness stays fail-closed on the backend's own verdict.
        assert!(!state.can_start(), "backend still reports blockers");
        assert!(
            state.start_clickable(),
            "the operator can press and get the exact reason"
        );
    }

    #[test]
    fn resync_replaces_tables_and_clears_the_gappy_tail() {
        let mut state = init_remote_state();
        apply_remote_sections(&mut state, &sections(), false);
        push_remote_event(&mut state, "FILL", &json!({"detail": "FILLED 1 @ 100"}));
        assert_eq!(state.events.len(), 1);
        apply_remote_sections(&mut state, &sections(), true);
        assert!(state.events.is_empty());
    }

    #[test]
    fn event_tail_is_bounded_and_oldest_first_out() {
        let mut state = init_remote_state();
        for seq in 0..(REMOTE_EVENT_CAP + 40) {
            push_remote_event(
                &mut state,
                "MARKET_TICK",
                &json!({"detail": format!("tick {seq}")}),
            );
        }
        assert_eq!(state.events.len(), REMOTE_EVENT_CAP);
        assert!(state
            .events
            .first()
            .expect("tail")
            .event
            .contains("tick 40"));
        assert!(state.events.iter().all(|e| e.category == "MARKET_TICK"));
    }

    #[test]
    fn pre_snapshot_state_claims_no_link_or_funds() {
        let state = init_remote_state();
        assert!(!state.bridge_wired);
        assert!(!state.has_valid_capital());
        assert_eq!(state.risk_status, RiskStatus::NotReady);
        assert_eq!(state.websocket.status, "CONNECTING");
        assert_eq!(state.market_data.status, "CONNECTING");
        assert_eq!(state.broker.connected, None);
        // START is inert with honest feedback, queuing nothing.
        let mut state = state;
        state.start();
        assert!(state.host_actions.is_empty());
        assert_eq!(
            state.action_note.as_deref(),
            Some("No execution backend attached.")
        );
    }

    #[test]
    fn link_notes_track_reconnects_without_touching_server_facts() {
        let mut state = init_remote_state();
        mark_link(&mut state, "RECONNECTING", "link lost — retrying in 2s", 3);
        assert_eq!(state.websocket.status, "RECONNECTING");
        assert_eq!(state.websocket.reconnect_count, 3);
        assert!(state.websocket.last_error.contains("retrying"));
        // Server snapshot lands: facts own the status from here on.
        apply_remote_sections(&mut state, &sections(), false);
        mark_link(&mut state, "RECONNECTING", "link lost — retrying in 4s", 4);
        assert_eq!(state.websocket.status, "CONNECTED");
        assert_eq!(state.websocket.reconnect_count, 4);
    }

    #[test]
    fn bootstrap_labels_walk_connecting_to_offline_without_server_facts() {
        let mut state = init_remote_state();
        assert_eq!(state.websocket.status, "CONNECTING");
        mark_link(&mut state, "AUTHENTICATING", "proving credential...", 0);
        assert_eq!(state.websocket.status, "AUTHENTICATING");
        assert!(state.websocket.last_error.contains("proving"));
        // No credential in the store: OFFLINE carries the one-time setup note.
        mark_link(
            &mut state,
            "OFFLINE",
            enrollment_note(Enrollment::Missing),
            1,
        );
        assert_eq!(state.websocket.status, "OFFLINE");
        assert_eq!(state.websocket.reconnect_count, 1);
        assert!(state.websocket.last_error.contains("vayren-remote"));
        // Labels never arm controls or invent funds.
        assert!(!state.bridge_wired);
        assert!(!state.has_valid_capital());
    }

    #[test]
    fn quotes_pnl_and_symbols_translate_to_model() {
        let mut state = init_remote_state();
        let payload = json!({
            "broker": {"name": "FYERS", "status": "CONNECTED"},
            "capital": {"capital_source": "broker", "broker_capital": 500000.0, "available": 500000.0},
            "quotes": [
                {
                    "symbol": "NSE:KAYNES",
                    "ltp": 4500.0,
                    "change_pct": 2.5,
                    "entry_price": 4450.0,
                    "stop_price": 4400.0,
                    "quantity": 10.0,
                    "planned_risk": 500.0
                }
            ],
            "pnl": {
                "realized": 1200.0,
                "unrealized": 300.0,
                "exposure": 45000.0,
                "orders": 2,
                "fills": 1,
                "wins": 1,
                "losses": 0
            },
            "available_strategies": ["OBR C1C4"],
            "available_symbols": ["NSE:KAYNES", "NSE:DREDGECORP"]
        });
        apply_remote_sections(&mut state, &payload, false);
        assert_eq!(state.watchlist_rows.len(), 1);
        assert_eq!(state.watchlist_rows[0].symbol, "NSE:KAYNES");
        assert_eq!(state.watchlist_rows[0].ltp, Some(4500.0));
        assert_eq!(state.pnl.realized, Some(1200.0));
        assert_eq!(state.pnl.unrealized, Some(300.0));
        assert_eq!(state.strategies, vec!["OBR C1C4".to_string()]);
        assert_eq!(state.symbols.len(), 2);
    }

    #[test]
    fn gateway_facts_track_remote_connection_lifecycle() {
        let mut state = init_remote_state();
        assert_eq!(state.gateway.status, "CONNECTING");
        assert!(!state.gateway.connected);
        assert_eq!(state.gateway.last_error, "dialling private gateway…");

        // Authenticating
        mark_link(&mut state, "AUTHENTICATING", "proving credential...", 0);
        assert_eq!(state.gateway.status, "AUTHENTICATING");
        assert!(!state.gateway.connected);

        // Connected
        mark_link(&mut state, "CONNECTED", "role client", 0);
        assert_eq!(state.gateway.status, "CONNECTED");
        assert!(state.gateway.connected);
        assert!(state.gateway.last_error.is_empty());

        // Snapshot arrives
        apply_remote_sections(&mut state, &sections(), false);
        assert_eq!(state.gateway.status, "CONNECTED");
        assert!(state.gateway.connected);

        // Reconnecting
        mark_link(&mut state, "RECONNECTING", "link lost — retrying in 2s", 1);
        assert_eq!(state.gateway.status, "RECONNECTING");
        assert!(!state.gateway.connected);
        assert_eq!(state.gateway.reconnect_count, 1);
        assert_eq!(state.gateway.last_error, "link lost — retrying in 2s");
    }

    #[test]
    fn remote_mode_fails_closed_without_silent_local_fallback() {
        let mut state = init_remote_state();

        // 1. Missing credential: held OFFLINE with enrollment guidance, never falling back to local
        mark_link(
            &mut state,
            "OFFLINE",
            enrollment_note(Enrollment::Missing),
            1,
        );
        assert_eq!(state.gateway.status, "OFFLINE");
        assert!(!state.gateway.connected);
        assert!(
            !state.bridge_wired,
            "no proven credential means no command capability"
        );
        assert!(
            !state.can_start(),
            "START must remain blocked without a proven credential"
        );
        assert!(
            !state.start_clickable(),
            "and must not offer a click that could never be delivered"
        );

        // 2. Transport down / gateway failure: held OFFLINE with retry timetable
        mark_link(&mut state, "OFFLINE", "transport down — retrying in 4s", 2);
        assert_eq!(state.gateway.status, "OFFLINE");
        assert_eq!(state.gateway.reconnect_count, 2);
        assert!(!state.gateway.connected);
        assert!(
            !state.bridge_wired,
            "a dropped link cannot deliver a command"
        );

        // 3. User attempts start while offline: rejected with honest notice, never executes locally
        state.start();
        assert!(
            state.host_actions.is_empty(),
            "No host actions queued while offline"
        );
        assert_eq!(
            state.action_note.as_deref(),
            Some("No execution backend attached.")
        );
    }

    fn authoritative_sections() -> serde_json::Value {
        json!({
            "broker": {"status": "CONNECTED", "name": "Fyers",
                "reason": "session expired — invalid token", "connected": true,
                "account_id": "", "last_sync": ""},
            "broker_session": {"broker_id": "fyers", "state": "LOGIN_REQUIRED",
                "reason": "session expired — invalid token",
                "next_action": "Complete FYERS login from SYSTEM → BROKERS",
                "account_id": "", "version": 7},
            "brokers": [{"id": "fyers", "name": "Fyers", "display_name": "FYERS",
                "configured": true,
                "credential_schema": [
                    {"key": "app_id", "label": "App ID", "secret": false, "required": true},
                    {"key": "secret", "label": "Secret", "secret": true, "required": true}
                ],
                "session": {"broker_id": "fyers", "state": "LOGIN_REQUIRED", "version": 7}}],
        })
    }

    #[test]
    fn authority_prefers_top_level_session_over_legacy_flag() {
        // The reported split: legacy says connected=true while the unified
        // session says expired. Both screens must read the session.
        let auth = authoritative_broker(&authoritative_sections()).expect("authority");
        assert_eq!(auth.broker_id, "fyers");
        assert_eq!(auth.display_name, "FYERS");
        let verdict = session_verdict(&auth);
        assert!(!verdict.connected);
        assert!(!verdict.failed);
        assert_eq!(verdict.pill_label, "Login Required");
        assert_eq!(
            verdict.status_message,
            "Complete FYERS login from SYSTEM → BROKERS"
        );
    }

    #[test]
    fn authority_falls_back_to_matching_row_session() {
        let mut sections = authoritative_sections();
        sections.as_object_mut().unwrap().remove("broker_session");
        let auth = authoritative_broker(&sections).expect("row authority");
        assert_eq!(auth.broker_id, "fyers");
        assert_eq!(
            auth.field_keys,
            vec!["app_id".to_string(), "secret".to_string()]
        );
        assert_eq!(auth.field_secret, vec![false, true]);
        assert!(auth.fields_configured);
        assert_eq!(session_verdict(&auth).pill_label, "Login Required");
    }

    #[test]
    fn authority_absent_without_any_session() {
        assert!(authoritative_broker(&sections()).is_none());
    }

    #[test]
    fn verdict_covers_the_session_vocabulary() {
        let base = authoritative_broker(&authoritative_sections()).expect("authority");
        let with_state = |state: &str| {
            let mut auth = base.clone();
            auth.session["state"] = serde_json::Value::String(state.to_string());
            session_verdict(&auth)
        };
        let connected = with_state("CONNECTED");
        assert!(connected.connected && !connected.failed);
        assert_eq!(connected.pill_label, "Connected");
        let degraded = with_state("DEGRADED");
        assert!(degraded.connected && !degraded.failed);
        for s in [
            "CHECKING",
            "CONNECTING",
            "VERIFYING",
            "RECONNECTING",
            "RATE_LIMITED",
        ] {
            let v = with_state(s);
            assert!(!v.connected && !v.failed && v.in_progress, "{s}");
            assert_eq!(v.pill_label, "Connecting…");
        }
        let expired = with_state("LOGIN_REQUIRED");
        assert!(!expired.connected && !expired.failed);
        let down = with_state("DISCONNECTED");
        assert!(!down.connected && !down.failed);
        assert_eq!(down.pill_label, "Not Connected");
        let broken = with_state("ERROR");
        assert!(!broken.connected && broken.failed);
        assert_eq!(broken.pill_label, "Connection Failed");
        let unknown = with_state("UNKNOWN");
        assert!(!unknown.connected && unknown.failed);
    }

    #[test]
    fn live_translation_follows_the_authoritative_session() {
        let mut state = init_remote_state();
        apply_remote_sections(&mut state, &authoritative_sections(), false);
        // Legacy flag claims connected=true; the session overrules it.
        assert_eq!(state.broker.connected, Some(false));
        assert_eq!(state.broker.status, "LOGIN_REQUIRED");
        assert!(state.broker.reason.contains("session expired"));
    }

    #[test]
    fn zip_credentials_skips_blanks_and_vault_sentinels() {
        let keys = [
            "app_id".to_string(),
            "secret".to_string(),
            "pin".to_string(),
        ];
        let values = vec!["APP1".to_string(), "••••••••".to_string(), "".to_string()];
        let zipped = zip_broker_credentials(&keys, &values);
        assert_eq!(zipped, json!({"app_id": "APP1"}));
        let empty =
            zip_broker_credentials(&keys, &["".to_string(), "".to_string(), "".to_string()]);
        assert_eq!(empty, json!({}));
    }

    #[test]
    fn system_and_live_agree_on_the_reported_mismatch_shape() {
        // Exact shape from the field report: the legacy section claims
        // connected/CONNECTED while the unified session says the saved
        // token is expired. Both screens must refuse success AND agree
        // with each other — never SYSTEM green beside LIVE red.
        let sections = json!({
            "broker": {"status": "CONNECTED", "name": "Fyers",
                "reason": "session expired — invalid token", "connected": true,
                "account_id": "AB12", "last_sync": ""},
            "broker_session": {"broker_id": "fyers", "state": "LOGIN_REQUIRED",
                "auth_valid": false,
                "reason": "session expired — invalid token",
                "next_action": "Complete FYERS login from SYSTEM → BROKERS",
                "account_id": "", "version": 7},
            "brokers": [{"id": "fyers", "name": "Fyers", "display_name": "FYERS",
                "connected": true, "configured": true, "credential_schema": [],
                "session": {"broker_id": "fyers", "state": "LOGIN_REQUIRED",
                    "auth_valid": false, "version": 7}}],
        });
        let auth = authoritative_broker(&sections).expect("authority");
        let verdict = session_verdict(&auth);
        assert!(!verdict.connected, "SYSTEM must not show success");
        assert_eq!(verdict.pill_label, "Login Required");
        let mut state = init_remote_state();
        apply_remote_sections(&mut state, &sections, false);
        assert_eq!(
            state.broker.connected,
            Some(false),
            "LIVE must not show success"
        );
        assert_eq!(state.broker.status, "LOGIN_REQUIRED");
        assert_eq!(
            state.broker.connected,
            Some(verdict.connected),
            "SYSTEM and LIVE must agree with each other"
        );
    }
}
