//! Schema-v1 wire contract mirror (remote gateway ↔ client).
//!
//! Every message is one JSON object over one WebSocket text frame:
//! `{"type":..,"v":1,"ts":..,"seq":..,"request_id":..,"payload":{..}}`.
//! Nothing broker-specific travels here — clients only see
//! VAYREN-normalized shapes. Validation mirrors the backend
//! (`src/remote/protocol.py`): unknown types, wrong versions, oversized or
//! non-object payloads are rejected, never misparsed.

use serde_json::Value;
use std::fmt;

/// Current wire schema this client speaks.
pub const SCHEMA_VERSION: u32 = 1;

/// Largest single message accepted (1 MiB — bounds memory per connection).
pub const MAX_MESSAGE_BYTES: usize = 1_000_000;

/// Client → server categories.
pub const CLIENT_MESSAGE_TYPES: &[&str] = &["hello", "ping", "subscribe", "unsubscribe", "command"];

/// Server → client categories.
pub const SERVER_MESSAGE_TYPES: &[&str] = &[
    "welcome",
    "pong",
    "snapshot",
    "state_update",
    "event",
    "command_result",
    "error",
];

/// Machine-readable error codes the gateway may send.
pub const ERROR_CODES: &[&str] = &[
    "NOT_AUTHENTICATED",
    "AUTH_FAILED",
    "NOT_AUTHORIZED",
    "BAD_MESSAGE",
    "UNSUPPORTED_VERSION",
    "UNKNOWN_COMMAND",
    "COMMAND_REJECTED",
    "RATE_LIMITED",
    "SERVER_ERROR",
];

/// A rejected wire message — carries the machine-readable error code.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProtocolError {
    /// One of [`ERROR_CODES`] (`BAD_MESSAGE` when the gateway sent garbage).
    pub code: String,
    /// Human-readable detail (safe to display; never a secret).
    pub detail: String,
}

impl ProtocolError {
    pub fn new(code: impl Into<String>, detail: impl Into<String>) -> Self {
        let code = code.into();
        let code = if ERROR_CODES.contains(&code.as_str()) {
            code
        } else {
            "BAD_MESSAGE".to_string()
        };
        Self {
            code,
            detail: detail.into(),
        }
    }
}

impl fmt::Display for ProtocolError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "remote protocol [{}]: {}", self.code, self.detail)
    }
}

impl std::error::Error for ProtocolError {}

/// One validated client → server message, ready to send.
#[derive(Debug, Clone)]
pub struct ClientEnvelope {
    pub msg_type: String,
    pub payload: Value,
    pub request_id: Option<String>,
}

impl ClientEnvelope {
    fn wire(&self, msg_type: &str) -> Value {
        let mut out = serde_json::json!({
            "type": msg_type,
            "v": SCHEMA_VERSION,
            "ts": chrono_stamp(),
            "payload": self.payload,
        });
        if let Some(id) = &self.request_id {
            out["request_id"] = Value::String(id.clone());
        }
        out
    }

    /// Serialize to the exact wire string (size-bounded).
    pub fn encode(&self) -> Result<String, ProtocolError> {
        if !CLIENT_MESSAGE_TYPES.contains(&self.msg_type.as_str()) {
            return Err(ProtocolError::new(
                "BAD_MESSAGE",
                format!("unknown client type '{}'", self.msg_type),
            ));
        }
        if matches!(self.msg_type.as_str(), "command")
            && self.request_id.as_deref().is_none_or(str::is_empty)
        {
            return Err(ProtocolError::new(
                "BAD_MESSAGE",
                "control commands require a 'request_id'",
            ));
        }
        let text = self.wire(&self.msg_type).to_string();
        if text.len() > MAX_MESSAGE_BYTES {
            return Err(ProtocolError::new(
                "BAD_MESSAGE",
                "message exceeds size limit",
            ));
        }
        Ok(text)
    }
}

/// Constructors for the five client message categories.
pub mod msg {
    use super::ClientEnvelope;
    use serde_json::{json, Value};

    /// First frame on every connection (proves the bearer token).
    pub fn hello(token: &str) -> ClientEnvelope {
        ClientEnvelope {
            msg_type: "hello".to_string(),
            payload: json!({"token": token}),
            request_id: None,
        }
    }

    /// Heartbeat (the gateway answers `pong`).
    pub fn ping() -> ClientEnvelope {
        ClientEnvelope {
            msg_type: "ping".to_string(),
            payload: json!({}),
            request_id: None,
        }
    }

    /// Narrow the stream; `last_seq` replays missed events or triggers a resync.
    pub fn subscribe(channels: &[&str], last_seq: Option<i64>) -> ClientEnvelope {
        let mut payload = json!({"channels": channels});
        if let Some(seq) = last_seq {
            payload["last_seq"] = Value::from(seq);
        }
        ClientEnvelope {
            msg_type: "subscribe".to_string(),
            payload,
            request_id: None,
        }
    }

