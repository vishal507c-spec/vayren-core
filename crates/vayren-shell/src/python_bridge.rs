//! Python backend bridge — IPC via JSON over stdin/stdout.
//!
//! Spawns the headless Python backend process and communicates via newline-
//! delimited JSON. The bridge is fail-closed: any protocol violation or
//! backend crash is immediately visible (no silent fallback).
//!
//! Every blocking read is DEADLINE-BOUNDED. A plain `read_line` on a pipe
//! blocks forever if the child wedges (deadlocked backend, hung worker
//! thread, machine suspend), which froze the whole UI behind a permanent
//! "RUNNING". A pipe read cannot be interrupted portably, so the read runs on
//! its own thread and the caller waits on a channel with a timeout; on expiry
//! the WATCHDOG KILLS the child (closing the pipe, which unblocks the reader)
//! and returns an error. The child is never left running and the caller is
//! never left waiting.

use serde::{Deserialize, Serialize};
use std::fmt;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// Deadline for a plain request/response round-trip (snapshot, connect).
/// Generous enough for a cold multi-thousand-bar fetch, short enough that a
/// wedged backend surfaces as an error instead of freezing the UI.
const RESPONSE_TIMEOUT: Duration = Duration::from_secs(120);

/// Idle deadline while STREAMING a run. A 500+ symbol run legitimately takes
/// minutes, so this bounds the gap BETWEEN messages, not the total: the
/// backend publishes progress every ~100ms, so two minutes of silence means
/// it is stuck, not slow.
const STREAM_IDLE_TIMEOUT: Duration = Duration::from_secs(120);

/// How long a clean `shutdown` may take before the child is killed.
const SHUTDOWN_GRACE: Duration = Duration::from_secs(5);
const SHUTDOWN_POLL: Duration = Duration::from_millis(50);

/// Bridge failure — carries the human-readable reason.
///
/// Implements [`std::error::Error`] so binaries returning
/// `Result<T, Box<dyn std::error::Error>>` can propagate with `?`.
#[derive(Debug, Clone)]
pub struct BridgeError(pub String);

impl fmt::Display for BridgeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "python bridge: {}", self.0)
    }
}

impl std::error::Error for BridgeError {}

/// Shorthand for bridge results.
pub type BridgeResult<T> = Result<T, BridgeError>;

fn bridge_error(message: impl fmt::Display) -> BridgeError {
    BridgeError(message.to_string())
}

/// Read ONE newline-terminated line from `reader` under a deadline.
///
/// The read itself is blocking and cannot be cancelled, so it runs on a
/// detached thread that owns the lock and reports the line over a channel.
/// The `Arc` is CLONED into the thread, never consumed, so the caller's
/// handle (and the buffered stream behind it) stays usable afterwards. On
/// expiry `on_timeout` is invoked (the caller kills the child, which closes
/// the pipe and lets the reader finish) and the error is returned
/// immediately — the UI thread is released either way.
fn read_line_within<R>(
    reader: &Arc<Mutex<R>>,
    timeout: Duration,
    on_timeout: impl FnOnce(),
) -> BridgeResult<String>
where
    R: BufRead + Send + 'static,
{
    let (tx, rx) = mpsc::channel::<BridgeResult<String>>();
    let mut on_timeout = Some(on_timeout);
    let reader = Arc::clone(reader);
    std::thread::spawn(move || {
        let mut guard = match reader.lock() {
            Ok(guard) => guard,
            Err(err) => {
                let _ = tx.send(Err(bridge_error(format!("reader lock: {err}"))));
                return;
            }
        };
        let mut line = String::new();
        match guard.read_line(&mut line) {
            Ok(0) => {
                let _ = tx.send(Err(bridge_error("backend closed the stream")));
            }
            Ok(_) => {
                let _ = tx.send(Ok(line));
            }
            Err(err) => {
                let _ = tx.send(Err(bridge_error(format!("read: {err}"))));
            }
        }
        // `line`/`tx` dropped here: the lock is released so a later call (or
        // the next command after a kill) is not wedged behind this thread.
    });
    match rx.recv_timeout(timeout) {
        Ok(result) => result,
        Err(mpsc::RecvTimeoutError::Timeout) => {
            if let Some(kill) = on_timeout.take() {
                kill();
            }
            Err(bridge_error(format!(
                "backend produced no response within {}s — watchdog killed it",
                timeout.as_secs()
            )))
        }
        // The reader thread died without answering (lock poisoned, thread
        // panicked). Never fall back to blocking.
        Err(mpsc::RecvTimeoutError::Disconnected) => {
            Err(bridge_error("backend reader stopped before responding"))
        }
    }
}

