//! DATA_PROCESSING event contracts — Rust equivalents of `02_data/data/events/`.
//!
//! Payload names, fields and command/fact roles mirror the Python frozen
//! dataclasses exactly (see `90_brain/event_catalog.md` §2). Dates cross as
//! `"YYYY-MM-DD"` / `"YYYY-MM-DD HH:MM:SS"` strings, states as `DLState.name()`
//! spellings — the same shapes the legacy worker emits today.
//!
//! The production Python worker stays the authority; constructor functions
//! below mirror its reporter → event mapping (`worker.py`) so future
//! pure-Rust consumers emit identical facts on `crate::event_bus::EventBus`.

use crate::download::{format_date, format_ts, DLState, Ts};
use crate::event_bus::Event;

// ── commands ─────────────────────────────────────────────────────────────

/// Request one explicit symbol/interval/date-range download.
#[derive(Debug, Clone, PartialEq)]
pub struct DownloadRequest {
    pub symbol: String,
    pub interval: String,
    pub from_date: String,
    pub to_date: String,
}
impl Event for DownloadRequest {}

/// Request a coverage scan for one symbol/interval.
#[derive(Debug, Clone, PartialEq)]
pub struct CoverageRequest {
    pub symbol: String,
    pub interval: String,
}
impl Event for CoverageRequest {}

/// Request cancellation of the running download.
#[derive(Debug, Clone, PartialEq)]
pub struct CancelDownload;
impl Event for CancelDownload {}

// ── facts ────────────────────────────────────────────────────────────────

/// A symbol download started (`total_chunks` from `count_chunks`).
#[derive(Debug, Clone, PartialEq)]
pub struct DownloadStarted {
    pub symbol: String,
    pub interval: String,
    pub from_date: String,
    pub to_date: String,
    pub total_chunks: u64,
}
impl Event for DownloadStarted {}

/// One chunk landed (cumulative `db_total`).
#[derive(Debug, Clone, PartialEq)]
pub struct DownloadProgress {
    pub symbol: String,
    pub interval: String,
    pub chunk: usize,
    pub total_chunks: usize,
    pub chunk_start: String,
    pub chunk_end: String,
    pub new_rows: usize,
    pub db_total: usize,
}
impl Event for DownloadProgress {}

/// A symbol download finished.
#[derive(Debug, Clone, PartialEq)]
pub struct DownloadCompleted {
    pub symbol: String,
    pub interval: String,
    pub new_rows: usize,
    pub db_total: usize,
    pub trading_days: u64,
}
impl Event for DownloadCompleted {}

/// A download or coverage scan failed (human-readable, secret-free reason).
#[derive(Debug, Clone, PartialEq)]
pub struct DownloadFailed {
    pub symbol: String,
    pub interval: String,
    pub reason: String,
}
impl Event for DownloadFailed {}

/// Coverage snapshot for one symbol.
#[derive(Debug, Clone, PartialEq)]
pub struct DownloadCoverage {
    pub symbol: String,
    pub interval: String,
    pub state: String,
    pub earliest: Option<String>,
    pub latest: Option<String>,
    pub row_count: u64,
    pub trading_days: u64,
    pub coverage_pct: f64,
    pub missing_head: bool,
    pub missing_tail: bool,
    pub listing_start_verified: bool,
}
impl Event for DownloadCoverage {}

// ── worker mapping (mirror of DownloadWorker reporter methods) ───────────

/// Mirror of `on_symbol_started`: range datetimes format as `%Y-%m-%d`.
pub fn started_event(
    symbol: &str,
    interval: &str,
    from: Ts,
    to: Ts,
    total_chunks: u64,
) -> DownloadStarted {
    DownloadStarted {
        symbol: symbol.to_string(),
        interval: interval.to_string(),
        from_date: format_date(from.div_euclid(86_400)),
        to_date: format_date(to.div_euclid(86_400)),
        total_chunks,
    }
}