    /// Leave channels (connection-local; no idempotency needed).
    pub fn unsubscribe(channels: &[&str]) -> ClientEnvelope {
        ClientEnvelope {
            msg_type: "unsubscribe".to_string(),
            payload: json!({"channels": channels}),
            request_id: None,
        }
    }

    /// State-changing command. `request_id` is REQUIRED: retries reuse the
    /// SAME id so the gateway replays the cached result instead of acting
    /// twice. This client never sends trading commands from the UI; the path
    /// exists so the protocol contract is complete and testable.
    pub fn command(action: &str, params: Value, request_id: &str) -> ClientEnvelope {
        ClientEnvelope {
            msg_type: "command".to_string(),
            payload: json!({"action": action, "params": params}),
            request_id: Some(request_id.to_string()),
        }
    }
}

/// One validated server → client message.
#[derive(Debug, Clone)]
pub struct ServerMessage {
    /// One of [`SERVER_MESSAGE_TYPES`].
    pub msg_type: String,
    /// Event/state ordering counter (present on `event`/`state_update`).
    pub seq: Option<i64>,
    /// Correlation id echoed on `command_result`/`error`.
    pub request_id: Option<String>,
    /// Message body (schema per category, see the gateway contract §5.11).
    pub payload: Value,
}

/// Parse + validate one inbound text frame.
pub fn decode_server(text: &str) -> Result<ServerMessage, ProtocolError> {
    if text.len() > MAX_MESSAGE_BYTES {
        return Err(ProtocolError::new(
            "BAD_MESSAGE",
            "message exceeds size limit",
        ));
    }
    let raw: Value = serde_json::from_str(text)
        .map_err(|err| ProtocolError::new("BAD_MESSAGE", format!("not valid JSON: {err}")))?;
    let obj = raw
        .as_object()
        .ok_or_else(|| ProtocolError::new("BAD_MESSAGE", "message must be a JSON object"))?;
    let msg_type = obj
        .get("type")
        .and_then(Value::as_str)
        .ok_or_else(|| ProtocolError::new("BAD_MESSAGE", "message needs a string 'type'"))?;
    if !SERVER_MESSAGE_TYPES.contains(&msg_type) {
        return Err(ProtocolError::new(
            "BAD_MESSAGE",
            format!("unknown message type '{msg_type}'"),
        ));
    }
    match obj.get("v").and_then(Value::as_u64) {
        Some(1) => {}
        other => {
            return Err(ProtocolError::new(
                "UNSUPPORTED_VERSION",
                format!("unsupported schema version {other:?}"),
            ));
        }
    }
    let payload = obj.get("payload").cloned().unwrap_or(Value::Null);
    if !payload.is_object() {
        return Err(ProtocolError::new(
            "BAD_MESSAGE",
            "'payload' must be an object",
        ));
    }
    let seq = obj.get("seq").and_then(Value::as_i64);
    if obj.contains_key("seq") && seq.is_none_or(|s| s < 0) {
        return Err(ProtocolError::new(
            "BAD_MESSAGE",
            "'seq' must be a non-negative integer",
        ));
    }
    let request_id = obj
        .get("request_id")
        .and_then(Value::as_str)
        .map(str::to_string);
    Ok(ServerMessage {
        msg_type: msg_type.to_string(),
        seq,
        request_id,
        payload,
    })
}

/// UTC timestamp for outbound envelopes (no `chrono` dependency — the
/// gateway only needs an opaque string here, not clock discipline).
fn chrono_stamp() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    format!("{secs}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hello_encodes_with_token() {
        let text = msg::hello("tok").encode().unwrap();
        let v: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(v["type"], "hello");
        assert_eq!(v["v"], 1);
        assert_eq!(v["payload"]["token"], "tok");
    }

    #[test]
    fn command_requires_request_id() {
        let mut env = ClientEnvelope {
            msg_type: "command".to_string(),
            payload: serde_json::json!({"action": "stop"}),
            request_id: None,
        };
        assert!(env.encode().is_err());
        env.request_id = Some("r-1".to_string());
        assert!(env.encode().is_ok());
    }

    #[test]
    fn decode_rejects_garbage_and_versions() {
        assert!(decode_server("nope").is_err());
        assert!(
            decode_server(r#"{"type":"snapshot","v":99,"payload":{}}"#)
                .unwrap_err()
                .code
                == "UNSUPPORTED_VERSION"
        );
        assert!(decode_server(r#"{"type":"snapshot","v":1,"payload":{},"seq":-1}"#).is_err());
        let ok =
            decode_server(r#"{"type":"event","v":1,"seq":7,"payload":{"name":"FILL"}}"#).unwrap();
        assert_eq!((ok.msg_type.as_str(), ok.seq), ("event", Some(7)));
    }
}
