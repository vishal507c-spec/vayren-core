//! End-to-end client tests against a scripted fake gateway (loopback only).
//!
//! The fake speaks the exact schema-v1 contract (hello/welcome/snapshot,
//! subscribe + last_seq replay-or-resync, sequenced events, command echo
//! with duplicate suppression) over plain `ws://` — the framing and
//! protocol are transport-identical to `wss://`; TLS is tungstenite+rustls
//! below this layer. Read-only throughout, except the `echo_probe`
//! round-trip, which exists ONLY to prove request_id retry semantics against
//! a harness that executes nothing (it is never a trading command and is
//! never sent to a production gateway by any caller in this tree).

use serde_json::{json, Value};
use std::collections::HashMap;
use std::net::TcpListener;
use std::sync::mpsc::{self, Receiver, Sender};
use std::thread;
use std::time::Duration;
use tungstenite::{accept, Message};
use vayren_remote_client::{ClientEvent, RemoteClient};

const TOKEN: &str = "test-token-0123456789abcdef";
const STEP: Duration = Duration::from_secs(5);

/// Out-of-band directives the test injects into the running fake.
enum Directive {
    PushStreamEvent { name: String },
    PushRaw(String),
    Stop,
}

fn envelope(msg_type: &str, payload: Value, seq: Option<i64>, request_id: Option<&str>) -> String {
    let mut out = json!({"type": msg_type, "v": 1, "ts": "t", "payload": payload});
    if let Some(seq) = seq {
        out["seq"] = seq.into();
    }
    if let Some(id) = request_id {
        out["request_id"] = id.into();
    }
    out.to_string()
}

fn snapshot_sections() -> Value {
    json!({
        "broker": {"status": "DISCONNECTED", "name": "", "reason": "not started"},
        "strategy": {"status": "BLOCKED", "mode": "PAPER"},
        "risk": {"status": "NOT READY"},
        "blockers": ["no strategy selected"],
    })
}

