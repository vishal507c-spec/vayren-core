//! Remote-gateway smoke probe (`--remote <wss-url>`, read-only).
//!
//! Proves the EXE binary itself reaches the private gateway: resolves the
//! token (env, then the OS store — never a literal, never logged), connects,
//! proves it with `hello`, prints `welcome` + the snapshot summary,
//! subscribes briefly, prints sequenced arrivals, and returns.
//!
//! The probe NEVER sends a command — no UI path calls `send_command` in
//! this change. For the full graphical binding see [`crate::remote_live`]
//! (`--remote-ui` feeds the existing UI read-only through the projection
//! adapter); this probe stays the minimal safe connection test.

use std::time::Duration;
use vayren_remote_client::{resolve_remote_token, summarize_snapshot, ClientEvent, RemoteClient};

/// Extract `--remote <url>` / `--remote=<url>` from the binary argv.
pub fn remote_url_from_argv(argv: &[String]) -> Option<String> {
    let mut index = 0;
    while index < argv.len() {
        let arg = argv[index].as_str();
        if let Some(value) = arg.strip_prefix("--remote=") {
            return Some(value.to_string());
        }
        if arg == "--remote" && index + 1 < argv.len() {
            return Some(argv[index + 1].clone());
        }
        index += 1;
    }
    None
}

/// Run the read-only probe. `Ok` means handshake + snapshot verified.
pub fn run_remote_probe(url: &str) -> Result<(), String> {
    let token = resolve_remote_token().map_err(|err| format!("remote token: {err}"))?;
    println!("Remote probe: connecting...");
    let (mut client, welcome, snapshot) =
        RemoteClient::connect(url, &token, Duration::from_secs(15))
            .map_err(|err| format!("remote connect failed: {err}"))?;
    // The token has served its single purpose: drop it before anything else.
    drop(token);
    let (connection_id, role) = match &welcome {
        ClientEvent::Welcome {
            connection_id,
            role,
        } => (connection_id.clone(), role.clone()),
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
    println!("Remote welcome: connection {connection_id} role {role}");
    for line in summarize_snapshot(&sections) {
        println!("Remote snapshot: {line}");
    }
    client
        .subscribe(&[], None)
        .map_err(|err| format!("remote subscribe failed: {err}"))?;
    let deadline = std::time::Instant::now() + Duration::from_secs(10);
    let mut shown = 0;
    while std::time::Instant::now() < deadline && shown < 8 {
        match client.next(Duration::from_secs(3)) {
            Ok(ClientEvent::StreamEvent { name, seq, .. }) => {
                println!("Remote event: seq={seq} {name}");
                shown += 1;
            }
            Ok(ClientEvent::StateUpdate { seq, .. }) => {
                println!("Remote state_update: seq={seq}");
                shown += 1;
            }
            Ok(ClientEvent::Snapshot { resync, .. }) => {
                println!("Remote snapshot refresh: resync={resync}");
                shown += 1;
            }
            Ok(ClientEvent::Pong) => {}
            Ok(ClientEvent::GatewayError { code, detail, .. }) => {
                return Err(format!("remote gateway error [{code}]: {detail}"));
            }
            Ok(other) => return Err(format!("remote probe: unexpected arrival: {other:?}")),
            Err(vayren_remote_client::ClientError::Timeout) => {}
            Err(err) => return Err(format!("remote stream failed: {err}")),
        }
    }
    println!("Remote probe: OK (read-only; no command sent)");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(words: &[&str]) -> Vec<String> {
        words.iter().map(|w| w.to_string()).collect()
    }

    #[test]
    fn remote_flag_parses_both_forms() {
        assert_eq!(
            remote_url_from_argv(&args(&["--remote", "wss://h/vayren/v1"])).as_deref(),
            Some("wss://h/vayren/v1")
        );
        assert_eq!(
            remote_url_from_argv(&args(&["--remote=wss://h/vayren/v1"])).as_deref(),
            Some("wss://h/vayren/v1")
        );
        assert_eq!(remote_url_from_argv(&args(&["--data-dir", "x"])), None);
        assert_eq!(remote_url_from_argv(&args(&["--remote"])), None);
    }
}
