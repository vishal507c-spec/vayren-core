//! Schema-v1 gateway client — handshake, heartbeat, reconnect, resume.
//!
//! The client is transport + protocol ONLY: it never interprets trading
//! facts, never sends trading commands from any UI, and never stores the
//! token (the caller holds it for the `hello` frame, then drops it).
//!
//! Steady-state discipline per connection:
//! ```text
//! connect → hello → welcome → snapshot → subscribe/stream ↔ ping/pong
//!   → (drop) → resume with last_seq → replay | resync snapshot
//! ```
//! `wss://` URLs terminate TLS via rustls (webpki roots — the Let's Encrypt
//! chain the private endpoint serves). Plain `ws://` is accepted for
//! loopback and tunneled transports only.

use crate::protocol::{self, msg, ClientEnvelope, ProtocolError, ServerMessage};
use std::fmt;
use std::io;
use std::net::TcpStream;
use std::time::Duration;
use tungstenite::{stream::MaybeTlsStream, Message, WebSocket};

/// Default deadline for the hello → welcome → snapshot handshake.
pub const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(15);
/// How long one `next()` waits for a frame before reporting idle.
pub const IDLE_TIMEOUT: Duration = Duration::from_secs(30);

/// Connection lifecycle (mirrors the gateway's management vocabulary).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConnectionState {
    Connecting,
    Connected,
    Reconnecting,
    Disconnected,
    Error,
}

/// Client failure — transport, protocol, auth, timeout, or a clean close.
#[derive(Debug)]
pub enum ClientError {
    Transport(String),
    Protocol(ProtocolError),
    Auth(String),
    Timeout,
    Closed,
}

impl fmt::Display for ClientError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Transport(detail) => write!(f, "remote transport: {detail}"),
            Self::Protocol(err) => write!(f, "remote protocol: {err}"),
            Self::Auth(detail) => write!(f, "remote auth: {detail}"),
            Self::Timeout => write!(f, "remote timed out waiting for the gateway"),
            Self::Closed => write!(f, "remote connection closed"),
        }
    }
}

impl std::error::Error for ClientError {}

/// One meaningful arrival on the stream (frames already sorted out).
#[derive(Debug, Clone)]
pub enum ClientEvent {
    /// Authenticated session facts (role gates what the client may send).
    Welcome { connection_id: String, role: String },
    /// Full state — initial subscribe answer, or `resync: true` after a gap.
    Snapshot {
        sections: serde_json::Value,
        resync: bool,
    },
    /// Changed sections since the last snapshot (ordered by `seq`).
    StateUpdate {
        sections: serde_json::Value,
        seq: i64,
    },
    /// One backend fact, ordered by `seq` (dedupe/resume key).
    StreamEvent {
        name: String,
        seq: i64,
        payload: serde_json::Value,
    },
    /// Heartbeat answer.
    Pong,
    /// Gateway answer to a command (echoes `request_id`).
    CommandResult {
        request_id: Option<String>,
        payload: serde_json::Value,
    },
    /// Gateway refusal (auth, authorization, unknown command, ...).
    GatewayError {
        code: String,
        detail: String,
        request_id: Option<String>,
    },
    /// The socket closed cleanly underneath.
    Closed,
}

type Socket = WebSocket<MaybeTlsStream<TcpStream>>;

fn ensure_tls_provider() {
    // tungstenite builds its rustls `ClientConfig` without naming a crypto
    // provider; with no unambiguous default that PANICS at connect time.
    // Pin `ring` process-wide for `wss://` (idempotent — an already-installed
    // host default wins). Verification is unaffected: webpki roots + full
    // hostname checks still apply to every handshake.
    let _ = rustls::crypto::ring::default_provider().install_default();
}

fn set_read_timeout(socket: &mut Socket, timeout: Option<Duration>) -> io::Result<()> {
    match socket.get_mut() {
        MaybeTlsStream::Plain(stream) => stream.set_read_timeout(timeout),
        MaybeTlsStream::Rustls(stream) => stream.get_mut().set_read_timeout(timeout),
        _ => Ok(()),
    }
}

