//! Regression tests for VAYREN Single-Click Instant START / STOP Optimization:
//! 1. Single-click START
//! 2. Single-click STOP
//! 3. Duplicate-click protection
//! 4. Slow backend response handling
//! 5. Network disconnection / pending action expiration
//! 6. Backend timeout handling
//! 7. START blocked by market-data streaming failure
//! 8. STOP during pending start
//! 9. Repeated START/STOP requests sequence
//! 10. Correct UI/backend state synchronization
//! 11. UI conflicting actions disabled during transition
//! 12. Instant synchronous action dispatching

use std::cell::RefCell;
use std::rc::Rc;
use std::sync::mpsc;
use vayren_domain::live::{
    project, ExecMode, Gate, GateStatus, LiveState, SessionStatus, SymbolPick,
};
use vayren_shell::remote_live::{apply_remote_sections, dispatch_pending_actions, RemoteCommand};

fn setup_ready_state() -> LiveState {
    let mut state = LiveState::default();
    state.bridge_wired = true;
    state.host_mode = true;
    state.remote_commands_allowed = true;
    state.mode = ExecMode::Backtest;
    state.broker.connected = Some(true);
    state.broker.name = "FYERS".to_string();
    state.broker.status = "CONNECTED".to_string();
    state.capital.available_margin = Some(100_000.0);
    state.strategies = vec!["MomentumAlpha".into()];
    state.strategy_index = Some(0);
    state.timeframes = vec!["5m".into()];
    state.timeframe_index = Some(0);
    state.quantity = 10.0;
    state.symbols = vec![SymbolPick {
        symbol: "NSE:RELIANCE".into(),
        checked: true,
        ltp: Some(2500.0),
        change_pct: Some(1.5),
        in_store: true,
    }];
    state.gates = vec![
        Gate {
            name: "BROKER SESSION".into(),
            status: GateStatus::Ready,
            reason: "authenticated".into(),
        },
        Gate {
            name: "Market Data Streaming".into(),
            status: GateStatus::Ready,
            reason: "venue feed connected".into(),
        },
        Gate {
            name: "ORDER STREAM".into(),
            status: GateStatus::Ready,
            reason: "connected".into(),
        },
        Gate {
            name: "STRATEGY INTEGRITY".into(),
            status: GateStatus::Ready,
            reason: "compiled".into(),
        },
        Gate {
            name: "RISK ENVELOPE".into(),
            status: GateStatus::Ready,
            reason: "limits within bounds".into(),
        },
    ];
    state
}

#[test]
fn test_1_single_click_start() {
    let mut state = setup_ready_state();
    assert_eq!(state.session, SessionStatus::Stopped);
    assert!(state.can_start());

    // Single click executes start workflow immediately
    state.start();
    assert_eq!(state.session, SessionStatus::Starting);
    assert_eq!(state.pending_action.as_deref(), Some("start"));

    let view = project(&state);
    assert!(view.setup.pending_start);
    assert!(!view.setup.can_start);

    // Emitted exactly one start action
    let actions = state.drain_actions();
    assert_eq!(actions.len(), 1);
    assert!(actions[0].contains("\"action\":\"start\""));
}

#[test]
fn test_2_single_click_stop() {
    let mut state = setup_ready_state();
    state.session = SessionStatus::Running;
    assert!(state.can_stop());

    // Single click executes stop workflow immediately
    state.stop();
    // NEVER fakes Stopped prematurely - transitions to Stopping
    assert_eq!(state.session, SessionStatus::Stopping);
    assert_eq!(state.pending_action.as_deref(), Some("stop"));

    let view = project(&state);
    assert!(view.setup.pending_stop);
    assert!(!view.setup.can_stop);

    // Emitted stop action
    let actions = state.drain_actions();
    assert_eq!(actions.len(), 1);
    assert!(actions[0].contains("\"action\":\"stop\""));
}

#[test]
fn test_3_duplicate_click_protection() {
    let mut state = setup_ready_state();
    // First click on START
    state.start();
    assert_eq!(state.session, SessionStatus::Starting);
    let actions_first = state.drain_actions();
    assert_eq!(actions_first.len(), 1);

    // Second click on START while already STARTING must be ignored
    state.start();
    let actions_second = state.drain_actions();
    assert!(
        actions_second.is_empty(),
        "Duplicate START click must not emit redundant action"
    );

    // Now engine confirms RUNNING
    state.session = SessionStatus::Running;
    state.pending_action = None;

    // Another click on START while RUNNING must be ignored
    state.start();
    assert!(
        state.drain_actions().is_empty(),
        "START click while already RUNNING must not emit action"
    );

    // First click on STOP
    state.stop();
    assert_eq!(state.session, SessionStatus::Stopping);
    let actions_stop_first = state.drain_actions();
    assert_eq!(actions_stop_first.len(), 1);

    // Second click on STOP while already STOPPING must be ignored
    state.stop();
    assert!(
        state.drain_actions().is_empty(),
        "Duplicate STOP click must not emit redundant action"
    );
}

