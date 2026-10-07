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
//! - The shell NEVER sends a command: [`RemoteClient::send_command`] has no
//!   call site in this crate. START/LIVE/BUY/SELL/CANCEL/MODIFY cannot be
//!   emitted from the UI. [`LiveState::bridge_wired`] stays `false`, so the
//!   model itself keeps every trading control inert (`can_start` is false and
//!   pressing START only records "No execution backend attached.").
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
        translated.insert(
            "strategy".to_string(),
            serde_json::json!({"id": "", "status": strategy_status,
                "mode": text_at(sections, "strategy", "mode")}),
        );
    }
    let reason = text_at(sections, "strategy", "reason");
    if !reason.is_empty() {
        translated.insert(
            "status_reason".to_string(),
            serde_json::Value::String(reason),
        );
    }

    if !section(sections, "broker").is_null() {
        let status = text_at(sections, "broker", "status");
        translated.insert(
            "broker".to_string(),
            serde_json::json!({
                "name": text_at(sections, "broker", "name"),
                "status": status,
                "reason": text_at(sections, "broker", "reason"),
                "connected": status == "CONNECTED",
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
    if !section(sections, "system").is_null() {
        let ws = text_at(sections, "system", "websocket");
        if !ws.is_empty() {
            translated.insert("websocket".to_string(), serde_json::json!({"status": ws}));
        }
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
    if let Some(stf) = sections.get("selected_timeframe").and_then(|v| v.as_str()) {
        translated.insert(
            "selected_timeframe".to_string(),
            serde_json::Value::String(stf.to_string()),
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
        // The backend's own start verdict, shown verbatim; `bridge_wired`
        // stays false so START can never enable from the remote client.
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
    // Read-only by construction: the snapshot must never flip the model
    // into a commanding state, whatever the backend reported.
    state.bridge_wired = false;
    if !state.host_mode {
        state.host_mode = true;
    }
    if !state.has_valid_capital() {
        state.risk_status = RiskStatus::NotReady;
    }

    // Order-stream fact rides as a readiness gate (extra gates never affect
    // the five named venue gates; START stays disabled via bridge_wired).
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
/// snapshot lands. `bridge_wired` stays false (read-only controls).
pub fn init_remote_state() -> LiveState {
    let mut state = LiveState::default();
    state.host_mode = true;
    state.bridge_wired = false;
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
}

/// One UI-thread arrival from the pump thread.
#[derive(Debug)]
pub enum RemoteUiUpdate {
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
/// [`spawn_remote_pump`] — which owns all later reconnects — before this
/// thread exits. On failure the UI holds OFFLINE with the enrollment or
/// transport note while the loop retries with backoff, so a credential
/// provisioned after first launch (or a gateway restart) connects with zero
/// restart and zero manual step. This function never sends a command.
pub fn spawn_remote_bootstrap(
    url: String,
    tx: mpsc::Sender<RemoteUiUpdate>,
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
                    let _ = tx.send(RemoteUiUpdate::Link {
                        label: "CONNECTED".to_string(),
                        detail: format!("role {role}"),
                        reconnects,
                    });
                    let _ = tx.send(RemoteUiUpdate::Snapshot {
                        sections,
                        resync: false,
                    });
                    spawn_remote_pump(client, url, tx);
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
pub fn spawn_remote_pump(
    mut client: RemoteClient,
    url: String,
    tx: mpsc::Sender<RemoteUiUpdate>,
) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        let mut backoff = Backoff::new(Duration::from_secs(1), Duration::from_secs(30));
        let mut reconnects: usize = 0;
        let pump = |client: &mut RemoteClient, tx: &mpsc::Sender<RemoteUiUpdate>| {
            loop {
                match client.next(PUMP_IDLE_TIMEOUT) {
                    Ok(ClientEvent::Snapshot { sections, resync }) => {
                        let _ = tx.send(RemoteUiUpdate::Snapshot { sections, resync });
                    }
                    Ok(ClientEvent::StateUpdate { sections, .. }) => {
                        let _ = tx.send(RemoteUiUpdate::StateUpdate { sections });
                    }
                    Ok(ClientEvent::StreamEvent { name, payload, .. }) => {
                        let _ = tx.send(RemoteUiUpdate::StreamEvent { name, payload });
                    }
                    Ok(ClientEvent::Pong) => {}
                    Ok(ClientEvent::CommandResult { .. }) => {
                        // The shell never sends commands; a result here
                        // would mean a protocol violation — log, don't act.
                        eprintln!("remote pump: unexpected command_result (none was sent)");
                    }
                    Ok(ClientEvent::GatewayError { code, detail, .. }) => {
                        eprintln!("remote pump: gateway error [{code}]: {detail}");
                    }
                    Ok(ClientEvent::Closed) => break,
                    Ok(ClientEvent::Welcome { .. }) => {}
                    Err(ClientError::Timeout) => {
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
        pump(&mut client, &tx);
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
                Ok((mut fresh, _, _)) => {
                    drop(token);
                    backoff.reset();
                    if fresh.subscribe(&[], last_seq).is_err() {
                        continue;
                    }
                    client = fresh;
                    let _ = tx.send(RemoteUiUpdate::Link {
                        label: "CONNECTED".to_string(),
                        detail: "resumed".to_string(),
                        reconnects,
                    });
                    pump(&mut client, &tx);
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
        // Read-only: the snapshot can never arm the commanding path.
        assert!(!state.bridge_wired);
        assert!(!state.can_start());
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
        // Even RUNNING never enables local commanding from the remote UI.
        assert!(!state.bridge_wired);
        assert!(!state.can_start());
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
}
