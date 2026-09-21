//! Order lifecycle state machine — the Rust-owned authority (AI_ENTRY.md §1:
//! Execution / Core / Performance).
//!
//! This is the single source of truth for which order-state transitions are
//! legal and which states are terminal. The Python execution layer materializes
//! its compatibility view from these functions; it holds no independent table.
//!
//! State codes are the Python `OrderState` declaration order (0..=12). The
//! mapping is pinned by a parity test on both sides.

/// Number of order states in the lifecycle vocabulary.
pub const STATE_COUNT: i32 = 13;

pub const CREATED: i32 = 0;
pub const VALIDATED: i32 = 1;
pub const SUBMITTED: i32 = 2;
pub const ACKNOWLEDGED: i32 = 3;
pub const PARTIALLY_FILLED: i32 = 4;
pub const FILLED: i32 = 5;
pub const REJECTED: i32 = 6;
pub const CANCEL_PENDING: i32 = 7;
pub const CANCELLED: i32 = 8;
pub const MODIFY_PENDING: i32 = 9;
pub const MODIFIED: i32 = 10;
pub const EXPIRED: i32 = 11;
pub const UNKNOWN: i32 = 12;

/// Allowed transitions, mirroring the broker-independent lifecycle contract.
/// `UNKNOWN` may be entered from any non-terminal state (submission/venue
/// uncertainty) but is exited only through explicit reconciliation, which the
/// caller performs outside this table.
const TRANSITIONS: &[(i32, &[i32])] = &[
    // UNKNOWN is enterable from every non-terminal state: the execution
    // engine historically bypassed the table for UNKNOWN targets (venue
    // uncertainty can strike at any pre-terminal point). Terminal states
    // and UNKNOWN itself have no exits.
    (CREATED, &[VALIDATED, REJECTED, UNKNOWN]),
    (VALIDATED, &[SUBMITTED, REJECTED, EXPIRED, UNKNOWN]),
    (SUBMITTED, &[ACKNOWLEDGED, REJECTED, EXPIRED, UNKNOWN]),
    (
        ACKNOWLEDGED,
        &[
            PARTIALLY_FILLED,
            FILLED,
            REJECTED,
            CANCEL_PENDING,
            MODIFY_PENDING,
            EXPIRED,
            UNKNOWN,
        ],
    ),
    (
        PARTIALLY_FILLED,
        &[
            PARTIALLY_FILLED,
            FILLED,
            CANCEL_PENDING,
            MODIFY_PENDING,
            EXPIRED,
            UNKNOWN,
        ],
    ),
    (CANCEL_PENDING, &[CANCELLED, FILLED, UNKNOWN]),
    (MODIFY_PENDING, &[MODIFIED, FILLED, UNKNOWN]),
    (
        MODIFIED,
        &[
            PARTIALLY_FILLED,
            FILLED,
            REJECTED,
            CANCEL_PENDING,
            MODIFY_PENDING,
            EXPIRED,
            UNKNOWN,
        ],
    ),
    (UNKNOWN, &[]),
    (FILLED, &[]),
    (REJECTED, &[]),
    (CANCELLED, &[]),
    (EXPIRED, &[]),
];

fn allowed_targets(state: i32) -> Option<&'static [i32]> {
    TRANSITIONS
        .iter()
        .find(|(from, _)| *from == state)
        .map(|(_, targets)| *targets)
}

/// True when `from -> to` is a legal lifecycle edge. Unknown state codes fail
/// closed (never a legal transition).
pub fn is_legal_transition(from: i32, to: i32) -> bool {
    match allowed_targets(from) {
        Some(targets) => targets.contains(&to),
        None => false,
    }
}

/// True when `state` is terminal (FILLED/REJECTED/CANCELLED/EXPIRED).
pub fn is_terminal(state: i32) -> bool {
    matches!(state, FILLED | REJECTED | CANCELLED | EXPIRED)
}

/// Total number of legal edges in the table (pinned by tests).
pub fn transition_edge_count() -> usize {
    TRANSITIONS.iter().map(|(_, t)| t.len()).sum()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_state_has_a_row() {
        for state in 0..STATE_COUNT {
            assert!(allowed_targets(state).is_some(), "missing row for {state}");
        }
        assert!(allowed_targets(-1).is_none());
        assert!(allowed_targets(STATE_COUNT).is_none());
    }

    #[test]
    fn terminal_states_have_no_exits() {
        for state in [FILLED, REJECTED, CANCELLED, EXPIRED, UNKNOWN] {
            assert_eq!(allowed_targets(state).unwrap().len(), 0);
        }
    }

    #[test]
    fn unknown_is_enterable_from_non_terminal() {
        for state in [
            CREATED,
            VALIDATED,
            SUBMITTED,
            ACKNOWLEDGED,
            PARTIALLY_FILLED,
            CANCEL_PENDING,
            MODIFY_PENDING,
            MODIFIED,
        ] {
            assert!(is_legal_transition(state, UNKNOWN), "{state} -> UNKNOWN");
        }
        for state in [FILLED, REJECTED, CANCELLED, EXPIRED, UNKNOWN] {
            assert!(!is_legal_transition(state, UNKNOWN), "{state} -/-> UNKNOWN");
        }
    }

    #[test]
    fn unknown_has_no_direct_exit() {
        for to in 0..STATE_COUNT {
            assert!(!is_legal_transition(UNKNOWN, to));
        }
    }

    #[test]
    fn core_happy_paths() {
        assert!(is_legal_transition(CREATED, VALIDATED));
        assert!(is_legal_transition(VALIDATED, SUBMITTED));
        assert!(is_legal_transition(SUBMITTED, ACKNOWLEDGED));
        assert!(is_legal_transition(ACKNOWLEDGED, PARTIALLY_FILLED));
        assert!(is_legal_transition(PARTIALLY_FILLED, FILLED));
        assert!(is_legal_transition(ACKNOWLEDGED, CANCEL_PENDING));
        assert!(is_legal_transition(CANCEL_PENDING, CANCELLED));
        assert!(is_legal_transition(ACKNOWLEDGED, MODIFY_PENDING));
        assert!(is_legal_transition(MODIFY_PENDING, MODIFIED));
    }

    #[test]
    fn illegal_jumps_fail_closed() {
        assert!(!is_legal_transition(CREATED, FILLED));
        assert!(!is_legal_transition(FILLED, CREATED));
        assert!(!is_legal_transition(CANCELLED, FILLED));
        assert!(!is_legal_transition(-5, 3));
        assert!(!is_legal_transition(3, 99));
    }

    #[test]
    fn terminal_membership() {
        for state in [FILLED, REJECTED, CANCELLED, EXPIRED] {
            assert!(is_terminal(state));
        }
        for state in [CREATED, SUBMITTED, ACKNOWLEDGED, UNKNOWN, PARTIALLY_FILLED] {
            assert!(!is_terminal(state));
        }
    }

    #[test]
    fn edge_count_is_pinned() {
        assert_eq!(transition_edge_count(), 37);
    }
}