#[test]
fn test_4_slow_backend_response() {
    let mut state = setup_ready_state();
    state.start();
    assert_eq!(state.session, SessionStatus::Starting);

    // While backend is slow, UI state projection continues to report pending_start
    let view = project(&state);
    assert!(view.setup.pending_start);
    assert!(!view.setup.can_start);
    assert!(!view.setup.start_clickable);

    // When backend acknowledges after a delay, state flips honestly
    let sections = serde_json::json!({
        "strategy": {
            "status": "RUNNING",
            "mode": "PAPER",
            "reason": ""
        }
    });
    apply_remote_sections(&mut state, &sections, true);
    state.pending_action = None;

    assert_eq!(state.session, SessionStatus::Running);
    let running_view = project(&state);
    assert!(!running_view.setup.pending_start);
    assert!(running_view.setup.can_stop);
}

#[test]
fn test_5_and_6_backend_timeout_and_disconnection() {
    let mut state = setup_ready_state();
    state.start();
    assert_eq!(state.session, SessionStatus::Starting);

    // Simulate timeout expiration: pending_since exceeds timeout
    state.pending_since = Some(
        std::time::Instant::now()
            .checked_sub(vayren_domain::live::PENDING_ACTION_TIMEOUT)
            .unwrap(),
    );

    let expired = state.expire_pending_action();
    assert!(expired);
    assert_eq!(state.pending_action, None);
    // Never fakes Stopped or Running on timeout: marks ERROR
    assert_eq!(state.session, SessionStatus::Error);
    assert!(state
        .action_note
        .as_deref()
        .unwrap_or("")
        .contains("timed out"));
}

#[test]
fn test_7_start_blocked_by_market_data_failure() {
    let mut state = setup_ready_state();
    state.mode = ExecMode::Live;
    state.live_confirmed = true;

    // Set Market Data Streaming gate to NotReady
    if let Some(gate) = state
        .gates
        .iter_mut()
        .find(|g| g.name.eq_ignore_ascii_case("Market Data Streaming"))
    {
        gate.status = GateStatus::NotReady;
        gate.reason = "feed disconnected".into();
    }

    assert!(
        !state.venue_gates_ready(),
        "Venue gates must not be ready when market data streaming is not ready"
    );
    let blockers = state.start_blockers();
    assert!(
        blockers
            .iter()
            .any(|b| b.contains("market data streaming is not ready")),
        "START must be explicitly blocked with market data streaming reason, got: {:?}",
        blockers
    );
    assert!(!state.can_start());

    // Single click on START while blocked must immediately surface the exact blocker in action_note
    state.start();
    let note = state.action_note.as_deref().unwrap();
    assert!(note.starts_with("START BLOCKED:"));
    assert!(note.contains("market data streaming is not ready"));
    let view = vayren_domain::live::project(&state);
    assert_eq!(view.bar.note_tone, 3);
    assert!(view.bar.note.starts_with("START BLOCKED:"));
    assert_eq!(state.session, SessionStatus::Stopped);
    assert!(state.pending_action.is_none());
}

#[test]
fn test_8_stop_during_pending_start() {
    let mut state = setup_ready_state();
    state.start();
    assert_eq!(state.session, SessionStatus::Starting);
    assert_eq!(state.pending_action.as_deref(), Some("start"));
    let _ = state.drain_actions();

    // User hits STOP while START is still in flight:
    state.stop();
    assert_eq!(state.session, SessionStatus::Stopping);
    assert_eq!(state.pending_action.as_deref(), Some("stop"));

    let actions = state.drain_actions();
    assert_eq!(actions.len(), 1);
    assert!(actions[0].contains("\"action\":\"stop\""));
}