/// Command sent to the Python backend.
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum BackendCommand {
    ListSymbols,
    GetMarketSnapshot {
        symbol: Option<String>,
        timeframe: Option<String>,
        limit: Option<i64>,
    },
    GetSystemSnapshot {
        #[serde(default, skip_serializing_if = "Option::is_none")]
        selected_id: Option<String>,
    },
    ConnectBroker {
        broker_id: String,
        credentials: std::collections::HashMap<String, String>,
    },
    DisconnectBroker {
        broker_id: String,
    },
    GetPortfolioSnapshot,
    GetLiveSnapshot,
    /// A host-mode UI action for the live service (setup/mode/start/stop/
    /// halt/arm). The Live view never mutates state in Rust — it queues the
    /// action and the backend applies it and answers with a fresh snapshot.
    LiveAction {
        action: serde_json::Value,
    },
    GetResearchSnapshot,
    GetLabSnapshot,
    SelectLabStrategy {
        strategy: String,
        symbols: Vec<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        timeframe: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        start: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        end: Option<String>,
        capital: f64,
        mode: String,
    },
    RunBacktest {
        strategy: String,
        symbols: Vec<String>,
        timeframe: Option<String>,
        start: Option<String>,
        end: Option<String>,
        capital: f64,
        mode: String,
    },
    /// Measured data-completeness probe for the Lab config strip. The backend
    /// counts real rows; the percentage is derived in `lab_coverage` (Rust).
    LabCoverage {
        symbols: Vec<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        timeframe: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        start: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        end: Option<String>,
        #[serde(default, skip_serializing_if = "std::ops::Not::not")]
        full: bool,
    },
    /// Cooperative stop of an in-flight run. The backend polls the flag
    /// between symbols, so a cancel can never leave a half-written result.
    CancelBacktest,
    SaveLabStrategy {
        strategy: String,
        code: String,
    },
    CreateStrategy {
        name: String,
        code: String,
    },
    DuplicateStrategy {
        source_name: String,
        copy_name: String,
    },
    ArchiveStrategy {
        strategy: String,
    },
    ValidateStrategy {
        code: String,
    },
    AiAssistStrategy {
        prompt: String,
        name: String,
    },
    /// Replace ONE strategy's NSE stock universe. Never touches strategy code.
    SaveLabUniverse {
        strategy: String,
        symbols: Vec<String>,
    },
    Shutdown,
}

/// Response from the Python backend.
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum BackendResponse {
    Ready { data: ReadyData },
    SymbolsListed { data: SymbolsData },
    MarketSnapshot { data: serde_json::Value },
    SystemSnapshot { data: serde_json::Value },
    PortfolioSnapshot { data: serde_json::Value },
    LiveSnapshot { data: serde_json::Value },
    ResearchSnapshot { data: serde_json::Value },
    LabSnapshot { data: serde_json::Value },
    LabCoverage { data: serde_json::Value },
    LabUniverse { data: serde_json::Value },
    StrategyCreated { data: serde_json::Value },
    StrategyDuplicated { data: serde_json::Value },
    StrategyArchived { data: serde_json::Value },
    StrategyValidated { data: serde_json::Value },
    AiStrategyAssisted { data: serde_json::Value },
    Error { data: ErrorData },
}

