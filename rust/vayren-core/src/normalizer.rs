//! Stream normalizer twin — reorder, dedupe, staleness, heartbeat.
//!
//! Rust port of `08_execution/execution/market_data/normalizer.py`
//! (`StreamNormalizer` + `StreamStats` + `NormalizerConfig`). Every provider
//! is untrusted at the transport edge; sequence gaps, duplicate deliveries,
//! out-of-order arrivals and silent stalls are normalized here so downstream
//! stages see a clean ordered stream. Dropped/duplicates are counted, never
//! silently swallowed.
//!
//! This module is a required dependency of the `live_session` pipeline twin
//! (the session calls `observe`/`check_health`/`is_stale` per event). The
//! Python `StreamNormalizer` stays the production authority; behavior here
//! is identical, pinned by parity tests.
//!
//! Two deliberate narrowings, both outside production shapes:
//! - Python accepts any object and raises `MarketDataError` for
//!   non-`MarketEvent` arrivals; Rust takes [`StreamInput`], so malformed
//!   arrivals are unrepresentable (the error path becomes a type-level
//!   guarantee — fail-closed by construction).
//! - Python `min(buf, key=seq)` keeps the FIRST lowest on ties and
//!   `sorted` is stable; this port keeps the first minimum explicitly and
//!   uses stable sort, so duplicate buffered seqs behave identically.

use std::collections::{HashMap, HashSet, VecDeque};

use crate::execution_events::MarketEvent;

/// Per-symbol stream counters (mirrors `StreamStats`).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct StreamStats {
    pub accepted: u64,
    pub duplicates: u64,
    pub gaps: u64,
    pub evicted: u64,
    pub heartbeats: u64,
    pub stale_flags: u64,
}

/// Buffer and timeout tuning (mirrors `NormalizerConfig` defaults).
#[derive(Debug, Clone, PartialEq)]
pub struct NormalizerConfig {
    pub max_reorder_buffer: usize,
    pub stale_after_seconds: f64,
    pub heartbeat_timeout_seconds: f64,
}

impl Default for NormalizerConfig {
    fn default() -> Self {
        Self {
            max_reorder_buffer: 128,
            stale_after_seconds: 60.0,
            heartbeat_timeout_seconds: 30.0,
        }
    }
}

/// One arrival at the transport edge (mirrors the `isinstance` dispatch in
/// `observe`: heartbeats take the health branch, everything else the
/// sequence branch).
#[derive(Debug, Clone, PartialEq)]
pub enum StreamInput {
    Heartbeat {
        symbol: String,
        timestamp: String,
        seq: i64,
    },
    Event(MarketEvent),
}

/// Per-symbol ordered gate with duplicate suppression and health signals.
#[derive(Debug, Default)]
pub struct StreamNormalizer {
    config: NormalizerConfig,
    last_seq: HashMap<String, i64>,
    buffer: HashMap<String, VecDeque<MarketEvent>>,
    last_event_epoch: HashMap<String, f64>,
    last_heartbeat_epoch: HashMap<String, f64>,
    stats: StreamStats,
    stale: HashSet<String>,
}

impl StreamNormalizer {
    pub fn new(config: NormalizerConfig) -> Self {
        Self {
            config,
            ..Default::default()
        }
    }

    pub fn stats(&self) -> &StreamStats {
        &self.stats
    }

    /// Accept one raw arrival; return newly deliverable events in order.
    pub fn observe(
        &mut self,
        input: StreamInput,
        now_epoch: f64,
    ) -> Result<Vec<MarketEvent>, String> {
        let (symbol, seq) = match &input {
            StreamInput::Heartbeat { symbol, .. } => {
                self.stats.heartbeats += 1;
                self.last_heartbeat_epoch.insert(symbol.clone(), now_epoch);
                self.stale.remove(symbol);
                return Ok(Vec::new());
            }
            StreamInput::Event(event) => (event.symbol.clone(), event.seq),
        };
        let last = self.last_seq.get(&symbol).copied().unwrap_or(0);
        if seq <= last {
            self.stats.duplicates += 1;
            return Ok(Vec::new());
        }
        let buf = self.buffer.entry(symbol.clone()).or_default();
        if seq > last + 1 + buf.len() as i64 {
            self.stats.gaps += 1;
        }
        let event = match input {
            StreamInput::Event(event) => event,
            StreamInput::Heartbeat { .. } => unreachable!("handled above"),
        };
        buf.push_back(event);
        if buf.len() > self.config.max_reorder_buffer {
            // First lowest wins (mirrors Python `min`, which keeps the
            // first minimum on ties — `min_by_key` would keep the last).
            let mut lowest_idx = 0;
            for (idx, candidate) in buf.iter().enumerate() {
                if candidate.seq < buf[lowest_idx].seq {
                    lowest_idx = idx;
                }
            }
            let lowest_seq = buf[lowest_idx].seq;
            if lowest_seq == self.last_seq.get(&symbol).copied().unwrap_or(0) + 1 {
                // Dropping the next deliverable: skip exactly one lost event.
                buf.remove(lowest_idx);
                self.last_seq.insert(symbol.clone(), lowest_seq);
            } else {
                // Unfillable hole below `lowest`: jump the watermark to it,
                // keeping `lowest` buffered for delivery.
                self.last_seq.insert(symbol.clone(), lowest_seq - 1);
            }
            self.stats.evicted += 1;
        }
        let expected = self.last_seq.get(&symbol).copied().unwrap_or(0) + 1;
        let mut ordered: Vec<MarketEvent> = buf.drain(..).collect();
        // Stable sort: equal seqs keep arrival order (mirrors `sorted`).
        ordered.sort_by_key(|e| e.seq);
        let mut ready = Vec::new();
        let mut waiting = VecDeque::new();
        let mut cursor = expected;
        for candidate in ordered {
            if candidate.seq == cursor {
                ready.push(candidate);
                cursor += 1;
            } else if candidate.seq < cursor {
                self.stats.duplicates += 1;
            } else {
                waiting.push_back(candidate);
            }
        }
        *buf = waiting;
        if !ready.is_empty() {
            self.last_seq
                .insert(symbol.clone(), ready.last().unwrap().seq);
            self.last_event_epoch.insert(symbol, now_epoch);
            self.stats.accepted += ready.len() as u64;
        }
        Ok(ready)
    }