/// Scripted gateway: exactly the Python contract, nothing more.
fn run_fake(addr_tx: Sender<String>, directives: Receiver<Directive>, seq_start: i64) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    addr_tx
        .send(listener.local_addr().unwrap().to_string())
        .unwrap();
    let mut seq = seq_start;
    let mut ring: Vec<(i64, Value)> = Vec::new();
    let mut seen_commands: HashMap<String, Value> = HashMap::new();
    let mut connections = 0;
    while connections < 4 {
        let Ok((stream, _)) = listener.accept() else {
            break;
        };
        stream
            .set_read_timeout(Some(Duration::from_millis(200)))
            .unwrap();
        let mut socket = match accept(stream) {
            Ok(socket) => socket,
            Err(_) => break,
        };
        connections += 1;
        // First frame must be hello.
        let hello_text = match socket.read() {
            Ok(Message::Text(text)) => text.to_string(),
            _ => continue,
        };
        let hello: Value = serde_json::from_str(&hello_text).unwrap_or(Value::Null);
        let token = hello
            .get("payload")
            .and_then(|p| p.get("token"))
            .and_then(|t| t.as_str())
            .unwrap_or("");
        if hello.get("type").and_then(|t| t.as_str()) != Some("hello") || token != TOKEN {
            let _ = socket.send(Message::text(envelope(
                "error",
                json!({"code": "AUTH_FAILED", "detail": "bad token"}),
                None,
                None,
            )));
            let _ = socket.close(None);
            continue;
        }
        let _ = socket.send(Message::text(envelope(
            "welcome",
            json!({"connection_id": "c1", "role": "trade", "server_time": "t"}),
            None,
            None,
        )));
        let _ = socket.send(Message::text(envelope(
            "snapshot",
            json!({"snapshot": snapshot_sections(), "server_time": "t"}),
            None,
            None,
        )));
        loop {
            // Test-side injections: single drain so no directive is swallowed.
            loop {
                match directives.try_recv() {
                    Ok(Directive::PushStreamEvent { name }) => {
                        seq += 1;
                        let payload = json!({"name": name, "detail": "d"});
                        ring.push((seq, payload.clone()));
                        if ring.len() > 16 {
                            ring.remove(0);
                        }
                        let _ =
                            socket.send(Message::text(envelope("event", payload, Some(seq), None)));
                    }
                    Ok(Directive::PushRaw(raw)) => {
                        let _ = socket.send(Message::text(raw));
                    }
                    Ok(Directive::Stop) => return,
                    Err(_) => break,
                }
            }
            let frame = match socket.read() {
                Ok(Message::Text(text)) => text.to_string(),
                Ok(Message::Ping(payload)) => {
                    let _ = socket.send(Message::Pong(payload));
                    continue;
                }
                Ok(Message::Pong(_)) => continue,
                // Close frame or a dead socket ends THIS connection — back to
                // accept. Idle read timeouts just loop (a quiet client is not
                // a dead client; breaking here reset every patient test).
                Ok(Message::Close(_)) | Ok(_) => break,
                Err(tungstenite::Error::Io(err))
                    if err.kind() == std::io::ErrorKind::TimedOut
                        || err.kind() == std::io::ErrorKind::WouldBlock =>
                {
                    continue
                }
                Err(_) => break,
            };
            let inbound: Value = match serde_json::from_str(&frame) {
                Ok(value) => value,
                Err(_) => {
                    let _ = socket.send(Message::text(envelope(
                        "error",
                        json!({"code": "BAD_MESSAGE", "detail": "not json"}),
                        None,
                        None,
                    )));
                    continue;
                }
            };
            match inbound.get("type").and_then(|t| t.as_str()) {
                Some("ping") => {
                    let _ = socket.send(Message::text(envelope("pong", json!({}), None, None)));
                }
                Some("subscribe") => {
                    let last = inbound
                        .get("payload")
                        .and_then(|p| p.get("last_seq"))
                        .and_then(serde_json::Value::as_i64);
                    match last {
                        Some(want)
                            if !ring.is_empty()
                                && want < ring.first().map(|(s, _)| s - 1).unwrap_or(0) =>
                        {
                            let _ = socket.send(Message::text(envelope(
                                "snapshot",
                                json!({"snapshot": snapshot_sections(), "resync": true}),
                                None,
                                None,
                            )));
                        }
                        Some(want) => {
                            for (seq, payload) in ring.iter().filter(|(s, _)| *s > want) {
                                let _ = socket.send(Message::text(envelope(
                                    "event",
                                    payload.clone(),
                                    Some(*seq),
                                    None,
                                )));
                            }
                        }
                        None => {
                            let _ = socket.send(Message::text(envelope(
                                "snapshot",
                                json!({"snapshot": snapshot_sections(), "server_time": "t"}),
                                None,
                                None,
                            )));
                        }
                    }
                }
                Some("command") => {
                    let id = inbound
                        .get("request_id")
                        .and_then(|r| r.as_str())
                        .unwrap_or("")
                        .to_string();
                    if let Some(cached) = seen_commands.get(&id) {
                        let mut replay = cached.clone();
                        replay["cached"] = true.into();
                        let _ = socket.send(Message::text(envelope(
                            "command_result",
                            replay,
                            None,
                            Some(&id),
                        )));
                    } else {
                        let result =
                            json!({"ok": true, "cached": false, "snapshot": snapshot_sections()});
                        seen_commands.insert(id.clone(), result.clone());
                        let _ = socket.send(Message::text(envelope(
                            "command_result",
                            result,
                            None,
                            Some(&id),
                        )));
                    }
                }
                _ => {
                    let _ = socket.send(Message::text(envelope(
                        "error",
                        json!({"code": "BAD_MESSAGE", "detail": "unexpected"}),
                        None,
                        None,
                    )));
                }
            }
        }
    }
}

fn start_fake(seq_start: i64) -> (String, Sender<Directive>) {
    let (addr_tx, addr_rx) = mpsc::channel();
    let (dir_tx, dir_rx) = mpsc::channel();
    thread::spawn(move || run_fake(addr_tx, dir_rx, seq_start));
    let addr = addr_rx.recv_timeout(Duration::from_secs(5)).unwrap();
    (format!("ws://{addr}/vayren/v1"), dir_tx)
}