#[derive(Debug, Clone, Deserialize)]
pub struct ReadyData {
    pub backend: String,
    pub version: String,
    pub data_dir: String,
    pub strategy_dir: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct SymbolsData {
    pub symbols: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ErrorData {
    pub message: String,
}

/// Python backend process handle.
pub struct PythonBackend {
    process: Arc<Mutex<Child>>,
    stdin: Arc<Mutex<ChildStdin>>,
    stdout: Arc<Mutex<BufReader<ChildStdout>>>,
}

impl PythonBackend {
    /// Chapter dirs of the repo this binary was built from, derived from the
    /// executable path (`<root>/rust/target/<profile>/vayren-shell`).
    /// The backend must always run THESE chapters — never whatever editable
    /// install happens to be active globally (several checkouts share one
    /// interpreter, and a stale pointer serves ancient backend code).
    fn chapter_paths() -> Option<std::ffi::OsString> {
        const CHAPTERS: [&str; 9] = [
            "app",
            "core",
            "data",
            "market",
            "strategy",
            "backtest",
            "risk",
            "execution",
            "broker",
        ];
        let mut dir = std::env::current_exe().ok()?;
        dir.pop();
        for _ in 0..6 {
            if dir.join("src").join("app").is_dir() {
                let src_dir = dir.join("src");
                let mut paths: Vec<std::path::PathBuf> = vec![src_dir.clone()];
                paths.extend(CHAPTERS.iter().map(|c| src_dir.join(c)));
                if let Some(existing) = std::env::var_os("PYTHONPATH") {
                    paths.extend(std::env::split_paths(&existing));
                }
                return std::env::join_paths(paths).ok();
            }
            if !dir.pop() {
                break;
            }
        }
        None
    }

    /// Spawn one backend child process with no console window ever.
    fn spawn_child(program: &str, data_dir: &str, strategy_dir: &str) -> BridgeResult<Child> {
        let mut command = Command::new(program);
        match Self::chapter_paths() {
            Some(paths) => {
                command.env("PYTHONPATH", paths);
            }
            None => {
                return Err(bridge_error(format!(
                    "chapter layout unresolvable from {} — refusing to start a possibly stale backend",
                    std::env::current_exe()
                        .map(|p| p.display().to_string())
                        .unwrap_or_else(|_| "<unknown exe>".to_string())
                )));
            }
        }
        #[cfg(target_os = "windows")]
        {
            // CREATE_NO_WINDOW: a console-subsystem child of a GUI parent
            // would otherwise pop its own terminal (the exact black window
            // users reported). Pipes carry the protocol; no console needed.
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            command.creation_flags(CREATE_NO_WINDOW);
        }
        command
            .arg("-m")
            .arg("app.headless")
            .arg("--data-dir")
            .arg(data_dir)
            .arg("--strategy-dir")
            .arg(strategy_dir)
            .arg("--log-level")
            .arg("INFO")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit()) // Python logs go to stderr
            .spawn()
            .map_err(|e| bridge_error(format!("spawn python backend ({program}): {e}")))
    }

    /// Spawn the headless Python backend with no console window ever.
    ///
    /// The windowless interpreter (`pythonw`) is preferred; plain `python`
    /// is the fallback with its console creation suppressed. Either way the
    /// user never sees a terminal — the Slint window is the only surface.
    /// Blocks until the "ready" message arrives or the spawn fails. Returns
    /// the ready data on success.
    pub fn spawn(data_dir: &str, strategy_dir: &str) -> BridgeResult<(Self, ReadyData)> {
        // Windowless interpreter first; plain interpreter (still console-
        // suppressed) on ANY pythonw failure — a stub can spawn yet never
        // speak the protocol, so the whole handshake decides, not the spawn.
        match Self::spawn_ready("pythonw", data_dir, strategy_dir) {
            Ok(ready) => Ok(ready),
            Err(first) => match Self::spawn_ready("python", data_dir, strategy_dir) {
                Ok(ready) => Ok(ready),
                Err(_) => Err(first),
            },
        }
    }

    /// Spawn one interpreter and complete the `ready` handshake with it.
    fn spawn_ready(
        program: &str,
        data_dir: &str,
        strategy_dir: &str,
    ) -> BridgeResult<(Self, ReadyData)> {
        let mut child = Self::spawn_child(program, data_dir, strategy_dir)?;

        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| bridge_error("backend stdin not captured"))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| bridge_error("backend stdout not captured"))?;

        let reader = Arc::new(Mutex::new(BufReader::new(stdout)));

