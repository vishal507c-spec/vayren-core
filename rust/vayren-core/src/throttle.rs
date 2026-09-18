//! API throttle — enforces minimum spacing between broker requests.
//!
//! Rust port of 02_data/data/throttle.py. Sequential requests only, at most
//! one call per `min_seconds`. Original engine's `_Throttle` preserved.

use std::time::{Duration, Instant};

/// Enforces a minimum spacing between API calls.
#[derive(Debug, Clone)]
pub struct Throttle {
    min_interval: Duration,
    last: Option<Instant>,
}

impl Throttle {
    /// Create a throttle with a minimum interval in seconds.
    pub fn new(min_seconds: f64) -> Self {
        Self {
            min_interval: Duration::from_secs_f64(min_seconds.max(0.0)),
            last: None,
        }
    }

    /// Block until the minimum interval has elapsed since the last call.
    pub fn wait(&mut self) {
        if let Some(last) = self.last {
            let elapsed = last.elapsed();
            if elapsed < self.min_interval {
                let gap = self.min_interval - elapsed;
                std::thread::sleep(gap);
            }
        }
        self.last = Some(Instant::now());
    }

    /// Check if enough time has passed without blocking.
    pub fn ready(&self) -> bool {
        self.last
            .map_or(true, |last| last.elapsed() >= self.min_interval)
    }

    /// Reset the throttle (next call will not wait).
    pub fn reset(&mut self) {
        self.last = None;
    }
}

impl Default for Throttle {
    fn default() -> Self {
        Self::new(0.5)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Instant;

    #[test]
    fn first_call_does_not_block() {
        let mut throttle = Throttle::new(1.0);
        let start = Instant::now();
        throttle.wait();
        assert!(start.elapsed().as_millis() < 50);
    }

    #[test]
    fn second_call_blocks() {
        let mut throttle = Throttle::new(0.1);
        throttle.wait();
        let start = Instant::now();
        throttle.wait();
        let elapsed = start.elapsed().as_secs_f64();
        assert!(elapsed >= 0.09 && elapsed < 0.15);
    }

    #[test]
    fn ready_returns_true_when_interval_elapsed() {
        let mut throttle = Throttle::new(0.05);
        assert!(throttle.ready());
        throttle.wait();
        assert!(!throttle.ready());
        std::thread::sleep(Duration::from_millis(60));
        assert!(throttle.ready());
    }

    #[test]
    fn reset_clears_last_call() {
        let mut throttle = Throttle::new(1.0);
        throttle.wait();
        assert!(!throttle.ready());
        throttle.reset();
        assert!(throttle.ready());
    }

    #[test]
    fn negative_interval_becomes_zero() {
        let throttle = Throttle::new(-1.0);
        assert_eq!(throttle.min_interval, Duration::ZERO);
    }
}
