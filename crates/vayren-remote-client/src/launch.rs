//! Packaged-client launch contract: remote auto-connect without CLI flags.
//!
//! The normal packaged build (Windows EXE, future APK) must connect to the
//! EC2 gateway on its own: no `--remote-ui` flag, no pasted URL, no token
//! entry per launch, and no dependence on any shortcut's arguments. This
//! module is the ONE place that decision lives:
//! [`resolve_launch_mode`] maps argv (+ the build-time packaged default) to
//! [`LaunchMode`]. The shell binary calls it once at startup; shortcuts stay
//! dumb (`vayren-shell.exe --remote-ui` still works, as does a bare launch).
//!
//! Rules (mirroring the existing flag spellings exactly):
//! - Explicit `--remote-ui[ =url]` / `--remote[ =url]` always win.
//! - `--local` forces the developer loop (local Python backend).
//! - Legacy local shortcuts carry `--data-dir` / `--strategy-dir`; their
//!   presence keeps the local backend so existing shortcuts never flip.
//! - Otherwise the packaged build defaults to remote, dev builds to local.
//!
//! Enrollment (first-run / revoked credential) is a data decision, not a
//! second architecture: [`decide_enrollment`] maps token presence + the last
//! auth verdict to [`Enrollment`], and [`enrollment_note`] renders the
//! one-time setup text. The notes name the OS slot only — never a secret.
//! The bootstrap loop (shell side) re-resolves the token per attempt, so a
//! credential provisioned after first launch is picked up with zero restart.

use crate::client::Backoff;
use std::time::Duration;

/// Default private gateway (Tailscale). Overridable per-launch via
/// `--remote-ui=<url>` or [`REMOTE_URL_ENV_VAR`]. No public fallback exists.
pub const REMOTE_DEFAULT_URL: &str = "wss://ip-172-31-12-198.taila678c5.ts.net/vayren/v1";

/// Env var carrying an explicit gateway URL (the token has its own var).
pub const REMOTE_URL_ENV_VAR: &str = "VAYREN_REMOTE_URL";

/// Explicit developer opt-out: force the local backend even in a packaged build.
pub const LOCAL_FLAG: &str = "--local";

/// How the binary starts: remote UI, read-only probe, or local backend.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LaunchMode {
    /// Graphical remote client (read-only; START stays inert by construction).
    Remote { url: String },
    /// Console smoke probe (handshake + snapshot, then exit; no UI).
    Probe { url: String },
    /// Developer loop: spawn the local backend, no gateway involved.
    Local,
}

/// Effective gateway URL: explicit flag value, then env, then the default.
pub fn default_remote_url() -> String {
    std::env::var(REMOTE_URL_ENV_VAR)
        .ok()
        .filter(|url| !url.trim().is_empty())
        .unwrap_or_else(|| REMOTE_DEFAULT_URL.to_string())
}

fn has_flag(argv: &[String], name: &str) -> bool {
    argv.iter()
        .any(|arg| arg == name || arg.starts_with(&format!("{name}=")))
}

/// Map argv to [`LaunchMode`]. `packaged_remote_default` is the build-time
/// packaged marker (`cfg!(feature = "packaged-remote")` at the call site):
/// true in the shipped EXE/APK, false in developer builds.
pub fn resolve_launch_mode(argv: &[String], packaged_remote_default: bool) -> LaunchMode {
    // 1. Explicit remote UI — every spelling the shell has ever accepted.
    let mut index = 0;
    while index < argv.len() {
        let arg = argv[index].as_str();
        if let Some(value) = arg.strip_prefix("--remote-ui=") {
            return LaunchMode::Remote {
                url: if value.trim().is_empty() {
                    default_remote_url()
                } else {
                    value.to_string()
                },
            };
        }
        if arg == "--remote-ui" {
            if index + 1 < argv.len() && !argv[index + 1].starts_with("--") {
                return LaunchMode::Remote {
                    url: argv[index + 1].clone(),
                };
            }
            return LaunchMode::Remote {
                url: default_remote_url(),
            };
        }
        index += 1;
    }
    // 2. Explicit console probe.
    let mut index = 0;
    while index < argv.len() {
        let arg = argv[index].as_str();
        if let Some(value) = arg.strip_prefix("--remote=") {
            return LaunchMode::Probe {
                url: value.to_string(),
            };
        }
        if arg == "--remote" && index + 1 < argv.len() {
            return LaunchMode::Probe {
                url: argv[index + 1].clone(),
            };
        }
        index += 1;
    }
    // 3. Explicit developer opt-out.
    if argv.iter().any(|arg| arg == LOCAL_FLAG) {
        return LaunchMode::Local;
    }
    // 4. Legacy local shortcuts name their data home; they keep the backend
    // they were created for instead of silently flipping to remote.
    if has_flag(argv, "--data-dir") || has_flag(argv, "--strategy-dir") {
        return LaunchMode::Local;
    }
    // 5. No opinion expressed: the packaged build goes remote on its own,
    // developer builds stay local.
    if packaged_remote_default {
        LaunchMode::Remote {
            url: default_remote_url(),
        }
    } else {
        LaunchMode::Local
    }
}

