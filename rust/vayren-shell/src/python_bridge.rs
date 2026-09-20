//! Python backend bridge — IPC via JSON over stdin/stdout.
//!
//! Spawns the headless Python backend process and communicates via newline-
//! delimited JSON. The bridge is fail-closed: any protocol violation or
//! backend crash is immediately visible (no silent fallback).

use serde::{Deserialize, Serialize};
use std::fmt;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::{Arc, Mutex};

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
    GetSystemSnapshot,
    GetPortfolioSnapshot,
    GetLiveSnapshot,
    GetResearchSnapshot,
    GetLabSnapshot,
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
    /// Spawn the headless Python backend.
    ///
    /// Blocks until the "ready" message arrives or the spawn fails. Returns
    /// the ready data on success.
    pub fn spawn(data_dir: &str, strategy_dir: &str) -> BridgeResult<(Self, ReadyData)> {
        let mut child = Command::new("python")
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
            .map_err(|e| bridge_error(format!("spawn python backend: {e}")))?;

        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| bridge_error("backend stdin not captured"))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| bridge_error("backend stdout not captured"))?;

        let mut reader = BufReader::new(stdout);

        // Wait for the "ready" message.
        let mut line = String::new();
        reader
            .read_line(&mut line)
            .map_err(|e| bridge_error(format!("read ready message: {e}")))?;

        let response: BackendResponse = serde_json::from_str(&line)
            .map_err(|e| bridge_error(format!("parse ready message: {e}")))?;

        match response {
            BackendResponse::Ready { data } => {
                let backend = Self {
                    process: Arc::new(Mutex::new(child)),
                    stdin: Arc::new(Mutex::new(stdin)),
                    stdout: Arc::new(Mutex::new(reader)),
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

    /// Send a command and wait for the response.
    ///
    /// Blocks until the response arrives. Fail-closed: any error stops
    /// the command.
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

        // Read response.
        let mut line = String::new();
        {
            let mut stdout = self
                .stdout
                .lock()
                .map_err(|e| bridge_error(format!("stdout lock: {e}")))?;
            stdout
                .read_line(&mut line)
                .map_err(|e| bridge_error(format!("read: {e}")))?;
        }

        let response: BackendResponse =
            serde_json::from_str(&line).map_err(|e| bridge_error(format!("decode: {e}")))?;

        Ok(response)
    }

    /// Shut the backend down gracefully (shared handle: waits via the lock).
    pub fn shutdown(&self) -> BridgeResult<()> {
        let _ = self.send_command(BackendCommand::Shutdown);

        let mut process = self
            .process
            .lock()
            .map_err(|e| bridge_error(format!("process lock: {e}")))?;

        process
            .wait()
            .map_err(|e| bridge_error(format!("wait: {e}")))?;

        Ok(())
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
        let json = serde_json::to_string(&BackendCommand::GetSystemSnapshot).unwrap();
        assert_eq!(json, r#"{"type":"get_system_snapshot"}"#);
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
    fn test_bridge_error_converts_to_boxed_error() {
        let err = bridge_error("boom");
        let boxed: Box<dyn std::error::Error> = err.into();
        assert_eq!(boxed.to_string(), "python bridge: boom");
    }
}