#[test]
fn test_9_repeated_start_stop_sequence() {
    let mut state = setup_ready_state();

    // Start -> backend running -> Stop -> backend stopped -> Start
    state.start();
    assert_eq!(state.session, SessionStatus::Starting);
    state.drain_actions();

    let running_sec = serde_json::json!({
        "strategy": { "id": "MomentumAlpha", "name": "MomentumAlpha", "status": "RUNNING", "mode": "BACKTEST", "reason": "" }
    });
    apply_remote_sections(&mut state, &running_sec, true);
    state.pending_action = None;
    assert_eq!(state.session, SessionStatus::Running);

    state.stop();
    assert_eq!(state.session, SessionStatus::Stopping);
    state.drain_actions();

    let stopped_sec = serde_json::json!({
        "strategy": { "id": "MomentumAlpha", "name": "MomentumAlpha", "status": "STOPPED", "mode": "BACKTEST", "reason": "" }
    });
    apply_remote_sections(&mut state, &stopped_sec, true);
    state.pending_action = None;
    assert_eq!(state.session, SessionStatus::Stopped);

    // Can start again cleanly
    state.start();
    assert_eq!(state.session, SessionStatus::Starting);
    assert_eq!(state.drain_actions().len(), 1);
}

#[test]
fn test_10_correct_ui_backend_state_synchronization() {
    let mut state = setup_ready_state();
    let sections = serde_json::json!({
        "strategy": {
            "status": "STOPPING",
            "mode": "PAPER",
            "reason": "operator requested halt"
        }
    });
    apply_remote_sections(&mut state, &sections, true);
    assert_eq!(state.session, SessionStatus::Stopping);

    let sections_running = serde_json::json!({
        "strategy": {
            "status": "STARTING",
            "mode": "PAPER",
            "reason": "bootstrapping"
        }
    });
    apply_remote_sections(&mut state, &sections_running, true);
    assert_eq!(state.session, SessionStatus::Starting);
}

#[test]
fn test_11_ui_slint_conflicting_actions_guarded() {
    let src = include_str!("../ui/live.slint");
    assert!(
        src.contains("property <bool> setup-editable: !root.setup.can-stop && !root.setup.pending-start && !root.setup.pending-stop;"),
        "live.slint must declare setup-editable guarding both pending transitions and active execution"
    );
    assert!(
        src.contains("enabled: root.setup-editable && root.strategy-names.length > 0;"),
        "Strategy selector must be disabled during transitions"
    );
}

#[test]
fn test_12_instant_synchronous_action_dispatch() {
    let state = Rc::new(RefCell::new(setup_ready_state()));
    let (tx, rx) = mpsc::channel::<RemoteCommand>();

    // When an action is queued, dispatch_pending_actions delivers immediately
    state.borrow_mut().start();
    assert_eq!(state.borrow().session, SessionStatus::Starting);

    dispatch_pending_actions(&mut state.borrow_mut(), &tx);

    let cmd = rx
        .try_recv()
        .expect("Command must be dispatched immediately");
    assert_eq!(cmd.action, "start");
    assert!(rx.try_recv().is_err(), "Only 1 command should be queued");
}

#[test]
fn test_delayed_backend_response_intermediate_snapshot_does_not_clear_pending_start() {
    let mut state = setup_ready_state();
    state.start();
    assert_eq!(state.session, SessionStatus::Starting);
    assert_eq!(state.pending_action.as_deref(), Some("start"));

    // Intermediate background snapshot arrives while start is still in flight on backend
    let intermediate_snapshot = serde_json::json!({
        "strategy": {
            "status": "STOPPED",
            "mode": "PAPER",
            "reason": "operator stop"
        }
    });
    apply_remote_sections(&mut state, &intermediate_snapshot, false);

    // MUST NOT clear pending_action and MUST NOT revert to Stopped
    assert_eq!(
        state.session,
        SessionStatus::Starting,
        "Snapshot must not abort in-flight start request"
    );
    assert_eq!(
        state.pending_action.as_deref(),
        Some("start"),
        "pending_action must stay intact"
    );

    // Now backend completes and reports RUNNING
    let running_snapshot = serde_json::json!({
        "strategy": {
            "status": "RUNNING",
            "mode": "PAPER",
            "reason": ""
        }
    });
    apply_remote_sections(&mut state, &running_snapshot, false);
    assert_eq!(state.session, SessionStatus::Running);
    assert!(state.pending_action.is_none());
}

#[test]
fn test_start_refusal_clears_pending_and_displays_exact_blocker() {
    let mut state = setup_ready_state();
    state.start();
    assert_eq!(state.session, SessionStatus::Starting);
    assert_eq!(state.pending_action.as_deref(), Some("start"));

    // Backend refuses with exact blocker
    let refusal_snapshot = serde_json::json!({
        "strategy": {
            "status": "STOPPED",
            "mode": "PAPER",
            "reason": "START blocked: broker adapter disconnected"
        },
        "blockers": ["broker adapter disconnected"]
    });
    apply_remote_sections(&mut state, &refusal_snapshot, false);

    assert_eq!(state.session, SessionStatus::Stopped);
    assert!(state.pending_action.is_none());
    assert!(state.start_clickable());
}
