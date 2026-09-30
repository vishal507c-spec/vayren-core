//! Venue-interaction resilience policy — the Rust-owned authority.
//!
//! Single owner (AI_ENTRY.md §1: Execution/Core) for the rules that decide
//! how the trading system behaves when a broker venue misbehaves:
//!
//! | Python (`08_execution/execution/broker/resilience.py`) | Rust (here) |
//!|---|---|
//! | `_RETRY_TABLE` + fail-closed default | `retry_kind` |
//! | `RateLimiter.__post_init__` / `allow` / `used` | `limiter_problem`, `Limiter` |
//! | `clock_drift_ok` | `clock_drift_ok` |
//! | `TimeoutPolicy.__post_init__` | `timeout_problem` |
//! | `BackoffPolicy.__post_init__` / `delay` / `exhausted` | `backoff_problem`, `backoff_delay`, `exhausted` |
//! | `ReconnectPolicy.__post_init__` / `exhausted` | `reconnect_problem`, `exhausted` |
//!
//! The Python module is a vocabulary + handle bridge: it names the retry
//! kinds, holds nothing else. No timer, thread or network call lives here —
//! this module only answers policy questions about facts the caller supplies.

use std::collections::VecDeque;

/// Retry classification codes. Cross the FFI as integers; the names and the
/// `MUST_RECONCILE_FIRST` alias stay Python-side vocabulary.
pub const RETRY_SAFE: i64 = 0;
pub const RETRY_NOT_SAFE: i64 = 1;
pub const RETRY_RECONCILE: i64 = 2;

/// Operation kinds with a known-safe classification. Anything absent is NOT
/// safe to retry (fail-closed classification), exactly as the table read.
pub fn retry_kind(operation: &str) -> i64 {
    match operation {
        "health" | "account" | "positions" | "open_orders" | "stream_poll" => RETRY_SAFE,
        "cancel_order" | "modify_order" | "settle" => RETRY_RECONCILE,
        _ => RETRY_NOT_SAFE,
    }
}

/// Why a sliding-window throttle configuration is unusable (`None` = usable).
///
/// Comparisons mirror the retired Python rule exactly, NaN included: a
/// non-positive window is unusable, an indistinguishable NaN is not.
pub fn limiter_problem(max_requests: i64, window_seconds: f64) -> Option<&'static str> {
    if max_requests <= 0 {
        return Some("max_requests must be positive");
    }
    if window_seconds <= 0.0 {
        return Some("window_seconds must be positive");
    }
    None
}

/// Sliding-window client-side throttle. Exhaustion returns `false` (the
/// caller backs off); it never raises and never retries by itself.
pub struct Limiter {
    max_requests: i64,
    window_seconds: f64,
    hits: VecDeque<f64>,
    rejections_429: i64,
}

impl Limiter {
    /// Build a limiter; a config the policy rejects yields `None`.
    pub fn new(max_requests: i64, window_seconds: f64) -> Option<Self> {
        if limiter_problem(max_requests, window_seconds).is_some() {
            return None;
        }
        Some(Self {
            max_requests,
            window_seconds,
            hits: VecDeque::new(),
            rejections_429: 0,
        })
    }

    /// Consume one quota unit if the window still has room.
    pub fn allow(&mut self, now_epoch: f64) -> bool {
        let cutoff = now_epoch - self.window_seconds;
        while let Some(front) = self.hits.front() {
            if *front <= cutoff {
                self.hits.pop_front();
            } else {
                break;
            }
        }
        if (self.hits.len() as i64) >= self.max_requests {
            return false;
        }
        self.hits.push_back(now_epoch);
        true
    }

    /// Record a venue rate-limit response (diagnostic counter only).
    pub fn record_429(&mut self) {
        self.rejections_429 += 1;
    }

    pub fn used(&self) -> i64 {
        self.hits.len() as i64
    }

    pub fn rejections(&self) -> i64 {
        self.rejections_429
    }
}

/// True when the local clock is within the threshold of the reference.
///
/// `inputs_valid` carries only whether the caller could read the two epochs
/// as numbers (boundary conversion); every decision — including the
/// fail-closed answer for unusable input — is made here.
pub fn clock_drift_ok(local: f64, reference: f64, max_drift: f64, inputs_valid: bool) -> bool {
    if !inputs_valid {
        return false;
    }
    if max_drift < 0.0 {
        return false;
    }
    (local - reference).abs() <= max_drift
}