        // Wait for the "ready" message, under the same deadline every other
        // read uses: an interpreter that starts but never speaks the protocol
        // (a `pythonw` stub) must fail fast instead of hanging startup.
        let line = match read_line_within(&reader, RESPONSE_TIMEOUT, || {
            let _ = child.kill();
        }) {
            Ok(line) => line,
            Err(err) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(err);
            }
        };

        let response: BackendResponse = serde_json::from_str(&line)
            .map_err(|e| bridge_error(format!("parse ready message: {e}")))?;

        match response {
            BackendResponse::Ready { data } => {
                let backend = Self {
                    process: Arc::new(Mutex::new(child)),
                    stdin: Arc::new(Mutex::new(stdin)),
                    stdout: reader,
                };
                Ok((backend, data))
            }
            BackendResponse::Error { data } => Err(bridge_error(format!(
                "backend startup failed: {}",
                data.message
            ))),
            _ => Err(bridge_error("expected ready, got another response")),
        }
    }

    /// Send a command and wait for the response, under a deadline.
    ///
    /// Fail-closed: any error stops the command. A backend that never answers
    /// is killed by the watchdog and reported as an error — the caller is
    /// never left blocked on a dead child.
    pub fn send_command(&self, command: BackendCommand) -> BridgeResult<BackendResponse> {
        let json =
            serde_json::to_string(&command).map_err(|e| bridge_error(format!("encode: {e}")))?;

        // Write command.
        {
            let mut stdin = self
                .stdin
                .lock()
                .map_err(|e| bridge_error(format!("stdin lock: {e}")))?;
            writeln!(stdin, "{json}").map_err(|e| bridge_error(format!("write: {e}")))?;
            stdin
                .flush()
                .map_err(|e| bridge_error(format!("flush: {e}")))?;
        }

        let line = read_line_within(&self.stdout, RESPONSE_TIMEOUT, || {
            self.kill_child("response timeout")
        })?;

        let response: BackendResponse =
            serde_json::from_str(&line).map_err(|e| bridge_error(format!("decode: {e}")))?;

        Ok(response)
    }

    /// Send a command that STREAMS while it runs, handing every intermediate
    /// `lab_progress` message to `on_progress` and returning the terminal
    /// response.
    ///
    /// A 500+ symbol run occupies the backend for minutes; a plain round-trip
    /// would leave the UI with a dead "RUNNING" and nothing to show. Progress
    /// is read as it arrives so the screen tracks real completed work. Each
    /// read carries its own idle deadline, so a run that keeps publishing
    /// progress is never cut off, while one that goes silent is killed.
    pub fn send_command_streaming(
        &self,
        command: BackendCommand,
        mut on_progress: impl FnMut(serde_json::Value),
        mut on_error: impl FnMut(String),
    ) -> BridgeResult<BackendResponse> {
        let json =
            serde_json::to_string(&command).map_err(|e| bridge_error(format!("encode: {e}")))?;
        {
            let mut stdin = self
                .stdin
                .lock()
                .map_err(|e| bridge_error(format!("stdin lock: {e}")))?;
            writeln!(stdin, "{json}").map_err(|e| bridge_error(format!("write: {e}")))?;
            stdin
                .flush()
                .map_err(|e| bridge_error(format!("flush: {e}")))?;
        }
        loop {
            let line = read_line_within(&self.stdout, STREAM_IDLE_TIMEOUT, || {
                self.kill_child("stream idle timeout")
            })?;
            // Single parse per line: progress lines carry `"lab_progress"`,
            // terminal lines parse straight into the response enum — the old
            // `from_str`→`Value` + `from_value`→`Response` double parse is gone.
            if line.contains("\"lab_progress\"") {
                let value: serde_json::Value = serde_json::from_str(&line)
                    .map_err(|e| bridge_error(format!("decode: {e}")))?;
                match value.get("data") {
                    Some(data) => on_progress(data.clone()),
                    None => on_error("lab_progress without data".to_string()),
                }
                continue;
            }
            let response: BackendResponse =
                serde_json::from_str(&line).map_err(|e| bridge_error(format!("decode: {e}")))?;
            return Ok(response);
        }
    }

    /// Force-kill the backend child. A kill closes its stdout pipe, which is
    /// what unblocks any in-flight reader thread. Best-effort: an already-dead
    /// child is not an error (there is nothing left to clean up).
    pub fn kill_child(&self, reason: &str) {
        if let Ok(mut process) = self.process.lock() {
            match process.kill() {
                Ok(()) => eprintln!("python bridge: killed backend ({reason})"),
                // Already exited — nothing to kill, and the pending read will
                // see EOF on its own.
                Err(err) => eprintln!("python bridge: kill failed ({reason}): {err}"),
            }
        }
    }

    /// One serialized command round-trip through a shared handle. A poisoned
    /// mutex means a worker died mid-command — fail closed, never guess.
    pub fn lock_send(
        backend: &std::sync::Mutex<PythonBackend>,
        command: BackendCommand,
    ) -> BridgeResult<BackendResponse> {
        let guard = backend
            .lock()
            .map_err(|e| bridge_error(format!("backend lock: {e}")))?;
        guard.send_command(command)
    }

    /// Streaming round-trip through a shared handle, forwarding every
    /// intermediate progress event to `on_progress` and malformed progress to
    /// `on_error`. The outer handle lock is held only to clone the pipe
    /// handles — never across the line-read loop — so a concurrent cancel can
    /// still send while a run streams.
    pub fn lock_send_streaming(
        backend: &std::sync::Mutex<PythonBackend>,
        command: BackendCommand,
        on_progress: impl FnMut(serde_json::Value),
        on_error: impl FnMut(String),
    ) -> BridgeResult<BackendResponse> {
        // Clone the pipe handles under a brief lock, then stream lock-free.
        let (stdin, stdout, process) = {
            let guard = backend
                .lock()
                .map_err(|e| bridge_error(format!("backend lock: {e}")))?;
            (
                Arc::clone(&guard.stdin),
                Arc::clone(&guard.stdout),
                Arc::clone(&guard.process),
            )
        };
        let json =
            serde_json::to_string(&command).map_err(|e| bridge_error(format!("encode: {e}")))?;
        {
            let mut stdin = stdin
                .lock()
                .map_err(|e| bridge_error(format!("stdin lock: {e}")))?;
            writeln!(stdin, "{json}").map_err(|e| bridge_error(format!("write: {e}")))?;
            stdin
                .flush()
                .map_err(|e| bridge_error(format!("flush: {e}")))?;
        }
        Self::stream_lines(&stdout, &process, on_progress, on_error)
    }

    /// Line-read loop over already-cloned pipe handles (outer lock released).
    fn stream_lines(
        stdout: &Arc<Mutex<BufReader<ChildStdout>>>,
        process: &Arc<Mutex<Child>>,
        mut on_progress: impl FnMut(serde_json::Value),
        mut on_error: impl FnMut(String),
    ) -> BridgeResult<BackendResponse> {
        loop {
            let process_kill = Arc::clone(process);
            let line = read_line_within(stdout, STREAM_IDLE_TIMEOUT, move || {
                if let Ok(mut child) = process_kill.lock() {
                    let _ = child.kill();
                }
                eprintln!("python bridge: killed backend (stream idle timeout)");
            })?;
            if line.contains("\"lab_progress\"") {
                let value: serde_json::Value = serde_json::from_str(&line)
                    .map_err(|e| bridge_error(format!("decode: {e}")))?;
                match value.get("data") {
                    Some(data) => on_progress(data.clone()),
                    None => on_error("lab_progress without data".to_string()),
                }
                continue;
            }
            let response: BackendResponse =
                serde_json::from_str(&line).map_err(|e| bridge_error(format!("decode: {e}")))?;
            return Ok(response);
        }
    }

    /// Shut the backend down gracefully, then FORCE it down if it hangs.
    ///
    /// A bare `process.wait()` blocks forever when the backend ignores the
    /// shutdown command (wedged worker thread, blocked native call). The app
    /// window would already be closed at that point, so an infinite wait means
    /// a process nobody can get rid of. After a bounded grace period the
    /// child is killed and the shutdown is reported as an error — the caller
    /// sees what happened instead of hanging.
    pub fn shutdown(&self) -> BridgeResult<()> {
        let _ = self.send_command(BackendCommand::Shutdown);

        let mut process = self
            .process
            .lock()
            .map_err(|e| bridge_error(format!("process lock: {e}")))?;

        let deadline = Instant::now() + SHUTDOWN_GRACE;
        loop {
            match process.try_wait() {
                Ok(Some(_status)) => return Ok(()),
                Ok(None) => {}
                Err(err) => return Err(bridge_error(format!("wait: {err}"))),
            }
            if Instant::now() >= deadline {
                let _ = process.kill();
                // Reap the killed child so no zombie is left behind.
                let _ = process.wait();
                return Err(bridge_error(format!(
                    "backend did not exit within {}s after shutdown — killed it",
                    SHUTDOWN_GRACE.as_secs()
                )));
            }
            std::thread::sleep(SHUTDOWN_POLL);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_command_serialization() {
        let cmd = BackendCommand::ListSymbols;
        let json = serde_json::to_string(&cmd).unwrap();
        assert_eq!(json, r#"{"type":"list_symbols"}"#);
    }

    #[test]
    fn test_response_deserialization() {
        let json = r#"{"type":"ready","data":{"backend":"vayren-headless","version":"1.18.0","data_dir":"/tmp","strategy_dir":"/tmp"}}"#;
        let response: BackendResponse = serde_json::from_str(json).unwrap();
        match response {
            BackendResponse::Ready { data } => {
                assert_eq!(data.backend, "vayren-headless");
                assert_eq!(data.version, "1.18.0");
            }
            _ => panic!("Expected Ready response"),
        }
    }

    #[test]
    fn test_market_snapshot_command_serialization() {
        let cmd = BackendCommand::GetMarketSnapshot {
            symbol: Some("RELIANCE".to_string()),
            timeframe: None,
            limit: Some(150),
        };
        let json = serde_json::to_string(&cmd).unwrap();
        assert_eq!(
            json,
            r#"{"type":"get_market_snapshot","symbol":"RELIANCE","timeframe":null,"limit":150}"#
        );
    }

    #[test]
    fn test_market_snapshot_ingestion() {
        let data = serde_json::json!({
            "symbols": [{"symbol": "RELIANCE", "price": 2500.0, "change_pct": 1.5}],
            "selected_symbol": "RELIANCE",
            "timeframes": ["15m", "30m"],
            "timeframe": "",
            "exchange": "",
            "bars": [
                {"time": "2026-09-18 09:15:00", "open": 1.0, "high": 2.0, "low": 0.5,
                 "close": 1.5, "volume": 100},
                {"time": "2026-09-18 09:30:00", "open": 1.5, "high": 2.5, "low": 1.0,
                 "close": 2.0, "volume": 200}
            ]
        });
        let mut state = crate::market::MarketState::default();
        crate::market::apply_snapshot_json(&mut state, &data);
        assert_eq!(state.selected_symbol, "RELIANCE");
        assert_eq!(state.bars.len(), 2);
        assert_eq!(state.symbols.len(), 1);
        assert!(matches!(state.status, crate::market::MarketStatus::Ready));
    }

    #[test]
    fn test_system_snapshot_command_serialization() {
        let json = serde_json::to_string(&BackendCommand::GetSystemSnapshot { selected_id: None })
            .unwrap();
        assert_eq!(json, r#"{"type":"get_system_snapshot"}"#);
    }

    #[test]
    fn test_connect_broker_command_serialization() {
        let mut creds = std::collections::HashMap::new();
        creds.insert("app_id".to_string(), "TEST_ID".to_string());
        let cmd = BackendCommand::ConnectBroker {
            broker_id: "fyers".to_string(),
            credentials: creds,
        };
        let json = serde_json::to_string(&cmd).unwrap();
        assert!(json.contains(r#""type":"connect_broker""#));
        assert!(json.contains(r#""broker_id":"fyers""#));
        assert!(json.contains(r#""app_id":"TEST_ID""#));
    }

    #[test]
    fn test_disconnect_broker_command_serialization() {
        let cmd = BackendCommand::DisconnectBroker {
            broker_id: "fyers".to_string(),
        };
        let json = serde_json::to_string(&cmd).unwrap();
        assert_eq!(json, r#"{"type":"disconnect_broker","broker_id":"fyers"}"#);
    }

    #[test]
    fn test_system_snapshot_ingestion() {
        let data = serde_json::json!({
            "brokers": [
                {"id": "zerodha", "display_name": "Zerodha", "status": "CONNECTED",
                 "selected": false},
                {"id": "fyers", "display_name": "Fyers", "status": "LOGIN_REQUIRED",
                 "selected": true}
            ],
            "selected_id": "fyers",
            "environment": "paper",
            "status_raw": "LOGIN_REQUIRED",
            "configured": true,
            "can_login": true,
            "can_disconnect": false,
            "credential_fields": [
                {"key": "app_id", "label": "App ID", "secret": false, "required": true}
            ]
        });
        let ws = crate::broker_connection::BrokerWorkspace::from_json(&data);
        assert_eq!(ws.selected_id, "fyers");
        assert_eq!(ws.brokers.len(), 2);
        assert_eq!(ws.display_name, "Fyers");
        assert_eq!(ws.fields.len(), 1);
    }

    #[test]
    fn test_portfolio_snapshot_command_serialization() {
        let json = serde_json::to_string(&BackendCommand::GetPortfolioSnapshot).unwrap();
        assert_eq!(json, r#"{"type":"get_portfolio_snapshot"}"#);
    }

    #[test]
    fn test_portfolio_snapshot_ingestion() {
        let data = serde_json::json!({
            "mode": "PAPER",
            "lifecycle": "STOPPED",
            "broker": {"name": "paper", "connected": false},
            "positions": [],
            "orders": [],
            "fills": [],
            "pnl": {"realized": 0.0, "unrealized": 0.0, "total": 0.0},
            "risk": {"status": "READY"},
            "kill": {"halted": false}
        });
        let snapshot = crate::portfolio::PortfolioSnapshot::from_json(&data);
        assert_eq!(snapshot.mode, "PAPER");
        assert!(snapshot.positions.is_empty());
        assert!(!crate::portfolio::is_configured(&snapshot));
    }

    #[test]
    fn test_live_snapshot_command_serialization() {
        let json = serde_json::to_string(&BackendCommand::GetLiveSnapshot).unwrap();
        assert_eq!(json, r#"{"type":"get_live_snapshot"}"#);
    }

    #[test]
    fn test_live_snapshot_ingestion() {
        let data = serde_json::json!({
            "mode": "PAPER",
            "session_status": "STOPPED",
            "market_symbol": "RELIANCE",
            "market_timeframe": "15m",
            "market_bars": [{"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}],
            "positions": []
        });
        let mut state = crate::shell::demo_live_state();
        state.apply_snapshot(&data);
        assert_eq!(state.market_symbol, "RELIANCE");
        assert_eq!(state.market_bar_count, 1);
        assert_eq!(state.market_last_price, Some(1.5));
    }

    #[test]
    fn test_research_snapshot_command_serialization() {
        let json = serde_json::to_string(&BackendCommand::GetResearchSnapshot).unwrap();
        assert_eq!(json, r#"{"type":"get_research_snapshot"}"#);
    }

    #[test]
    fn test_research_snapshot_ingestion() {
        let data = serde_json::json!({
            "strategies": [{"name": "OBR", "description": "", "version": "v3"}],
            "experiments": [{"id": "exp1", "strategy": "OBR", "status": "COMPLETED"}],
            "selected_id": "",
            "running": false
        });
        let mut state = crate::research_state::demo_research_state();
        state.apply_host_snapshot(&data);
        assert_eq!(state.strategies.len(), 1);
        assert_eq!(state.strategies[0].name, "OBR");
        assert_eq!(state.experiments.len(), 1);
    }

    #[test]
    fn test_lab_snapshot_command_serialization() {
        let json = serde_json::to_string(&BackendCommand::GetLabSnapshot).unwrap();
        assert_eq!(json, r#"{"type":"get_lab_snapshot"}"#);
    }

    #[test]
    fn test_lab_snapshot_ingestion() {
        let data = serde_json::json!({
            "strategies": [{"name": "OBR", "modified": "18 Sep 26"}],
            "selected_name": "OBR",
            "mode": "buy",
            "run": "ready",
            "engine_wired": true,
            "config": {"universe": "NO UNIVERSE"},
            "results": null
        });
        let mut state = crate::shell::demo_lab_state();
        crate::lab::apply_snapshot_json(&mut state, &data);
        assert_eq!(state.strategies.len(), 1);
        assert_eq!(state.strategies[0].name, "OBR");
        assert_eq!(state.selected, Some(0));
    }

    #[test]
    fn test_save_lab_universe_command_serialization() {
        let cmd = BackendCommand::SaveLabUniverse {
            strategy: "OBR".to_string(),
            symbols: vec!["RELIANCE".to_string(), "TCS".to_string()],
        };
        let json = serde_json::to_string(&cmd).unwrap();
        assert_eq!(
            json,
            r#"{"type":"save_lab_universe","strategy":"OBR","symbols":["RELIANCE","TCS"]}"#
        );
    }

    #[test]
    fn test_select_lab_strategy_command_serialization() {
        let cmd = BackendCommand::SelectLabStrategy {
            strategy: "SMA Crossover".to_string(),
            symbols: vec!["RELIANCE".to_string()],
            timeframe: Some("15m".to_string()),
            start: Some("2026-01-01".to_string()),
            end: None,
            capital: 10000.0,
            mode: "buy".to_string(),
        };
        let json = serde_json::to_string(&cmd).unwrap();
        assert_eq!(
            json,
            r#"{"type":"select_lab_strategy","strategy":"SMA Crossover","symbols":["RELIANCE"],"timeframe":"15m","start":"2026-01-01","capital":10000.0,"mode":"buy"}"#
        );
    }

    #[test]
    fn test_lab_coverage_command_serialization() {
        let cmd = BackendCommand::LabCoverage {
            symbols: vec!["RELIANCE".to_string(), "TCS".to_string()],
            timeframe: Some("30m".to_string()),
            start: Some("2019-09-19".to_string()),
            end: Some("2026-08-18".to_string()),
            full: false,
        };
        let json = serde_json::to_string(&cmd).unwrap();
        assert_eq!(
            json,
            r#"{"type":"lab_coverage","symbols":["RELIANCE","TCS"],"timeframe":"30m","start":"2019-09-19","end":"2026-08-18"}"#
        );
    }

    #[test]
    fn test_lab_coverage_response_is_its_own_kind() {
        let parsed: BackendResponse =
            serde_json::from_str(r#"{"type":"lab_coverage","data":{"bars_present":10}}"#).unwrap();
        assert!(matches!(parsed, BackendResponse::LabCoverage { .. }));
    }

    #[test]
    fn test_run_backtest_command_serialization() {
        let cmd = BackendCommand::RunBacktest {
            strategy: "SMA Crossover".to_string(),
            symbols: vec!["RELIANCE".to_string()],
            timeframe: Some("15m".to_string()),
            start: Some("2026-01-01".to_string()),
            end: None,
            capital: 10000.0,
            mode: "buy".to_string(),
        };
        let json = serde_json::to_string(&cmd).unwrap();
        assert_eq!(
            json,
            r#"{"type":"run_backtest","strategy":"SMA Crossover","symbols":["RELIANCE"],"timeframe":"15m","start":"2026-01-01","end":null,"capital":10000.0,"mode":"buy"}"#
        );
    }

    #[test]
    fn test_lab_run_result_ingestion() {
        let data = serde_json::json!({
            "strategies": [{"name": "SMA Crossover", "modified": "built-in"}],
            "selected_name": "SMA Crossover",
            "mode": "buy",
            "run": "complete",
            "engine_wired": true,
            "config": {"universe": "RELIANCE", "timeframe": "15m"},
            "results": {
                "metrics": {"net_profit": 1234.5, "total_trades": 10,
                    "win_rate": 60.0, "profit_factor": 1.5, "expectancy": 123.4,
                    "max_drawdown_pct": 2.5, "sharpe_ratio": 1.1, "avg_trade": 123.4},
                "ranking": [{"rank": 1, "symbol": "RELIANCE", "status": "ranked",
                    "net_profit": 1234.5, "return_pct": 1.2, "total_trades": 10,
                    "win_rate": 60.0, "profit_factor": 1.5, "max_drawdown_pct": 2.5,
                    "sharpe_ratio": 1.1}],
                "trades": [{"symbol": "RELIANCE", "side": "LONG",
                    "entry_time": "2026-01-02T09:15:00", "exit_time": "2026-01-02T15:30:00",
                    "entry_px": 100.0, "exit_px": 101.0, "pnl": 100.0,
                    "r_multiple": 1.0, "bars": 5, "reason": "SIGNAL", "winning": true}],
                "equity_curve": [{"equity": 1000000.0, "drawdown_pct": 0.0},
                    {"equity": 1001234.5, "drawdown_pct": 0.0}],
                "risk_notes": []
            }
        });
        let mut state = crate::shell::demo_lab_state();
        crate::lab::apply_snapshot_json(&mut state, &data);
        assert_eq!(state.run, crate::lab::RunState::Complete);
        let view = crate::lab::project(&state);
        assert!(view.show_results);
        assert_eq!(view.ranking.len(), 1);
        assert_eq!(view.trades.len(), 1);
        assert!(!view.equity.is_empty());
    }

    #[test]
    fn test_chapter_paths_resolves_repo_chapters() {
        // Test binaries run from target/debug/deps: walking up must reach
        // the repo root holding the src dirs.
        let paths = PythonBackend::chapter_paths().expect("repo root not found");
        let joined = paths.to_string_lossy();
        for chapter in ["app", "market", "strategy"] {
            assert!(joined.contains(chapter), "missing {chapter} in {joined}");
        }
    }

    #[test]
    fn test_bridge_error_converts_to_boxed_error() {
        let err = bridge_error("boom");
        let boxed: Box<dyn std::error::Error> = err.into();
        assert_eq!(boxed.to_string(), "python bridge: boom");
    }

    #[test]
    fn a_reader_that_never_answers_is_cut_off_not_waited_on_forever() {
        // The whole point of the deadline: a backend that stops talking must
        // not freeze the caller. The watchdog fires and the error returns
        // promptly instead of blocking on the pipe read.
        use std::io::Read;
        struct Silent;
        impl Read for Silent {
            fn read(&mut self, _buf: &mut [u8]) -> std::io::Result<usize> {
                // Block long past the test's deadline; killing the reader is
                // what normally ends this, and here the channel timeout does.
                std::thread::sleep(std::time::Duration::from_secs(30));
                Ok(0)
            }
        }
        let reader = Arc::new(Mutex::new(std::io::BufReader::new(Silent)));
        let started = std::time::Instant::now();
        let killed = Arc::new(Mutex::new(false));
        let flag = Arc::clone(&killed);
        let result: BridgeResult<String> =
            read_line_within(&reader, std::time::Duration::from_millis(100), move || {
                *flag.lock().expect("flag lock") = true;
            });
        let elapsed = started.elapsed();
        assert!(result.is_err(), "a silent backend is an error, not a hang");
        assert!(
            elapsed < std::time::Duration::from_secs(5),
            "must return at the deadline, took {elapsed:?}"
        );
        assert!(*killed.lock().expect("flag lock"), "watchdog must fire");
    }

    #[test]
    fn a_reader_that_answers_returns_the_line_within_the_deadline() {
        let reader = Arc::new(Mutex::new(std::io::BufReader::new(std::io::Cursor::new(
            b"{\"type\":\"ready\"}\n".to_vec(),
        ))));
        let line = read_line_within(&reader, std::time::Duration::from_secs(5), || {
            panic!("the watchdog must not fire when the backend answers");
        })
        .expect("line");
        assert_eq!(line, "{\"type\":\"ready\"}\n");
    }

    #[test]
    fn a_closed_stream_is_reported_not_treated_as_a_timeout() {
        let reader = Arc::new(Mutex::new(std::io::BufReader::new(std::io::Cursor::new(
            Vec::new(),
        ))));
        let result = read_line_within(&reader, std::time::Duration::from_secs(5), || {
            panic!("EOF is not a timeout")
        });
        assert!(result.is_err());
        assert!(result.unwrap_err().0.contains("closed the stream"));
    }
}
