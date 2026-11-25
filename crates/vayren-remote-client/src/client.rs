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
use std::net::{SocketAddr, TcpStream, ToSocketAddrs};
use std::time::{Duration, Instant};
use tungstenite::{stream::MaybeTlsStream, Message, WebSocket};

/// Default deadline for the hello → welcome → snapshot handshake.
pub const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(15);
/// How long one `next()` waits for a frame before reporting idle.
pub const IDLE_TIMEOUT: Duration = Duration::from_secs(30);

/// Connection lifecycle (mirrors the gateway's management vocabulary).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConnectionState {
    Connecting,
    Authenticating,
    Connected,
    Reconnecting,
    Stale,
    Offline,
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
        stale: bool,
        stale_reason: Option<String>,
        snapshot_age_ms: Option<f64>,
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
    /// Gateway accepted an async lifecycle command (immediate ack, before
    /// the backend START/STOP work finishes).
    CommandAccepted {
        request_id: Option<String>,
        payload: serde_json::Value,
    },
    /// One lifecycle progress stage for an accepted command.
    CommandProgress {
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

fn set_write_timeout(socket: &mut Socket, timeout: Option<Duration>) -> io::Result<()> {
    match socket.get_mut() {
        MaybeTlsStream::Plain(stream) => stream.set_write_timeout(timeout),
        MaybeTlsStream::Rustls(stream) => stream.get_mut().set_write_timeout(timeout),
        _ => Ok(()),
    }
}

fn map_handshake_err(err: tungstenite::Error) -> ClientError {
    match err {
        tungstenite::Error::Io(io_err)
            if io_err.kind() == io::ErrorKind::TimedOut
                || io_err.kind() == io::ErrorKind::WouldBlock =>
        {
            ClientError::Timeout
        }
        other => ClientError::Transport(other.to_string()),
    }
}

fn connect_socket_with_timeout(url: &str, deadline: Instant) -> Result<Socket, ClientError> {
    let uri: tungstenite::http::Uri = url
        .parse()
        .map_err(|err| ClientError::Transport(format!("invalid URL '{url}': {err}")))?;
    let scheme = uri.scheme_str().unwrap_or("");
    let default_port = match scheme {
        "wss" => 443,
        "ws" => 80,
        _ => {
            return Err(ClientError::Transport(format!(
                "unsupported scheme '{scheme}'"
            )))
        }
    };
    let host = uri
        .host()
        .ok_or_else(|| ClientError::Transport(format!("missing host in '{url}'")))?;
    let host_clean = host.trim_start_matches('[').trim_end_matches(']');
    let port = uri.port_u16().unwrap_or(default_port);
    let addrs: Vec<SocketAddr> = (host_clean, port)
        .to_socket_addrs()
        .map_err(|err| ClientError::Transport(format!("resolve {host_clean}:{port}: {err}")))?
        .collect();
    if addrs.is_empty() {
        return Err(ClientError::Transport(format!(
            "no addresses resolved for {host_clean}:{port}"
        )));
    }

    let mut last_err: Option<ClientError> = None;
    let total = addrs.len();
    for (idx, addr) in addrs.iter().enumerate() {
        let Some(remaining) = deadline.checked_duration_since(Instant::now()) else {
            return Err(last_err.unwrap_or(ClientError::Timeout));
        };
        let left = (total - idx) as u32;
        let slice = (remaining / left)
            .max(Duration::from_millis(250))
            .min(remaining);
        match TcpStream::connect_timeout(addr, slice) {
            Ok(stream) => {
                let _ = stream.set_nodelay(true);
                let rem_hs = deadline
                    .checked_duration_since(Instant::now())
                    .ok_or(ClientError::Timeout)?;
                stream
                    .set_read_timeout(Some(rem_hs))
                    .map_err(|err| ClientError::Transport(err.to_string()))?;
                stream
                    .set_write_timeout(Some(rem_hs))
                    .map_err(|err| ClientError::Transport(err.to_string()))?;
                return match tungstenite::client_tls(url, stream) {
                    Ok((socket, _response)) => Ok(socket),
                    Err(tungstenite::HandshakeError::Interrupted(mut mid)) => loop {
                        if deadline.checked_duration_since(Instant::now()).is_none() {
                            return Err(ClientError::Timeout);
                        }
                        match mid.handshake() {
                            Ok((socket, _)) => return Ok(socket),
                            Err(tungstenite::HandshakeError::Interrupted(next_mid)) => {
                                mid = next_mid;
                                std::thread::sleep(Duration::from_millis(5));
                            }
                            Err(tungstenite::HandshakeError::Failure(err)) => {
                                return Err(map_handshake_err(err));
                            }
                        }
                    },
                    Err(tungstenite::HandshakeError::Failure(err)) => Err(map_handshake_err(err)),
                };
            }
            Err(err) => {
                last_err = Some(if err.kind() == io::ErrorKind::TimedOut {
                    ClientError::Timeout
                } else {
                    ClientError::Transport(err.to_string())
                });
            }
        }
    }
    Err(last_err.unwrap_or(ClientError::Timeout))
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
    /// Role the gateway granted in `welcome` (`trade` / `read`). The shell
    /// reads this to decide whether a control command is even admissible, so
    /// a `read` credential shows an honest refusal instead of a dead button.
    pub role: String,
    /// Latest backend `health` payload received in a `pong` heartbeat.
    pub last_pong_health: Option<serde_json::Value>,
}

impl RemoteClient {
    /// Whether this connection may send control commands. Mirrors the
    /// gateway's own `RemoteRole.allows_command` (server-side check stays
    /// authoritative — this only keeps the UI from offering what will be
    /// refused).
    pub fn role_allows_commands(&self) -> bool {
        self.role == ROLE_TRADE
    }
}

/// Role string the gateway grants a credential permitted to command.
pub const ROLE_TRADE: &str = "trade";

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
        let deadline = Instant::now() + timeout;
        Self::check_url(url)?;
        ensure_tls_provider();
        let socket = connect_socket_with_timeout(url, deadline)?;
        let mut client = Self {
            socket,
            state: ConnectionState::Connecting,
            last_seq: None,
            role: String::new(),
            last_pong_health: None,
        };
        client.state = ConnectionState::Authenticating;
        let rem_hello = deadline
            .checked_duration_since(Instant::now())
            .ok_or(ClientError::Timeout)?
            .max(Duration::from_millis(1));
        set_write_timeout(&mut client.socket, Some(rem_hello))
            .map_err(|err| ClientError::Transport(err.to_string()))?;
        client.send_envelope(&msg::hello(token))?;

        let rem_welcome = deadline
            .checked_duration_since(Instant::now())
            .ok_or(ClientError::Timeout)?;
        let welcome = match client.await_type("welcome", rem_welcome) {
            Ok(ev) => ev,
            Err(err) => {
                client.state = if matches!(err, ClientError::Timeout) {
                    ConnectionState::Offline
                } else {
                    ConnectionState::Error
                };
                return Err(err);
            }
        };
        // Record the granted role BEFORE the socket is usable for commands:
        // the shell gates START/STOP on it, and an unparsed welcome must
        // fail closed (empty role ⇒ no commands).
        if let ClientEvent::Welcome { role, .. } = &welcome {
            client.role = role.clone();
        }
        // Receiving `welcome` means authentication succeeded, NOT snapshot sync:
        // stay Authenticating until an authoritative `snapshot` arrives.
        let rem_snapshot = deadline
            .checked_duration_since(Instant::now())
            .ok_or(ClientError::Timeout)?;
        let snapshot = match client.await_type("snapshot", rem_snapshot) {
            Ok(ev) => ev,
            Err(err) => {
                client.state = if matches!(err, ClientError::Timeout) {
                    ConnectionState::Offline
                } else {
                    ConnectionState::Error
                };
                return Err(err);
            }
        };
        client.state = match &snapshot {
            ClientEvent::Snapshot { stale: true, .. } => ConnectionState::Stale,
            _ => ConnectionState::Connected,
        };
        let _ = set_read_timeout(&mut client.socket, None);
        let _ = set_write_timeout(&mut client.socket, Some(HANDSHAKE_TIMEOUT));
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

    /// Pump one arrival (auto-answers pings, tracks `last_seq` within cumulative `idle`).
    pub fn next(&mut self, idle: Duration) -> Result<ClientEvent, ClientError> {
        let deadline = Instant::now() + idle;
        loop {
            let Some(remaining) = deadline.checked_duration_since(Instant::now()) else {
                return Err(ClientError::Timeout);
            };
            set_read_timeout(
                &mut self.socket,
                Some(remaining.max(Duration::from_millis(1))),
            )
            .map_err(|err| ClientError::Transport(err.to_string()))?;
            let text = match read_text(&mut self.socket)? {
                Some(text) => text,
                None => continue,
            };
            let message = protocol::decode_server(&text).map_err(ClientError::Protocol)?;
            if let Some(seq) = message.seq {
                self.last_seq = Some(self.last_seq.map_or(seq, |prev| prev.max(seq)));
            }
            if message.msg_type == "pong" {
                self.last_pong_health = message.payload.get("health").cloned();
            }
            let ev = classify(message);
            match &ev {
                ClientEvent::Snapshot { stale: true, .. } => {
                    self.state = ConnectionState::Stale;
                }
                ClientEvent::Snapshot { stale: false, .. } | ClientEvent::StateUpdate { .. } => {
                    self.state = ConnectionState::Connected;
                }
                ClientEvent::Closed => self.state = ConnectionState::Disconnected,
                _ => {}
            }
            return Ok(ev);
        }
    }

    /// Current lifecycle state (for status surfaces, never for logic).
    pub fn state(&self) -> ConnectionState {
        self.state
    }

    fn send_envelope(&mut self, envelope: &ClientEnvelope) -> Result<(), ClientError> {
        let text = envelope.encode().map_err(ClientError::Protocol)?;
        let _ = set_write_timeout(&mut self.socket, Some(HANDSHAKE_TIMEOUT));
        self.socket
            .send(Message::text(text))
            .map_err(|err| ClientError::Transport(err.to_string()))
    }

    fn await_type(&mut self, want: &str, timeout: Duration) -> Result<ClientEvent, ClientError> {
        let deadline = Instant::now() + timeout;
        loop {
            let Some(remaining) = deadline.checked_duration_since(Instant::now()) else {
                return Err(ClientError::Timeout);
            };
            set_read_timeout(
                &mut self.socket,
                Some(remaining.max(Duration::from_millis(1))),
            )
            .map_err(|err| ClientError::Transport(err.to_string()))?;
            let text = match read_text(&mut self.socket)? {
                Some(text) => text,
                // Control frames (WS Ping / Pong) must never abort the handshake.
                None => continue,
            };
            let message = protocol::decode_server(&text).map_err(ClientError::Protocol)?;
            if let Some(seq) = message.seq {
                self.last_seq = Some(self.last_seq.map_or(seq, |prev| prev.max(seq)));
            }
            if message.msg_type == "error" {
                let code = message
                    .payload
                    .get("code")
                    .and_then(|c| c.as_str())
                    .unwrap_or("SERVER_ERROR");
                let detail = message
                    .payload
                    .get("detail")
                    .and_then(|d| d.as_str())
                    .unwrap_or("");
                if matches!(code, "AUTH_FAILED" | "NOT_AUTHENTICATED" | "NOT_AUTHORIZED") {
                    return Err(ClientError::Auth(format!("{code}: {detail}")));
                }
                return Err(ClientError::Protocol(ProtocolError::new(code, detail)));
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
            stale: message
                .payload
                .get("stale")
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
            stale_reason: message
                .payload
                .get("stale_reason")
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
                .map(|s| s.to_string()),
            snapshot_age_ms: message
                .payload
                .get("snapshot_age_ms")
                .and_then(|v| v.as_f64()),
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
        "command_accepted" => ClientEvent::CommandAccepted {
            request_id: message.request_id,
            payload: message.payload,
        },
        "command_progress" => ClientEvent::CommandProgress {
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