/// Why a timeout budget is unusable, naming the first field in declaration
/// order (`None` = usable).
pub fn timeout_problem(
    connect_seconds: f64,
    read_seconds: f64,
    submit_seconds: f64,
    reconcile_seconds: f64,
) -> Option<String> {
    for (name, value) in [
        ("connect_seconds", connect_seconds),
        ("read_seconds", read_seconds),
        ("submit_seconds", submit_seconds),
        ("reconcile_seconds", reconcile_seconds),
    ] {
        if value <= 0.0 {
            return Some(format!("{name} must be positive"));
        }
    }
    None
}

/// Why a backoff configuration is unusable. Bounded attempts, capped delay,
/// no randomness (reproducible retries).
pub fn backoff_problem(
    base_seconds: f64,
    factor: f64,
    max_seconds: f64,
    max_attempts: i64,
) -> Option<&'static str> {
    if base_seconds <= 0.0 || factor < 1.0 || max_seconds <= 0.0 {
        return Some("backoff requires positive base/max and factor >= 1");
    }
    if max_attempts < 1 {
        return Some("backoff max_attempts must be >= 1");
    }
    None
}

/// Delay before attempt `attempt` (1-indexed); attempt 0 yields the base.
/// Capped at `max_seconds`. Only SAFE_TO_RETRY operations may use this.
///
/// Beyond the float range Python's `factor ** (attempt - 1)` raises
/// `OverflowError`; here the overshoot saturates to infinity and the cap
/// wins — the configured maximum delay, which is what a capped backoff
/// already means for any attempt that large.
///
/// The cap is an explicit comparison, not `f64::min`: Python's
/// `min(cap, delay)` keeps `cap` when `delay` is NaN and keeps `cap`'s own
/// NaN when the cap is one, and only a plain `<` test reproduces both.
pub fn backoff_delay(base_seconds: f64, factor: f64, max_seconds: f64, attempt: i64) -> f64 {
    if attempt < 1 {
        return base_seconds;
    }
    let grown = base_seconds * factor.powf((attempt - 1) as f64);
    if grown < max_seconds {
        grown
    } else {
        max_seconds
    }
}

/// True when no further attempts are allowed (shared by backoff/reconnect).
pub fn exhausted(attempts_made: i64, max_attempts: i64) -> bool {
    attempts_made >= max_attempts
}

