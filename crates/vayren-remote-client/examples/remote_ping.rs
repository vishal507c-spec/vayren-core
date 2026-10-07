//! SAFE read-only gateway probe (operator connectivity test).
//!
//! Connects, proves the token with `hello`, prints `welcome` + the snapshot
//! summary, subscribes, prints a few sequenced arrivals, and exits.
//! This example NEVER sends a command — not START, not anything else. It is
//! the exact safe procedure from the EC2 runbook §5, runnable as:
//! ```sh
//! VAYREN_REMOTE_TOKEN=<token> cargo run -p vayren-remote-client --example remote_ping -- wss://host/vayren/v1
//! ```
//! The token travels in the environment or the OS store only; it is never
//! printed, logged, or persisted by this tool.

use std::time::{Duration, Instant};
use vayren_remote_client::{resolve_remote_token, summarize_snapshot, ClientEvent, RemoteClient};

fn main() {
    let url = std::env::args().nth(1).unwrap_or_else(|| {
        eprintln!("usage: remote_ping <ws(s)://host/vayren/v1>");
        std::process::exit(2);
    });
    let token = match resolve_remote_token() {
        Ok(token) => token,
        Err(err) => {
            eprintln!("remote_ping: {err}");
            std::process::exit(2);
        }
    };
    let (mut client, welcome, snapshot) =
        match RemoteClient::connect(&url, &token, Duration::from_secs(15)) {
            Ok(opened) => opened,
            Err(err) => {
                eprintln!("remote_ping: connect failed: {err}");
                std::process::exit(1);
            }
        };
    // The token has served its single purpose: drop it before doing anything else.
    drop(token);
    match (&welcome, &snapshot) {
        (
            ClientEvent::Welcome {
                connection_id,
                role,
            },
            ClientEvent::Snapshot { sections, resync },
        ) => {
            println!("welcome: connection {connection_id} role {role} resync={resync}");
            for line in summarize_snapshot(sections) {
                println!("snapshot: {line}");
            }
        }
        _ => {
            eprintln!("remote_ping: unexpected handshake pair");
            std::process::exit(1);
        }
    }
    if let Err(err) = client.subscribe(&[], None) {
        eprintln!("remote_ping: subscribe failed: {err}");
        std::process::exit(1);
    }
    let deadline = Instant::now() + Duration::from_secs(10);
    let mut shown = 0;
    while Instant::now() < deadline && shown < 5 {
        match client.next(Duration::from_secs(3)) {
            Ok(ClientEvent::StreamEvent { name, seq, .. }) => {
                println!("event: seq={seq} {name}");
                shown += 1;
            }
            Ok(ClientEvent::StateUpdate { seq, .. }) => {
                println!("state_update: seq={seq}");
                shown += 1;
            }
            Ok(ClientEvent::Snapshot { resync, .. }) => {
                println!("snapshot refresh: resync={resync}");
                shown += 1;
            }
            Ok(ClientEvent::Pong) => {}
            Ok(ClientEvent::GatewayError { code, detail, .. }) => {
                eprintln!("remote_ping: gateway error [{code}]: {detail}");
                std::process::exit(1);
            }
            Ok(other) => {
                eprintln!("remote_ping: unexpected arrival: {other:?}");
                std::process::exit(1);
            }
            Err(vayren_remote_client::ClientError::Timeout) => {}
            Err(err) => {
                eprintln!("remote_ping: stream failed: {err}");
                std::process::exit(1);
            }
        }
    }
    println!("remote_ping: OK (read-only; no command sent)");
}