fn next_of(client: &mut RemoteClient) -> ClientEvent {
    client.next(STEP).expect("frame within step timeout")
}

#[test]
fn handshake_hello_welcome_snapshot() {
    let (url, dir) = start_fake(0);
    let (client, welcome, snapshot) = RemoteClient::connect(&url, TOKEN, STEP).unwrap();
    let (connection_id, role) = match welcome {
        ClientEvent::Welcome {
            connection_id,
            role,
        } => (connection_id, role),
        other => panic!("expected welcome, got {other:?}"),
    };
    assert_eq!((connection_id.as_str(), role.as_str()), ("c1", "trade"));
    match snapshot {
        ClientEvent::Snapshot { sections, resync } => {
            assert!(!resync);
            assert_eq!(sections["strategy"]["status"], "BLOCKED");
        }
        other => panic!("expected snapshot, got {other:?}"),
    }
    assert!(client.last_seq.is_none());
    let _ = dir.send(Directive::Stop);
}

#[test]
fn bad_token_is_refused_before_snapshot() {
    let (url, dir) = start_fake(0);
    let err = RemoteClient::connect(&url, "wrong-token-0123456789", STEP)
        .err()
        .expect("bad token must fail");
    assert!(err.to_string().contains("AUTH_FAILED"), "{err}");
    let _ = dir.send(Directive::Stop);
}

#[test]
fn streams_arrive_in_sequence_order() {
    let (url, dir) = start_fake(0);
    let (mut client, _, _) = RemoteClient::connect(&url, TOKEN, STEP).unwrap();
    dir.send(Directive::PushStreamEvent {
        name: "ORDER_SENT".to_string(),
    })
    .unwrap();
    dir.send(Directive::PushStreamEvent {
        name: "FILL".to_string(),
    })
    .unwrap();
    let mut names = Vec::new();
    for _ in 0..2 {
        match next_of(&mut client) {
            ClientEvent::StreamEvent {
                name,
                seq: _,
                payload: _,
            } => names.push(name),
            other => panic!("expected stream event, got {other:?}"),
        }
    }
    assert_eq!(names, vec!["ORDER_SENT", "FILL"]);
    assert_eq!(client.last_seq, Some(2));
}

#[test]
fn resume_replays_only_missed_events() {
    let (url, dir) = start_fake(0);
    let (mut first, _, _) = RemoteClient::connect(&url, TOKEN, STEP).unwrap();
    dir.send(Directive::PushStreamEvent {
        name: "E1".to_string(),
    })
    .unwrap();
    dir.send(Directive::PushStreamEvent {
        name: "E2".to_string(),
    })
    .unwrap();
    for _ in 0..2 {
        assert!(matches!(
            next_of(&mut first),
            ClientEvent::StreamEvent { .. }
        ));
    }
    assert_eq!(first.last_seq, Some(2));
    drop(first);
    dir.send(Directive::PushStreamEvent {
        name: "E3".to_string(),
    })
    .unwrap();
    // Second connection resumes where the first left off.
    let (mut second, _, _) = RemoteClient::connect(&url, TOKEN, STEP).unwrap();
    second.subscribe(&[], Some(2)).unwrap();
    // Skip the fresh subscribe snapshot; the replay follows it.
    let mut replayed = Vec::new();
    for _ in 0..4 {
        match next_of(&mut second) {
            ClientEvent::Snapshot { .. } => {}
            ClientEvent::StreamEvent { name, seq, .. } => replayed.push((name, seq)),
            other => panic!("unexpected {other:?}"),
        }
        if replayed.len() == 1 {
            break;
        }
    }
    assert_eq!(replayed, vec![("E3".to_string(), 3)]);
}