/// Why a reconnect budget is unusable.
pub fn reconnect_problem(max_attempts: i64) -> Option<&'static str> {
    if max_attempts < 1 {
        return Some("reconnect max_attempts must be >= 1");
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unknown_operations_are_never_safe_to_retry() {
        assert_eq!(retry_kind("health"), RETRY_SAFE);
        assert_eq!(retry_kind("stream_poll"), RETRY_SAFE);
        assert_eq!(retry_kind("place_order"), RETRY_NOT_SAFE);
        assert_eq!(retry_kind("cancel_order"), RETRY_RECONCILE);
        assert_eq!(retry_kind("modify_order"), RETRY_RECONCILE);
        assert_eq!(retry_kind("settle"), RETRY_RECONCILE);
        assert_eq!(retry_kind(""), RETRY_NOT_SAFE);
        assert_eq!(retry_kind("Health"), RETRY_NOT_SAFE);
        assert_eq!(retry_kind("anything_new"), RETRY_NOT_SAFE);
    }

    #[test]
    fn limiter_config_fails_closed_on_zero_or_negative() {
        assert_eq!(
            limiter_problem(0, 60.0),
            Some("max_requests must be positive")
        );
        assert_eq!(
            limiter_problem(-1, 60.0),
            Some("max_requests must be positive")
        );
        assert_eq!(
            limiter_problem(10, 0.0),
            Some("window_seconds must be positive")
        );
        assert_eq!(
            limiter_problem(10, -3.0),
            Some("window_seconds must be positive")
        );
        assert_eq!(
            limiter_problem(10, f64::NAN),
            None,
            "the retired rule compared with `<=`, so NaN passed"
        );
        assert_eq!(limiter_problem(1, 0.5), None);
        assert!(Limiter::new(0, 10.0).is_none());
    }

    #[test]
    fn the_window_slides_and_exhaustion_only_denies() {
        let mut limiter = Limiter::new(3, 60.0).unwrap();
        assert!(limiter.allow(0.0));
        assert!(limiter.allow(10.0));
        assert!(limiter.allow(20.0));
        assert_eq!(limiter.used(), 3);
        assert!(!limiter.allow(30.0));
        assert_eq!(limiter.used(), 3);
        // t=61 drops the hit at t=0 (strictly older than the 60s cutoff).
        assert!(limiter.allow(61.0));
        assert_eq!(limiter.used(), 3);
        // t=70's cutoff is 10, so the hit at 10.0 is retired: room again.
        assert!(limiter.allow(70.0));
        assert!(!limiter.allow(75.0));
        // The boundary itself is exclusive on the other side: a hit exactly at
        // the cutoff age is retired too.
        let mut edge = Limiter::new(1, 10.0).unwrap();
        assert!(edge.allow(100.0));
        assert!(!edge.allow(109.999));
        assert!(edge.allow(110.0));
    }

    #[test]
    fn forty_two_counters_are_observable() {
        let mut limiter = Limiter::new(2, 60.0).unwrap();
        assert_eq!(limiter.rejections(), 0);
        limiter.record_429();
        limiter.record_429();
        assert_eq!(limiter.rejections(), 2);
    }

    #[test]
    fn clock_drift_is_symmetric_and_unusable_input_denies() {
        assert!(clock_drift_ok(100.0, 104.0, 5.0, true));
        assert!(clock_drift_ok(104.0, 100.0, 5.0, true));
        assert!(!clock_drift_ok(100.0, 106.0, 5.0, true));
        assert!(clock_drift_ok(100.0, 105.0, 5.0, true));
        assert!(!clock_drift_ok(100.0, 105.0, -1.0, true));
        assert!(!clock_drift_ok(100.0, 100.0, 5.0, false));
        assert!(!clock_drift_ok(f64::NAN, 100.0, 5.0, true));
        assert!(clock_drift_ok(100.0, 100.0, 0.0, true));
    }

    #[test]
    fn timeout_budgets_name_the_first_unusable_field() {
        assert_eq!(timeout_problem(10.0, 5.0, 10.0, 30.0), None);
        assert_eq!(
            timeout_problem(0.0, 5.0, 10.0, 30.0),
            Some("connect_seconds must be positive".to_string())
        );
        assert_eq!(
            timeout_problem(10.0, -1.0, 10.0, 30.0),
            Some("read_seconds must be positive".to_string())
        );
        assert_eq!(
            timeout_problem(10.0, 5.0, 10.0, -30.0),
            Some("reconcile_seconds must be positive".to_string())
        );
        assert_eq!(
            timeout_problem(f64::NAN, 5.0, 10.0, 30.0),
            None,
            "`<= 0` let a NaN budget through in the retired rule"
        );
    }

    #[test]
    fn backoff_grows_geometrically_and_stays_capped() {
        let (base, factor, cap) = (1.0, 2.0, 30.0);
        assert_eq!(backoff_delay(base, factor, cap, 0), 1.0);
        assert_eq!(backoff_delay(base, factor, cap, -5), 1.0);
        assert_eq!(backoff_delay(base, factor, cap, 1), 1.0);
        assert_eq!(backoff_delay(base, factor, cap, 2), 2.0);
        assert_eq!(backoff_delay(base, factor, cap, 5), 16.0);
        assert_eq!(backoff_delay(base, factor, cap, 6), 30.0);
        assert_eq!(backoff_delay(base, factor, cap, 60), 30.0);
        assert_eq!(backoff_delay(0.5, 1.0, 10.0, 9), 0.5);
        assert_eq!(backoff_delay(f64::NAN, 2.0, 30.0, 3), 30.0);
        assert!(backoff_delay(1.0, 2.0, f64::NAN, 3).is_nan());
    }

    #[test]
    fn backoff_and_reconnect_budgets_fail_closed() {
        assert_eq!(backoff_problem(1.0, 2.0, 30.0, 5), None);
        assert_eq!(
            backoff_problem(0.0, 2.0, 30.0, 5),
            Some("backoff requires positive base/max and factor >= 1")
        );
        assert_eq!(
            backoff_problem(1.0, 0.5, 30.0, 5),
            Some("backoff requires positive base/max and factor >= 1")
        );
        assert_eq!(
            backoff_problem(1.0, 2.0, 0.0, 5),
            Some("backoff requires positive base/max and factor >= 1")
        );
        assert_eq!(
            backoff_problem(1.0, 2.0, 30.0, 0),
            Some("backoff max_attempts must be >= 1")
        );
        assert_eq!(reconnect_problem(3), None);
        assert_eq!(
            reconnect_problem(0),
            Some("reconnect max_attempts must be >= 1")
        );
        assert!(exhausted(5, 5));
        assert!(!exhausted(4, 5));
    }
}