fn read_text(socket: &mut Socket) -> Result<Option<String>, ClientError> {
    match socket.read() {
        Ok(Message::Text(text)) => Ok(Some(text.to_string())),
        Ok(Message::Binary(_)) => Err(ClientError::Protocol(ProtocolError::new(
            "BAD_MESSAGE",
            "binary frames are not part of the contract",
        ))),
        Ok(Message::Ping(payload)) => {
            socket
                .send(Message::Pong(payload))
                .map_err(|err| ClientError::Transport(err.to_string()))?;
            Ok(None)
        }
        Ok(Message::Pong(_)) => Ok(None),
        Ok(Message::Close(_)) => Err(ClientError::Closed),
        // Unreachable via `read()` (raw frames never surface there) — kept
        // for exhaustiveness so a future tungstenite stops compiling loudly.
        Ok(Message::Frame(_)) => Err(ClientError::Protocol(ProtocolError::new(
            "BAD_MESSAGE",
            "unexpected raw frame",
        ))),
        Err(tungstenite::Error::Io(err))
            if err.kind() == io::ErrorKind::TimedOut || err.kind() == io::ErrorKind::WouldBlock =>
        {
            Err(ClientError::Timeout)
        }
        Err(err) => Err(ClientError::Transport(err.to_string())),
    }
}

/// Capped exponential backoff for reconnect loops (pure — unit-tested).
#[derive(Debug, Clone)]
pub struct Backoff {
    base: Duration,
    cap: Duration,
    attempts: u32,
}

impl Backoff {
    pub fn new(base: Duration, cap: Duration) -> Self {
        Self {
            base,
            cap,
            attempts: 0,
        }
    }

    /// Next wait (1×, 2×, 4× … base, capped). Resets on success via [`Backoff::reset`].
    pub fn next_wait(&mut self) -> Duration {
        let shift = self.attempts.min(10);
        self.attempts = self.attempts.saturating_add(1);
        let wait = self.base.saturating_mul(1 << shift);
        wait.min(self.cap)
    }

    pub fn reset(&mut self) {
        self.attempts = 0;
    }
}

/// The gateway connection. Owns the socket and the resume cursor.
pub struct RemoteClient {
    socket: Socket,
    state: ConnectionState,
    /// Highest `seq` delivered — the resume cursor for reconnects.
    pub last_seq: Option<i64>,
}

impl RemoteClient {
    /// Reject anything that is not a WebSocket URL before a socket opens.
    pub fn check_url(url: &str) -> Result<(), ClientError> {
        if url.starts_with("ws://") || url.starts_with("wss://") {
            Ok(())
        } else {
            Err(ClientError::Transport(format!("not a ws(s) URL: '{url}'")))
        }
    }

    /// Connect, prove the token FIRST (`hello`), and return once `welcome`
    /// + the initial `snapshot` have arrived (deadline-bounded).
    pub fn connect(
        url: &str,
        token: &str,
        timeout: Duration,
    ) -> Result<(Self, ClientEvent, ClientEvent), ClientError> {
        Self::check_url(url)?;
        ensure_tls_provider();
        let (mut socket, _response) =
            tungstenite::connect(url).map_err(|err| ClientError::Transport(err.to_string()))?;
        set_read_timeout(&mut socket, Some(timeout))
            .map_err(|err| ClientError::Transport(err.to_string()))?;
        let mut client = Self {
            socket,
            state: ConnectionState::Connecting,
            last_seq: None,
        };
        client.send_envelope(&msg::hello(token))?;
        let welcome = client.await_type("welcome", timeout)?;
        let snapshot = client.await_type("snapshot", timeout)?;
        client.state = ConnectionState::Connected;
        set_read_timeout(&mut client.socket, None)
            .map_err(|err| ClientError::Transport(err.to_string()))?;
        Ok((client, welcome, snapshot))
    }

    /// Reconnect after a drop and resume with `last_seq`: the gateway
    /// replays missed events, or answers a `resync:true` snapshot.
    pub fn resume(
        url: &str,
        token: &str,
        last_seq: Option<i64>,
        timeout: Duration,
    ) -> Result<(Self, ClientEvent, ClientEvent), ClientError> {
        let (mut client, welcome, snapshot) = Self::connect(url, token, timeout)?;
        client.state = ConnectionState::Reconnecting;
        client.send_envelope(&msg::subscribe(&[], last_seq))?;
        client.state = ConnectionState::Connected;
        let _ = snapshot;
        // The resume answer arrives on the pump: snapshot (fresh or resync)
        // followed by replayed events. Return the handshake pair; the caller
        // keeps pumping `next()` for the rest.
        let resumed = client.next(IDLE_TIMEOUT)?;
        Ok((client, welcome, resumed))
    }

    /// Narrow the stream (read-only; safe on a live gateway).
    pub fn subscribe(
        &mut self,
        channels: &[&str],
        last_seq: Option<i64>,
    ) -> Result<(), ClientError> {
        self.send_envelope(&msg::subscribe(channels, last_seq))
    }

    /// Heartbeat (the gateway answers `pong`, surfaced by [`RemoteClient::next`]).
    pub fn ping(&mut self) -> Result<(), ClientError> {
        self.send_envelope(&msg::ping())
    }