#[test]
fn stale_cursor_triggers_resync_snapshot() {
    // Gateway ring holds seq 100..102 (seq_start=99): a cursor of 2 is
    // irrecoverably stale, so the resume must answer resync — exactly the
    // gateway's own rule (empty ring = nothing missed = no resync).
    let (url, dir) = start_fake(99);
    let (first, _, _) = RemoteClient::connect(&url, TOKEN, STEP).unwrap();
    drop(first);
    // NOTE: events must be pushed while SOME connection holds the fake's
    // per-connection loop. Reconnect first, then inject, then resume.
    let (mut client, _, _) = RemoteClient::connect(&url, TOKEN, STEP).unwrap();
    dir.send(Directive::PushStreamEvent {
        name: "E100".to_string(),
    })
    .unwrap();
    dir.send(Directive::PushStreamEvent {
        name: "E101".to_string(),
    })
    .unwrap();
    // Drain the two live arrivals (cursor now 101, ring covers 100..101).
    for _ in 0..2 {
        assert!(matches!(
            next_of(&mut client),
            ClientEvent::StreamEvent { .. }
        ));
    }
    drop(client);
    let (mut resumed, _, _) = RemoteClient::connect(&url, TOKEN, STEP).unwrap();
    resumed.subscribe(&[], Some(2)).unwrap();
    let mut saw_resync = false;
    for _ in 0..4 {
        match next_of(&mut resumed) {
            ClientEvent::Snapshot { resync, .. } if resync => {
                saw_resync = true;
                break;
            }
            ClientEvent::Snapshot { .. } | ClientEvent::StreamEvent { .. } => {}
            other => panic!("unexpected {other:?}"),
        }
    }
    assert!(saw_resync, "stale last_seq must resync");
    let _ = dir.send(Directive::Stop);
}

#[test]
fn command_retry_reuses_request_id_without_duplicate_effect() {
    let (url, dir) = start_fake(0);
    let (mut client, _, _) = RemoteClient::connect(&url, TOKEN, STEP).unwrap();
    // Harness-only echo action: proves idempotency mechanics, executes nothing.
    client
        .send_command("echo_probe", json!({}), "req-7")
        .unwrap();
    let first = next_of(&mut client);
    let (first_id, first_cached) = match first {
        ClientEvent::CommandResult {
            request_id,
            payload,
        } => (request_id, payload["cached"].as_bool().unwrap()),
        other => panic!("expected command_result, got {other:?}"),
    };
    assert_eq!(first_id.as_deref(), Some("req-7"));
    assert!(!first_cached);
    // Retry with the SAME id: the gateway replays instead of re-acting.
    client
        .send_command("echo_probe", json!({}), "req-7")
        .unwrap();
    match next_of(&mut client) {
        ClientEvent::CommandResult {
            request_id,
            payload,
        } => {
            assert_eq!(request_id.as_deref(), Some("req-7"));
            assert!(payload["cached"].as_bool().unwrap());
        }
        other => panic!("expected cached command_result, got {other:?}"),
    }
    let _ = dir.send(Directive::Stop);
}

#[test]
fn gateway_error_and_malformed_frames_surface_cleanly() {
    let (url, dir) = start_fake(0);
    let (mut client, _, _) = RemoteClient::connect(&url, TOKEN, STEP).unwrap();
    dir.send(Directive::PushRaw(envelope(
        "error",
        json!({"code": "RATE_LIMITED", "detail": "slow down"}),
        None,
        None,
    )))
    .unwrap();
    match next_of(&mut client) {
        ClientEvent::GatewayError { code, detail, .. } => {
            assert_eq!(
                (code.as_str(), detail.as_str()),
                ("RATE_LIMITED", "slow down")
            );
        }
        other => panic!("expected gateway error, got {other:?}"),
    }
    // Malformed bytes are a client-side protocol error, not a hang.
    dir.send(Directive::PushRaw("this is not json".to_string()))
        .unwrap();
    let err = client.next(STEP).unwrap_err();
    assert!(
        matches!(err, vayren_remote_client::ClientError::Protocol(_)),
        "{err}"
    );
    // The connection itself survives both: ping still answers.
    client.ping().unwrap();
    assert!(matches!(next_of(&mut client), ClientEvent::Pong));
}