    /// `(healthy, reason)`: heartbeat gaps and event stalls flag stale.
    /// Every failing call counts (mirrors the Python increments).
    pub fn check_health(&mut self, symbol: &str, now_epoch: f64) -> (bool, &'static str) {
        if let Some(last_hb) = self.last_heartbeat_epoch.get(symbol).copied() {
            if now_epoch - last_hb > self.config.heartbeat_timeout_seconds {
                self.stats.stale_flags += 1;
                self.stale.insert(symbol.to_string());
                return (false, "heartbeat timeout");
            }
        }
        if let Some(last_event) = self.last_event_epoch.get(symbol).copied() {
            if now_epoch - last_event > self.config.stale_after_seconds {
                self.stats.stale_flags += 1;
                self.stale.insert(symbol.to_string());
                return (false, "stale market data");
            }
        }
        (true, "ok")
    }

    pub fn is_stale(&self, symbol: &str) -> bool {
        self.stale.contains(symbol)
    }

    pub fn expected_seq(&self, symbol: &str) -> i64 {
        self.last_seq.get(symbol).copied().unwrap_or(0) + 1
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn event(symbol: &str, seq: i64) -> StreamInput {
        StreamInput::Event(MarketEvent {
            symbol: symbol.to_string(),
            timestamp: "2026-01-01 09:15:00".to_string(),
            seq,
            source: "test".to_string(),
        })
    }

    fn heartbeat(symbol: &str, now: f64) -> (StreamInput, f64) {
        (
            StreamInput::Heartbeat {
                symbol: symbol.to_string(),
                timestamp: "2026-01-01 09:15:00".to_string(),
                seq: 0,
            },
            now,
        )
    }

    fn seqs(events: &[MarketEvent]) -> Vec<i64> {
        events.iter().map(|e| e.seq).collect()
    }

    #[test]
    fn in_order_events_deliver_immediately() {
        let mut normalizer = StreamNormalizer::new(NormalizerConfig::default());
        for seq in 1..=3 {
            let ready = normalizer.observe(event("A", seq), seq as f64).unwrap();
            assert_eq!(seqs(&ready), vec![seq]);
        }
        assert_eq!(normalizer.stats.accepted, 3);
        assert_eq!(normalizer.expected_seq("A"), 4);
    }

    #[test]
    fn duplicates_are_counted_never_delivered() {
        let mut normalizer = StreamNormalizer::new(NormalizerConfig::default());
        assert_eq!(normalizer.observe(event("A", 1), 1.0).unwrap().len(), 1);
        assert!(normalizer.observe(event("A", 1), 2.0).unwrap().is_empty());
        assert_eq!(normalizer.stats.duplicates, 1);
        assert_eq!(normalizer.stats.accepted, 1);
    }

    #[test]
    fn gap_then_fill_delivers_in_order() {
        let mut normalizer = StreamNormalizer::new(NormalizerConfig::default());
        assert!(normalizer.observe(event("A", 1), 1.0).unwrap().len() == 1);
        // Seq 3 arrives before 2: buffered, nothing deliverable, gap counted.
        assert!(normalizer.observe(event("A", 3), 2.0).unwrap().is_empty());
        assert_eq!(normalizer.stats.gaps, 1);
        let ready = normalizer.observe(event("A", 2), 3.0).unwrap();
        assert_eq!(seqs(&ready), vec![2, 3]);
        assert_eq!(normalizer.expected_seq("A"), 4);
    }

    #[test]
    fn heartbeat_counts_and_clears_stale() {
        let mut normalizer = StreamNormalizer::new(NormalizerConfig::default());
        let (hb, now) = heartbeat("A", 100.0);
        assert!(normalizer.observe(hb, now).unwrap().is_empty());
        assert_eq!(normalizer.stats.heartbeats, 1);
        // Heartbeat 100s ago with a 30s timeout: stale.
        assert_eq!(
            normalizer.check_health("A", 131.0),
            (false, "heartbeat timeout")
        );
        assert!(normalizer.is_stale("A"));
        // A fresh heartbeat clears the stale flag.
        let (hb2, now2) = heartbeat("A", 200.0);
        normalizer.observe(hb2, now2).unwrap();
        assert!(!normalizer.is_stale("A"));
        assert_eq!(normalizer.check_health("A", 200.0), (true, "ok"));
    }

    #[test]
    fn stalled_events_flag_stale_market_data() {
        let mut normalizer = StreamNormalizer::new(NormalizerConfig::default());
        normalizer.observe(event("A", 1), 10.0).unwrap();
        assert_eq!(normalizer.check_health("A", 20.0), (true, "ok"));
        assert_eq!(
            normalizer.check_health("A", 71.0),
            (false, "stale market data")
        );
        assert_eq!(normalizer.stats.stale_flags, 1);
    }

    #[test]
    fn overflow_drops_next_deliverable_and_skips_one() {
        let mut normalizer = StreamNormalizer::new(NormalizerConfig {
            max_reorder_buffer: 2,
            ..Default::default()
        });
        for seq in 1..=3 {
            normalizer.observe(event("A", seq), seq as f64).unwrap();
        }
        // Buffer seqs 5, 6 (4 missing), then the late 4 overflows: it is the
        // first minimum and equals last+1, so exactly it is skipped while 5
        // and 6 deliver (live-Python parity: ready [5, 6]).
        normalizer.observe(event("A", 5), 4.0).unwrap();
        normalizer.observe(event("A", 6), 5.0).unwrap();
        let ready = normalizer.observe(event("A", 4), 6.0).unwrap();
        assert_eq!(seqs(&ready), vec![5, 6]);
        assert_eq!(normalizer.stats.evicted, 1);
        assert_eq!(normalizer.stats.gaps, 2);
        let ready = normalizer.observe(event("A", 7), 7.0).unwrap();
        assert_eq!(seqs(&ready), vec![7]);
        assert_eq!(normalizer.stats.accepted, 6);
    }

    #[test]
    fn overflow_with_hole_jumps_watermark() {
        let mut normalizer = StreamNormalizer::new(NormalizerConfig {
            max_reorder_buffer: 2,
            ..Default::default()
        });
        // Seqs 4, 5 buffered with last == 0: lowest (4) is not last+1, so
        // the watermark jumps to 3 and the whole window delivers
        // (live-Python parity: ready [4, 5, 6], gaps 3).
        normalizer.observe(event("A", 4), 1.0).unwrap();
        normalizer.observe(event("A", 5), 2.0).unwrap();
        let ready = normalizer.observe(event("A", 6), 3.0).unwrap();
        assert_eq!(seqs(&ready), vec![4, 5, 6]);
        assert_eq!(normalizer.stats.evicted, 1);
        assert_eq!(normalizer.stats.gaps, 3);
    }

    #[test]
    fn duplicate_buffered_seq_keeps_first_minimum() {
        // Python `min` keeps the FIRST lowest on ties; `sorted` is stable.
        let mut normalizer = StreamNormalizer::new(NormalizerConfig {
            max_reorder_buffer: 3,
            ..Default::default()
        });
        normalizer.observe(event("A", 1), 1.0).unwrap();
        normalizer.observe(event("A", 4), 2.0).unwrap();
        normalizer.observe(event("A", 4), 3.0).unwrap();
        normalizer.observe(event("A", 5), 4.0).unwrap();
        // Overflow with tied lowest (4, 4): the first 4 is the eviction
        // candidate; it is not last+1 (last is 1), so the watermark jumps
        // to 3. Delivery then consumes the second 4 as a duplicate
        // (live-Python parity: ready [4, 5, 6], dup 1 already).
        let ready = normalizer.observe(event("A", 6), 5.0).unwrap();
        assert_eq!(seqs(&ready), vec![4, 5, 6]);
        assert_eq!(normalizer.stats.evicted, 1);
        assert_eq!(normalizer.stats.duplicates, 1);
        assert_eq!(normalizer.stats.gaps, 4);
        // Seqs below the watermark count as duplicates too.
        assert!(normalizer.observe(event("A", 2), 6.0).unwrap().is_empty());
        assert_eq!(normalizer.stats.duplicates, 2);
        assert_eq!(normalizer.stats.accepted, 4);
    }
}