    /// Send a state-changing command with an explicit idempotency id.
    /// Retries MUST reuse the same `request_id` — the gateway replays the
    /// cached result instead of acting twice. No UI path calls this today;
    /// the method exists so the contract (and its retry rule) stays tested.
    pub fn send_command(
        &mut self,
        action: &str,
        params: serde_json::Value,
        request_id: &str,
    ) -> Result<(), ClientError> {
        self.send_envelope(&msg::command(action, params, request_id))
    }

    /// Pump one arrival (auto-answers pings, tracks `last_seq`).
    pub fn next(&mut self, idle: Duration) -> Result<ClientEvent, ClientError> {
        set_read_timeout(&mut self.socket, Some(idle))
            .map_err(|err| ClientError::Transport(err.to_string()))?;
        loop {
            let text = match read_text(&mut self.socket)? {
                Some(text) => text,
                None => continue,
            };
            let message = protocol::decode_server(&text).map_err(ClientError::Protocol)?;
            if let Some(seq) = message.seq {
                self.last_seq = Some(self.last_seq.map_or(seq, |prev| prev.max(seq)));
            }
            return Ok(classify(message));
        }
    }

    /// Current lifecycle state (for status surfaces, never for logic).
    pub fn state(&self) -> ConnectionState {
        self.state
    }

    fn send_envelope(&mut self, envelope: &ClientEnvelope) -> Result<(), ClientError> {
        let text = envelope.encode().map_err(ClientError::Protocol)?;
        self.socket
            .send(Message::text(text))
            .map_err(|err| ClientError::Transport(err.to_string()))
    }

    fn await_type(&mut self, want: &str, _timeout: Duration) -> Result<ClientEvent, ClientError> {
        loop {
            let text = read_text(&mut self.socket)?.ok_or(ClientError::Closed)?;
            let message = protocol::decode_server(&text).map_err(ClientError::Protocol)?;
            if message.msg_type == "error" {
                let code = message
                    .payload
                    .get("code")
                    .and_then(|c| c.as_str())
                    .unwrap_or("SERVER_ERROR");
                return Err(ClientError::Auth(format!(
                    "{}: {}",
                    code,
                    message
                        .payload
                        .get("detail")
                        .and_then(|d| d.as_str())
                        .unwrap_or("")
                )));
            }
            if message.msg_type == want {
                return Ok(classify(message));
            }
        }
    }
}

fn classify(message: ServerMessage) -> ClientEvent {
    match message.msg_type.as_str() {
        "welcome" => ClientEvent::Welcome {
            connection_id: message
                .payload
                .get("connection_id")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            role: message
                .payload
                .get("role")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
        },
        "snapshot" => ClientEvent::Snapshot {
            sections: message
                .payload
                .get("snapshot")
                .cloned()
                .unwrap_or(serde_json::Value::Null),
            resync: message
                .payload
                .get("resync")
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
        },
        "state_update" => ClientEvent::StateUpdate {
            sections: message
                .payload
                .get("sections")
                .cloned()
                .unwrap_or(serde_json::Value::Null),
            seq: message.seq.unwrap_or(-1),
        },
        "event" => ClientEvent::StreamEvent {
            name: message
                .payload
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or("SYSTEM")
                .to_string(),
            seq: message.seq.unwrap_or(-1),
            payload: message.payload,
        },
        "pong" => ClientEvent::Pong,
        "command_result" => ClientEvent::CommandResult {
            request_id: message.request_id,
            payload: message.payload,
        },
        "error" => ClientEvent::GatewayError {
            code: message
                .payload
                .get("code")
                .and_then(|v| v.as_str())
                .unwrap_or("SERVER_ERROR")
                .to_string(),
            detail: message
                .payload
                .get("detail")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            request_id: message.request_id,
        },
        _ => ClientEvent::Closed,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backoff_caps_and_resets() {
        let mut backoff = Backoff::new(Duration::from_millis(100), Duration::from_millis(250));
        assert_eq!(backoff.next_wait(), Duration::from_millis(100));
        assert_eq!(backoff.next_wait(), Duration::from_millis(200));
        assert_eq!(backoff.next_wait(), Duration::from_millis(250));
        backoff.reset();
        assert_eq!(backoff.next_wait(), Duration::from_millis(100));
    }

    #[test]
    fn url_scheme_gate() {
        assert!(RemoteClient::check_url("wss://x.ts.net/vayren/v1").is_ok());
        assert!(RemoteClient::check_url("ws://127.0.0.1:8765/vayren/v1").is_ok());
        assert!(RemoteClient::check_url("https://x/vayren/v1").is_err());
        assert!(RemoteClient::check_url("notaurl").is_err());
    }
}