/// Mirror of `on_chunk`.
#[allow(clippy::too_many_arguments)]
pub fn progress_event(
    symbol: &str,
    interval: &str,
    chunk: usize,
    total_chunks: usize,
    chunk_start: Ts,
    chunk_end: Ts,
    new_rows: usize,
    db_total: usize,
) -> DownloadProgress {
    DownloadProgress {
        symbol: symbol.to_string(),
        interval: interval.to_string(),
        chunk,
        total_chunks,
        chunk_start: format_date(chunk_start.div_euclid(86_400)),
        chunk_end: format_date(chunk_end.div_euclid(86_400)),
        new_rows,
        db_total,
    }
}

/// Mirror of `on_symbol_finished`.
pub fn completed_event(
    symbol: &str,
    interval: &str,
    new_rows: usize,
    db_total: usize,
    trading_days: u64,
) -> DownloadCompleted {
    DownloadCompleted {
        symbol: symbol.to_string(),
        interval: interval.to_string(),
        new_rows,
        db_total,
        trading_days,
    }
}

/// Mirror of `on_error`.
pub fn failed_event(symbol: &str, interval: &str, reason: &str) -> DownloadFailed {
    DownloadFailed {
        symbol: symbol.to_string(),
        interval: interval.to_string(),
        reason: reason.to_string(),
    }
}

/// Coverage input for the event constructor (mirrors `SymbolInfo` fields read
/// by `DownloadWorker.on_coverage`, with datetimes as full-format strings).
pub struct CoverageSummary {
    pub symbol: String,
    pub interval: String,
    pub state: DLState,
    pub earliest: Option<Ts>,
    pub latest: Option<Ts>,
    pub row_count: u64,
    pub trading_days: u64,
    pub coverage_pct: f64,
    pub missing_head: bool,
    pub missing_tail: bool,
    pub listing_start_verified: bool,
}