/// First-run credential state for the bootstrap loop.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Enrollment {
    /// A token resolved from env or the OS store (not yet proven).
    Provisioned,
    /// Nothing resolvable: show the one-time setup note, keep retrying.
    Missing,
    /// The gateway refused the stored credential: show re-provision note.
    Rejected,
}

/// Pure enrollment decision: token presence crossed with the last auth
/// verdict. `auth_rejected` outranks presence (a present-but-dead token must
/// re-provision, not spin on "connecting").
pub fn decide_enrollment(token_present: bool, auth_rejected: bool) -> Enrollment {
    if auth_rejected {
        Enrollment::Rejected
    } else if token_present {
        Enrollment::Provisioned
    } else {
        Enrollment::Missing
    }
}

/// Status text for an enrollment state. Names the OS slot only; carries no
/// secret, so it is safe for status surfaces and logs.
pub fn enrollment_note(which: Enrollment) -> &'static str {
    match which {
        Enrollment::Provisioned => "credential present — connecting…",
        Enrollment::Missing => "one-time setup: save the remote token in the OS credential store (service vayren-remote, account remote-token), then wait — this client connects itself",
        Enrollment::Rejected => "stored credential rejected by the gateway — replace the OS credential once (service vayren-remote, account remote-token); this client retries itself",
    }
}