/// Mirror of `on_coverage`: datetimes format as `%Y-%m-%d %H:%M:%S`.
pub fn coverage_event(summary: &CoverageSummary) -> DownloadCoverage {
    DownloadCoverage {
        symbol: summary.symbol.clone(),
        interval: summary.interval.clone(),
        state: summary.state.name().to_string(),
        earliest: summary.earliest.map(format_ts),
        latest: summary.latest.map(format_ts),
        row_count: summary.row_count,
        trading_days: summary.trading_days,
        coverage_pct: summary.coverage_pct,
        missing_head: summary.missing_head,
        missing_tail: summary.missing_tail,
        listing_start_verified: summary.listing_start_verified,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::download::{
        forward_sweep, parse_ts, validate_range, CandleSink, ChunkProgress, FetchResult, MemSink,
    };
    use crate::event_bus::EventBus;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;

    fn ts(s: &str) -> Ts {
        parse_ts(s).unwrap()
    }

    #[test]
    fn worker_mapping_shapes_match_python() {
        let started = started_event(
            "TEST",
            "15m",
            ts("2026-01-01 00:00:00"),
            ts("2026-01-02 23:59:59"),
            4,
        );
        assert_eq!(started.from_date, "2026-01-01");
        assert_eq!(started.to_date, "2026-01-02");
        assert_eq!(started.total_chunks, 4);

        let progress = progress_event(
            "TEST",
            "15m",
            2,
            4,
            ts("2026-01-01 00:00:00"),
            ts("2026-01-02 00:00:00"),
            7,
            42,
        );
        assert_eq!(
            (progress.chunk_start.as_str(), progress.chunk_end.as_str()),
            ("2026-01-01", "2026-01-02")
        );
        assert_eq!((progress.new_rows, progress.db_total), (7, 42));

        let failed = failed_event("TEST", "15m", "from date after to date");
        assert_eq!(failed.reason, "from date after to date");

        let coverage = coverage_event(&CoverageSummary {
            symbol: "TEST".to_string(),
            interval: "15m".to_string(),
            state: DLState::PartialDownload,
            earliest: Some(ts("2020-06-01 09:15:00")),
            latest: None,
            row_count: 43,
            trading_days: 43,
            coverage_pct: 1.71,
            missing_head: true,
            missing_tail: false,
            listing_start_verified: false,
        });
        assert_eq!(coverage.state, "PARTIAL_DOWNLOAD");
        assert_eq!(coverage.earliest.as_deref(), Some("2020-06-01 09:15:00"));
        assert_eq!(coverage.latest, None);
    }

    #[test]
    fn all_event_types_ride_the_bus() {
        let bus = EventBus::new();
        let hits = Arc::new(AtomicUsize::new(0));
        macro_rules! count {
            ($t:ty) => {{
                let h = Arc::clone(&hits);
                bus.subscribe(move |_: &$t| {
                    h.fetch_add(1, Ordering::SeqCst);
                });
            }};
        }
        count!(DownloadRequest);
        count!(CoverageRequest);
        count!(CancelDownload);
        count!(DownloadStarted);
        count!(DownloadProgress);
        count!(DownloadCompleted);
        count!(DownloadFailed);
        count!(DownloadCoverage);

        bus.publish(DownloadRequest {
            symbol: "A".to_string(),
            interval: "15m".to_string(),
            from_date: "2026-01-01".to_string(),
            to_date: "2026-01-05".to_string(),
        });
        bus.publish(CoverageRequest {
            symbol: "A".to_string(),
            interval: "15m".to_string(),
        });
        bus.publish(CancelDownload);
        bus.publish(started_event("A", "15m", 0, 0, 1));
        bus.publish(progress_event("A", "15m", 1, 1, 0, 0, 0, 0));
        bus.publish(completed_event("A", "15m", 0, 0, 0));
        bus.publish(failed_event("A", "15m", "x"));
        bus.publish(coverage_event(&CoverageSummary {
            symbol: "A".to_string(),
            interval: "15m".to_string(),
            state: DLState::NotStarted,
            earliest: None,
            latest: None,
            row_count: 0,
            trading_days: 0,
            coverage_pct: 0.0,
            missing_head: false,
            missing_tail: false,
            listing_start_verified: false,
        }));
        assert_eq!(hits.load(Ordering::SeqCst), 8);
    }

    #[test]
    fn sweep_drives_progress_events_on_the_bus() {
        // End-to-end slice: validate range → sweep → per-chunk facts on the
        // Rust EventBus, mirroring worker → engine → legacy-signal → bus.publish.
        let bus = EventBus::new();
        let chunks = Arc::new(AtomicUsize::new(0));
        let counter = Arc::clone(&chunks);
        bus.subscribe(move |event: &DownloadProgress| {
            assert_eq!(event.symbol, "TEST");
            assert_eq!(event.total_chunks, 1);
            counter.fetch_add(1, Ordering::SeqCst);
        });

        let (from, to) = validate_range("2026-01-01", "2026-01-05").unwrap();
        let mut sink = MemSink::new();
        let mut fetch = |_s: Ts, _e: Ts| {
            FetchResult::Rows(vec![(
                "2026-01-02 09:15:00".to_string(),
                1.0,
                2.0,
                0.5,
                1.5,
                10.0,
            )])
        };
        let abort = || false;
        let bus_ref = &bus;
        let mut progress = |p: ChunkProgress| {
            bus_ref.publish(progress_event(
                "TEST", "15m", p.index, p.total, p.start, p.end, p.new_rows, p.db_total,
            ));
        };
        let (ok, new, _) = forward_sweep(
            &mut fetch,
            &mut sink,
            from,
            to,
            200,
            false,
            &abort,
            &mut progress,
        );
        assert!(ok && new == 1);
        assert_eq!(chunks.load(Ordering::SeqCst), 1);
        assert_eq!(sink.count(), 1);
    }
}