/// Retry timetable for the pre-connect bootstrap loop: 1s, 2s, 4s … capped
/// at 30s. Same shape as the post-connect pump backoff, so a gateway restart
/// and a first-run-before-provisioning look identical to the operator.
pub fn bootstrap_backoff() -> Backoff {
    Backoff::new(Duration::from_secs(1), Duration::from_secs(30))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    static ENV_GUARD: Mutex<()> = Mutex::new(());

    fn args(words: &[&str]) -> Vec<String> {
        words.iter().map(|w| w.to_string()).collect()
    }

    fn with_url_env(value: Option<&str>, check: impl FnOnce()) {
        let _guard = ENV_GUARD.lock().unwrap();
        let previous = std::env::var(REMOTE_URL_ENV_VAR).ok();
        match value {
            Some(url) => std::env::set_var(REMOTE_URL_ENV_VAR, url),
            None => std::env::remove_var(REMOTE_URL_ENV_VAR),
        }
        check();
        match previous {
            Some(url) => std::env::set_var(REMOTE_URL_ENV_VAR, url),
            None => std::env::remove_var(REMOTE_URL_ENV_VAR),
        }
    }

    #[test]
    fn explicit_remote_ui_wins_in_every_spelling() {
        with_url_env(None, || {
            assert!(matches!(
                resolve_launch_mode(&args(&["--remote-ui", "wss://h/vayren/v1"]), true),
                LaunchMode::Remote { url } if url == "wss://h/vayren/v1"
            ));
            assert!(matches!(
                resolve_launch_mode(&args(&["--remote-ui=wss://h/vayren/v1"]), false),
                LaunchMode::Remote { url } if url == "wss://h/vayren/v1"
            ));
            // Bare flag falls back to the built-in default, never to empty.
            match resolve_launch_mode(&args(&["--remote-ui"]), false) {
                LaunchMode::Remote { url } => {
                    assert!(!url.trim().is_empty());
                    assert_eq!(url, REMOTE_DEFAULT_URL);
                }
                other => panic!("bare --remote-ui must be remote, got {other:?}"),
            }
            // Empty equals-form also falls back to the default.
            assert!(matches!(
                resolve_launch_mode(&args(&["--remote-ui="]), false),
                LaunchMode::Remote { url } if url == REMOTE_DEFAULT_URL
            ));
        });
    }

    #[test]
    fn explicit_probe_keeps_both_spellings() {
        with_url_env(None, || {
            assert!(matches!(
                resolve_launch_mode(&args(&["--remote", "wss://h/vayren/v1"]), true),
                LaunchMode::Probe { url } if url == "wss://h/vayren/v1"
            ));
            assert!(matches!(
                resolve_launch_mode(&args(&["--remote=wss://h/vayren/v1"]), true),
                LaunchMode::Probe { url } if url == "wss://h/vayren/v1"
            ));
            // A trailing bare --remote names no URL: not a probe.
            assert_eq!(
                resolve_launch_mode(&args(&["--remote"]), true),
                LaunchMode::Remote {
                    url: REMOTE_DEFAULT_URL.to_string()
                }
            );
        });
    }

    #[test]
    fn remote_ui_outranks_probe_local_and_legacy_shortcuts() {
        with_url_env(None, || {
            let mode = resolve_launch_mode(
                &args(&[
                    "--remote-ui",
                    "--remote",
                    "wss://h/vayren/v1",
                    "--local",
                    "--data-dir",
                    "D:\\data",
                ]),
                false,
            );
            assert!(matches!(mode, LaunchMode::Remote { .. }));
        });
    }

    #[test]
    fn local_flag_forces_the_developer_loop() {
        with_url_env(None, || {
            assert_eq!(
                resolve_launch_mode(&args(&["--local"]), true),
                LaunchMode::Local
            );
            assert_eq!(
                resolve_launch_mode(&args(&["--local", "--data-dir", "x"]), true),
                LaunchMode::Local
            );
        });
    }

    #[test]
    fn legacy_data_dirs_keep_the_local_backend() {
        with_url_env(None, || {
            // Both spellings, both dirs: a legacy shortcut never flips remote.
            for argv in [
                args(&["--data-dir", "D:\\data"]),
                args(&["--data-dir=D:\\data"]),
                args(&["--strategy-dir", "D:\\strats"]),
                args(&["--strategy-dir=D:\\strats"]),
            ] {
                assert_eq!(resolve_launch_mode(&argv, true), LaunchMode::Local);
            }
        });
    }

    #[test]
    fn bare_launch_follows_the_build_marker() {
        with_url_env(None, || {
            assert!(matches!(
                resolve_launch_mode(&args(&[]), true),
                LaunchMode::Remote { url } if url == REMOTE_DEFAULT_URL
            ));
            assert_eq!(resolve_launch_mode(&args(&[]), false), LaunchMode::Local);
        });
    }

    #[test]
    fn unrelated_args_never_change_the_mode() {
        // Shortcut delete/recreate invariance: icon, workdir, window flags and
        // unknown args carry no configuration.
        with_url_env(None, || {
            assert_eq!(
                resolve_launch_mode(&args(&["--foo", "--icon=x", "--start-in=."]), true),
                LaunchMode::Remote {
                    url: REMOTE_DEFAULT_URL.to_string()
                }
            );
            assert_eq!(
                resolve_launch_mode(&args(&["--foo"]), false),
                LaunchMode::Local
            );
        });
    }

    #[test]
    fn url_env_overrides_the_default_but_not_explicit_flags() {
        with_url_env(Some("wss://override/vayren/v1"), || {
            assert!(matches!(
                resolve_launch_mode(&args(&[]), true),
                LaunchMode::Remote { url } if url == "wss://override/vayren/v1"
            ));
            assert!(matches!(
                resolve_launch_mode(&args(&["--remote-ui"]), true),
                LaunchMode::Remote { url } if url == "wss://override/vayren/v1"
            ));
            // An explicit URL still beats the env override.
            assert!(matches!(
                resolve_launch_mode(&args(&["--remote-ui", "wss://h/vayren/v1"]), true),
                LaunchMode::Remote { url } if url == "wss://h/vayren/v1"
            ));
        });
        with_url_env(Some("   "), || {
            // Blank env is not a URL: fall back to the built-in default.
            assert!(matches!(
                resolve_launch_mode(&args(&[]), true),
                LaunchMode::Remote { url } if url == REMOTE_DEFAULT_URL
            ));
        });
    }

    #[test]
    fn enrollment_truth_table() {
        assert_eq!(decide_enrollment(true, false), Enrollment::Provisioned);
        assert_eq!(decide_enrollment(false, false), Enrollment::Missing);
        assert_eq!(decide_enrollment(true, true), Enrollment::Rejected);
        assert_eq!(decide_enrollment(false, true), Enrollment::Rejected);
    }

    #[test]
    fn enrollment_notes_name_the_slot_and_carry_no_secret() {
        let canary = "super-secret-token-value-0123456789";
        for state in [
            Enrollment::Provisioned,
            Enrollment::Missing,
            Enrollment::Rejected,
        ] {
            let note = enrollment_note(state);
            assert!(!note.is_empty());
            assert!(
                !note.contains(canary),
                "status text must never carry a credential"
            );
        }
        assert!(enrollment_note(Enrollment::Missing).contains("vayren-remote"));
        assert!(enrollment_note(Enrollment::Missing).contains("remote-token"));
        assert!(enrollment_note(Enrollment::Rejected).contains("vayren-remote"));
    }

    #[test]
    fn bootstrap_backoff_matches_the_pump_timetable() {
        let mut backoff = bootstrap_backoff();
        assert_eq!(backoff.next_wait(), Duration::from_secs(1));
        assert_eq!(backoff.next_wait(), Duration::from_secs(2));
        assert_eq!(backoff.next_wait(), Duration::from_secs(4));
        for _ in 0..10 {
            assert!(backoff.next_wait() <= Duration::from_secs(30));
        }
        assert_eq!(backoff.next_wait(), Duration::from_secs(30));
        backoff.reset();
        assert_eq!(backoff.next_wait(), Duration::from_secs(1));
    }
}
