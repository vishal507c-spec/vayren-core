//! DATA_PROCESSING core — download planning, sweep control, coverage decisions.
//!
//! Rust port of the pure-computation slices of `02_data`:
//!
//! | Python (`02_data/data/...`)            | Rust (this module)                          |
//! |----------------------------------------|---------------------------------------------|
//! | `downloader/queue.py` (jobs, priority) | `build_jobs`, `job_order`, `jobs_for`       |
//! | `downloader/sweep.py` (chunk windows)  | `chunk_windows`, `count_chunks`             |
//! | `downloader/sweep.py` (fetch/upsert)   | `forward_sweep` + `CandleSink`              |
//! | `storage/scanner.py` (state decision)  | `decide_coverage`                           |
//! | `calendar.py` (trading-day math)       | `is_trading_day`, `count_trading_days`, ... |
//! | `storage/candle_db.py` (normalise)     | `normalise_ts_str`, `normalise_candle`, ... |
//! | `worker.py` (date validation)          | `validate_range`                            |
//! | `provider/contract.py` (vocabulary)    | `ERR_*`, `CANONICAL_INTERVALS`              |
//! | `models.py` (`DLState`)                | `DLState::name`                             |
//!
//! Stays Python: SQLite IO, file locking (`lock.py`), provider SDKs/auth,
//! the Qt worker thread, settings/credentials, UI, jitter sleeps (timing,
//! not observable behavior). Timestamps cross this boundary as normalized
//! `"YYYY-MM-DD HH:MM:SS"` strings (lexicographic order == chronological).
//! `Ts` is seconds since the Unix epoch; civil-date math uses integer
//! algorithms (no date library), so behavior never depends on locale.

use std::collections::{BTreeMap, HashSet};

// ── timestamps ────────────────────────────────────────────────────────────

/// Seconds since the Unix epoch (UTC-naive wall clock, as in Python).
pub type Ts = i64;

const SECONDS_PER_DAY: i64 = 86_400;

fn days_from_civil(year: i64, month: i64, day: i64) -> i64 {
    let y = if month <= 2 { year - 1 } else { year };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = if month > 2 { month - 3 } else { month + 9 };
    let doy = (153 * mp + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146097 + doe - 719468
}

fn civil_from_days(days: i64) -> (i64, i64, i64) {
    let z = days + 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (if m <= 2 { y + 1 } else { y }, m, d)
}

/// Monday = 0 .. Sunday = 6 (1970-01-01 was a Thursday).
pub fn weekday(days: i64) -> u32 {
    (days + 3).rem_euclid(7) as u32
}

/// Format seconds as `"YYYY-MM-DD HH:MM:SS"`.
pub fn format_ts(ts: Ts) -> String {
    let days = ts.div_euclid(SECONDS_PER_DAY);
    let secs = ts.rem_euclid(SECONDS_PER_DAY);
    let (y, m, d) = civil_from_days(days);
    format!(
        "{:04}-{:02}-{:02} {:02}:{:02}:{:02}",
        y,
        m,
        d,
        secs / 3600,
        (secs % 3600) / 60,
        secs % 60
    )
}

/// Format day number as `"YYYY-MM-DD"`.
pub fn format_date(days: i64) -> String {
    let (y, m, d) = civil_from_days(days);
    format!("{y:04}-{m:02}-{d:02}")
}

fn all_digits(s: &str) -> bool {
    !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit())
}

/// Strict `"YYYY-MM-DD"` → day number, or `None` (mirrors `strptime` failure).
pub fn parse_date(s: &str) -> Option<i64> {
    let b = s.as_bytes();
    if b.len() != 10 || b[4] != b'-' || b[7] != b'-' {
        return None;
    }
    let (ys, ms, ds) = (&s[0..4], &s[5..7], &s[8..10]);
    if !all_digits(ys) || !all_digits(ms) || !all_digits(ds) {
        return None;
    }
    let (y, m, d): (i64, i64, i64) = (ys.parse().ok()?, ms.parse().ok()?, ds.parse().ok()?);
    if !(1..=12).contains(&m) || d < 1 {
        return None;
    }
    let days = days_from_civil(y, m, d);
    if civil_from_days(days) != (y, m, d) {
        return None;
    }
    Some(days)
}

/// Strict `"YYYY-MM-DD HH:MM:SS"` → seconds, or `None`.
pub fn parse_ts(s: &str) -> Option<Ts> {
    let b = s.as_bytes();
    if b.len() != 19
        || b[4] != b'-'
        || b[7] != b'-'
        || b[10] != b' '
        || b[13] != b':'
        || b[16] != b':'
    {
        return None;
    }
    let days = parse_date(&s[0..10])?;
    let (hs, ns, ss) = (&s[11..13], &s[14..16], &s[17..19]);
    if !all_digits(hs) || !all_digits(ns) || !all_digits(ss) {
        return None;
    }
    let (h, n, sec): (i64, i64, i64) = (hs.parse().ok()?, ns.parse().ok()?, ss.parse().ok()?);
    if h > 23 || n > 59 || sec > 61 {
        return None;
    }
    Some(days * SECONDS_PER_DAY + h * 3600 + n * 60 + sec)
}

// ── candle_db.py: normalisation ────────────────────────────────────────────

/// Force `"YYYY-MM-DD HH:MM:00"`; strip seconds and ISO separators.
///
/// Exact mirror of `normalise_ts_str` (first 19 chars, `T` → space, seconds
/// zeroed only when the result is a full 19-char timestamp).
pub fn normalise_ts_str(raw: &str) -> String {
    let mut s: String = raw.chars().take(19).collect();
    s = s.replace('T', " ");
    if s.chars().count() == 19 {
        s = s.chars().take(17).collect::<String>() + "00";
    }
    s
}

/// Mirror of `parse_dt`: empty/unparseable input → `None` (never panics).
pub fn parse_dt_opt(s: &str) -> Option<Ts> {
    if s.is_empty() {
        return None;
    }
    parse_ts(&normalise_ts_str(s))
}

/// Mirror of `db_path` sanitising: keep `[A-Za-z0-9_]` (the interval label is
/// deliberately not part of the filename; directory join stays Python).
pub fn sanitize_symbol(symbol: &str) -> String {
    symbol
        .bytes()
        .filter(|b| b.is_ascii_alphanumeric() || *b == b'_')
        .map(|b| b as char)
        .collect()
}

/// One normalized candle (storage shape: `candle_time` + OHLCV).
#[derive(Debug, Clone, PartialEq)]
pub struct Candle {
    pub ts: String,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: i64,
}

/// SQLite integer ceiling used by the upsert clamp.
pub const SQLITE_INT_MAX: i64 = i64::MAX;

/// Mirror of `CandleDB.upsert` row mapping: normalize the timestamp, clamp
/// volume to `[0, SQLITE_INT_MAX]`, skip the row (`None`) when the volume is
/// not finite (Python's `int()` raises there).
///
/// OHLC values pass through unvalidated — Python stores even `NaN` there
/// (SQLite keeps it `NULL`, later counted as corruption), so validating here
/// would change observable behavior.
pub fn normalise_candle(
    date: &str,
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    volume: f64,
) -> Option<Candle> {
    if !volume.is_finite() {
        return None;
    }
    let vol = if volume > SQLITE_INT_MAX as f64 {
        SQLITE_INT_MAX
    } else if volume < 0.0 {
        0
    } else {
        volume as i64
    };
    Some(Candle {
        ts: normalise_ts_str(date),
        open,
        high,
        low,
        close,
        volume: vol,
    })
}

// ── calendar.py ───────────────────────────────────────────────────────────

/// True on Mon–Fri and not a holiday (`"YYYY-MM-DD"` set).
pub fn is_trading_day(days: i64, holidays: &HashSet<String>) -> bool {
    weekday(days) < 5 && !holidays.contains(&format_date(days))
}

/// Count trading days in `[d1, d2)` stepping whole days (mirrors the loop,
/// including its half-open bound on datetimes).
pub fn count_trading_days(d1: Ts, d2: Ts, holidays: &HashSet<String>) -> u64 {
    let mut count = 0u64;
    let mut cur = d1;
    while cur < d2 {
        if is_trading_day(cur.div_euclid(SECONDS_PER_DAY), holidays) {
            count += 1;
        }
        cur += SECONDS_PER_DAY;
    }
    count
}

/// Midnight `max_history_years * 365` days back (mirrors `target_start_dt`).
pub fn target_start(today_days: i64, max_history_years: i64) -> Ts {
    (today_days - max_history_years * 365) * SECONDS_PER_DAY
}

/// End of today, 23:59:59 (mirrors `today_end_dt`).
pub fn today_end(today_days: i64) -> Ts {
    today_days * SECONDS_PER_DAY + 86399
}

/// Coverage percent: `min(100, trading_days / (years * 252) * 100)`.
pub fn coverage_pct(trading_days: u64, max_history_years: u64) -> f64 {
    let target = max_history_years * 252;
    if target == 0 {
        0.0
    } else {
        (trading_days as f64 / target as f64 * 100.0).min(100.0)
    }
}

// ── models.py ─────────────────────────────────────────────────────────────

/// Download-only states, derived from the candle DB scan.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DLState {
    NotStarted,
    PartialDownload,
    DownloadComplete,
}

impl DLState {
    /// Python `Enum.name` spelling (used in `DownloadCoverage.state`).
    pub fn name(&self) -> &'static str {
        match self {
            DLState::NotStarted => "NOT_STARTED",
            DLState::PartialDownload => "PARTIAL_DOWNLOAD",
            DLState::DownloadComplete => "DOWNLOAD_COMPLETE",
        }
    }
}

// ── scanner.py: coverage decision (pure part) ─────────────────────────────

/// What the scanner observed in one symbol's database.
pub struct ScanObserved {
    pub row_count: u64,
    pub trading_days: u64,
    pub earliest: Option<Ts>,
    pub latest: Option<Ts>,
    pub corrupt: u64,
    /// `(boundary_date "YYYY-MM-DD", verified == 1)`.
    pub boundary: Option<(String, bool)>,
}

/// Tunables the decision reads (subset of `DownloadSettings`).
pub struct CoverageParams<'a> {
    pub target_start: Ts,
    pub today_end: Ts,
    pub max_history_years: u64,
    pub head_tolerance_trading_days: u64,
    pub tail_lag_tolerance_days: i64,
    pub holidays: &'a HashSet<String>,
}

/// Pure mirror of `DatabaseScanner._scan_one` after the DB reads.
pub struct CoverageDecision {
    pub state: DLState,
    pub missing_head: bool,
    pub missing_tail: bool,
    pub coverage_pct: f64,
    pub listing_start_verified: bool,
}

pub fn decide_coverage(obs: &ScanObserved, params: &CoverageParams) -> CoverageDecision {
    let (Some(earliest), Some(latest)) = (obs.earliest, obs.latest) else {
        return CoverageDecision {
            state: DLState::NotStarted,
            missing_head: false,
            missing_tail: false,
            coverage_pct: 0.0,
            listing_start_verified: false,
        };
    };
    if obs.row_count == 0 {
        return CoverageDecision {
            state: DLState::NotStarted,
            missing_head: false,
            missing_tail: false,
            coverage_pct: 0.0,
            listing_start_verified: false,
        };
    }

    let pct = coverage_pct(obs.trading_days, params.max_history_years);
    let head_gap = count_trading_days(params.target_start, earliest, params.holidays);
    let mut missing_head = head_gap > params.head_tolerance_trading_days;

    // LISTING_START check: verified boundary dated <= earliest candle date
    // suppresses the head gap. `<=` (not `==`) lets the boundary survive
    // cleanup operations that shift the earliest candle forward.
    let mut verified = false;
    if missing_head {
        if let Some((boundary_date, is_verified)) = &obs.boundary {
            if *is_verified
                && parse_date(boundary_date).is_some()
                && boundary_date <= &format_date(earliest.div_euclid(SECONDS_PER_DAY))
            {
                missing_head = false;
                verified = true;
            }
        }
    }

    let tail_days =
        params.today_end.div_euclid(SECONDS_PER_DAY) - latest.div_euclid(SECONDS_PER_DAY);
    let missing_tail = tail_days > params.tail_lag_tolerance_days;

    let state = if missing_head || missing_tail || obs.corrupt > 0 {
        DLState::PartialDownload
    } else {
        DLState::DownloadComplete
    };
    CoverageDecision {
        state,
        missing_head,
        missing_tail,
        coverage_pct: pct,
        listing_start_verified: verified,
    }
}

// ── queue.py ──────────────────────────────────────────────────────────────

/// One missing range to download (mirrors `DownloadJob` + reason strings).
#[derive(Debug, Clone, PartialEq)]
pub struct Job {
    pub symbol: String,
    pub interval: String,
    pub from: Ts,
    pub to: Ts,
    pub reason: &'static str,
}

/// Coverage input for queue building (mirrors the `SymbolInfo` fields used).
pub struct JobInfo {
    pub symbol: String,
    pub interval: String,
    pub state: DLState,
    pub earliest: Option<Ts>,
    pub latest: Option<Ts>,
    pub missing_head: bool,
    pub missing_tail: bool,
    pub listing_start_verified: bool,
}

/// Mirror of `DownloadQueue.build_from_scan`: PARTIAL first, then NOT_STARTED
/// (stable within a state), COMPLETE skipped, unknown symbols skipped when a
/// universe filter is given (`None` = no filter).
pub fn build_jobs(
    infos: &[JobInfo],
    known: Option<&HashSet<String>>,
    target_start: Ts,
    today_end: Ts,
) -> Vec<Job> {
    let mut ordered: Vec<&JobInfo> = infos.iter().collect();
    ordered.sort_by_key(|si| match si.state {
        DLState::PartialDownload => 1,
        DLState::NotStarted => 2,
        DLState::DownloadComplete => 3,
    });

    let mut jobs = Vec::new();
    for si in ordered {
        if si.state == DLState::DownloadComplete {
            continue;
        }
        if let Some(universe) = known {
            if !universe.contains(&si.symbol) {
                continue;
            }
        }
        if si.state == DLState::NotStarted {
            jobs.push(Job {
                symbol: si.symbol.clone(),
                interval: si.interval.clone(),
                from: target_start,
                to: today_end,
                reason: "NOT_STARTED: full history",
            });
        } else if si.state == DLState::PartialDownload {
            if si.missing_head && !si.listing_start_verified {
                match si.earliest {
                    Some(earliest) => jobs.push(Job {
                        symbol: si.symbol.clone(),
                        interval: si.interval.clone(),
                        from: target_start,
                        to: earliest - 60,
                        reason: "PARTIAL: missing head coverage",
                    }),
                    None => jobs.push(Job {
                        symbol: si.symbol.clone(),
                        interval: si.interval.clone(),
                        from: target_start,
                        to: today_end,
                        reason: "PARTIAL: missing head (no earliest)",
                    }),
                }
            }
            if si.missing_tail {
                match si.latest {
                    Some(latest) => jobs.push(Job {
                        symbol: si.symbol.clone(),
                        interval: si.interval.clone(),
                        from: latest + 60,
                        to: today_end,
                        reason: "PARTIAL: missing tail coverage",
                    }),
                    None => jobs.push(Job {
                        symbol: si.symbol.clone(),
                        interval: si.interval.clone(),
                        from: target_start,
                        to: today_end,
                        reason: "PARTIAL: missing tail (no latest)",
                    }),
                }
            }
        }
    }
    jobs
}

/// `(symbol, interval)` pairs in first-seen order (mirrors `symbols_in_order`).
pub fn job_order(jobs: &[Job]) -> Vec<(String, String)> {
    let mut seen = Vec::new();
    for job in jobs {
        let pair = (job.symbol.clone(), job.interval.clone());
        if !seen.contains(&pair) {
            seen.push(pair);
        }
    }
    seen
}

/// Jobs for one pair (mirrors `jobs_for`).
pub fn jobs_for<'a>(jobs: &'a [Job], symbol: &str, interval: &str) -> Vec<&'a Job> {
    jobs.iter()
        .filter(|j| j.symbol == symbol && j.interval == interval)
        .collect()
}

// ── sweep.py ──────────────────────────────────────────────────────────────

/// Mirror of `HistoricalDownloadEngine._count_chunks` (and the identical
/// pre-count loop in `forward_sweep`): chunk `+chunk_days`, step `+1 minute`.
pub fn count_chunks(from: Ts, to: Ts, chunk_days: i64) -> usize {
    let mut count = 0usize;
    let mut chunk = from;
    while chunk <= to {
        count += 1;
        chunk = (chunk + chunk_days * SECONDS_PER_DAY).min(to) + 60;
    }
    count.max(1)
}

/// Chunk `(start, end)` windows for a sweep (mirrors the sweep loop).
pub fn chunk_windows(from: Ts, to: Ts, chunk_days: i64) -> Vec<(Ts, Ts)> {
    let mut windows = Vec::new();
    let mut chunk = from;
    while chunk <= to {
        let end = (chunk + chunk_days * SECONDS_PER_DAY).min(to);
        windows.push((chunk, end));
        chunk = end + 60;
    }
    windows
}

/// Provider fetch outcome: normalized rows or a control sentinel (mirrors
/// `TOKEN_EXPIRED` / `RATE_LIMITED` identity checks).
pub enum FetchResult {
    Rows(Vec<(String, f64, f64, f64, f64, f64)>),
    TokenExpired,
    RateLimited,
}

/// Storage surface the sweep needs (mirrors the `CandleDB` methods used).
pub trait CandleSink {
    /// `INSERT OR IGNORE` semantics; returns newly inserted row count.
    fn upsert(&mut self, rows: &[Candle]) -> usize;
    fn earliest(&self) -> Option<Ts>;
    fn count(&self) -> usize;
}

/// In-memory sink for tests (lexicographic key order == chronological for
/// normalized timestamps).
#[derive(Default)]
pub struct MemSink {
    rows: BTreeMap<String, Candle>,
}

impl MemSink {
    pub fn new() -> Self {
        Self::default()
    }
}

impl CandleSink for MemSink {
    fn upsert(&mut self, rows: &[Candle]) -> usize {
        let mut new = 0;
        for row in rows {
            if !self.rows.contains_key(&row.ts) {
                self.rows.insert(row.ts.clone(), row.clone());
                new += 1;
            }
        }
        new
    }

    fn earliest(&self) -> Option<Ts> {
        self.rows.keys().next().and_then(|k| parse_ts(k))
    }

    fn count(&self) -> usize {
        self.rows.len()
    }
}

/// Progress report for one chunk (caller adds symbol/interval context).
pub struct ChunkProgress {
    pub index: usize,
    pub total: usize,
    pub start: Ts,
    pub end: Ts,
    pub new_rows: usize,
    pub db_total: usize,
}

/// Mirror of `forward_sweep`: returns `(ok, total_new, all_chunks_zero)`.
///
/// Jitter sleeps are caller-side (timing, not observable behavior).
/// `all_chunks_zero` follows the head-sweep rule exactly: every chunk yielded
/// nothing new AND the earliest candle date did not move.
#[allow(clippy::too_many_arguments)]
pub fn forward_sweep(
    fetch: &mut dyn FnMut(Ts, Ts) -> FetchResult,
    sink: &mut dyn CandleSink,
    sweep_start: Ts,
    sweep_end: Ts,
    chunk_days: i64,
    is_head_sweep: bool,
    should_abort: &dyn Fn() -> bool,
    progress: &mut dyn FnMut(ChunkProgress),
) -> (bool, usize, bool) {
    let total = count_chunks(sweep_start, sweep_end, chunk_days);
    let earliest_before = if is_head_sweep { sink.earliest() } else { None };

    let mut total_new = 0usize;
    for (n, (start, end)) in chunk_windows(sweep_start, sweep_end, chunk_days)
        .into_iter()
        .enumerate()
    {
        if should_abort() {
            return (false, total_new, false);
        }
        let index = n + 1;
        match fetch(start, end) {
            FetchResult::TokenExpired | FetchResult::RateLimited => {
                return (false, total_new, false);
            }
            FetchResult::Rows(raw) => {
                let candles: Vec<Candle> = raw
                    .iter()
                    .filter_map(|(date, o, h, l, c, v)| normalise_candle(date, *o, *h, *l, *c, *v))
                    .collect();
                let mut new = 0;
                if !candles.is_empty() {
                    new = sink.upsert(&candles);
                    total_new += new;
                }
                progress(ChunkProgress {
                    index,
                    total,
                    start,
                    end,
                    new_rows: new,
                    db_total: sink.count(),
                });
            }
        }
    }

    let mut all_zero = false;
    if is_head_sweep && total_new == 0 {
        let after = sink.earliest();
        match (earliest_before, after) {
            (None, None) => all_zero = true,
            (Some(before), Some(after)) => {
                if after.div_euclid(SECONDS_PER_DAY) == before.div_euclid(SECONDS_PER_DAY) {
                    all_zero = true;
                }
            }
            _ => {}
        }
    }
    (true, total_new, all_zero)
}

// ── worker.py: date validation ────────────────────────────────────────────

/// Mirror of `DownloadWorker._run_download` date handling: `"YYYY-MM-DD"`
/// bounds, end-of-day `to`, exact failure strings.
pub fn validate_range(from_date: &str, to_date: &str) -> Result<(Ts, Ts), &'static str> {
    let from_day = parse_date(from_date).ok_or("invalid date range")?;
    let to_day = parse_date(to_date).ok_or("invalid date range")?;
    let from = from_day * SECONDS_PER_DAY;
    let to = to_day * SECONDS_PER_DAY + 86399;
    if from > to {
        return Err("from date after to date");
    }
    Ok((from, to))
}

// ── provider/contract.py: vocabulary ──────────────────────────────────────

pub const ERR_AUTHENTICATION_FAILED: &str = "AUTHENTICATION_FAILED";
pub const ERR_RATE_LIMITED: &str = "RATE_LIMITED";
pub const ERR_INVALID_SYMBOL: &str = "INVALID_SYMBOL";
pub const ERR_NETWORK_ERROR: &str = "NETWORK_ERROR";
pub const ERR_PROVIDER_UNAVAILABLE: &str = "PROVIDER_UNAVAILABLE";
pub const ERR_INVALID_REQUEST: &str = "INVALID_REQUEST";
pub const ERR_UNKNOWN_PROVIDER_ERROR: &str = "UNKNOWN_PROVIDER_ERROR";

/// Canonical broker-agnostic interval ids.
pub const CANONICAL_INTERVALS: &[&str] = &["1m", "5m", "15m", "30m", "1h"];

/// Canonical interval → minutes (mirrors `INTERVAL_MINUTES`).
pub const INTERVAL_MINUTES: &[(&str, i64)] =
    &[("1m", 1), ("5m", 5), ("15m", 15), ("30m", 30), ("1h", 60)];

#[cfg(test)]
mod tests {
    use super::*;

    fn ts(s: &str) -> Ts {
        parse_ts(s).unwrap()
    }

    fn holidays() -> HashSet<String> {
        HashSet::from(["2026-01-26".to_string()])
    }

    // ── ground truth: live Python probe, 2026-09-17 ──────────────────────

    #[test]
    fn chunk_counts_match_engine() {
        assert_eq!(
            count_chunks(ts("2026-01-01 00:00:00"), ts("2026-01-05 00:00:00"), 200),
            1
        );
        assert_eq!(
            count_chunks(ts("2026-01-01 00:00:00"), ts("2026-12-31 00:00:00"), 200),
            2
        );
        assert_eq!(
            count_chunks(ts("2026-01-01 00:00:00"), ts("2026-01-01 00:00:00"), 200),
            1
        );
        assert_eq!(
            count_chunks(ts("2026-01-01 00:00:00"), ts("2026-01-03 00:00:00"), 1),
            2
        );
        assert_eq!(
            count_chunks(ts("2026-01-01 00:00:00"), ts("2026-01-01 00:01:00"), 1),
            1
        );
    }

    #[test]
    fn sweep_windows_match_python() {
        assert_eq!(
            chunk_windows(ts("2026-01-01 00:00:00"), ts("2026-06-10 00:00:00"), 200),
            vec![(ts("2026-01-01 00:00:00"), ts("2026-06-10 00:00:00"))]
        );
        let windows = chunk_windows(ts("2026-01-01 00:00:00"), ts("2026-01-03 00:00:00"), 1);
        assert_eq!(windows.len(), 2);
        assert_eq!(windows[0].1 + 60, windows[1].0);
    }

    #[test]
    fn queue_jobs_match_python() {
        let target = ts("2016-09-19 00:00:00");
        let today_end = ts("2026-09-17 23:59:00") + 59;
        let infos = vec![
            JobInfo {
                symbol: "FRESH".to_string(),
                interval: "15m".to_string(),
                state: DLState::NotStarted,
                earliest: None,
                latest: None,
                missing_head: false,
                missing_tail: false,
                listing_start_verified: false,
            },
            JobInfo {
                symbol: "PART".to_string(),
                interval: "15m".to_string(),
                state: DLState::PartialDownload,
                earliest: Some(ts("2020-06-01 09:15:00")),
                latest: Some(ts("2026-09-10 15:30:00")),
                missing_head: true,
                missing_tail: true,
                listing_start_verified: false,
            },
            JobInfo {
                symbol: "DONE".to_string(),
                interval: "15m".to_string(),
                state: DLState::DownloadComplete,
                earliest: Some(ts("2016-01-04 09:15:00")),
                latest: Some(ts("2026-09-16 15:30:00")),
                missing_head: false,
                missing_tail: false,
                listing_start_verified: false,
            },
        ];
        let known: HashSet<String> = ["FRESH", "PART", "DONE"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        let jobs = build_jobs(&infos, Some(&known), target, today_end);
        assert_eq!(jobs.len(), 3);
        // PARTIAL sorts before NOT_STARTED (probe ORDER).
        assert_eq!(jobs[0].symbol, "PART");
        assert_eq!(jobs[0].reason, "PARTIAL: missing head coverage");
        assert_eq!(format_ts(jobs[0].from), "2016-09-19 00:00:00");
        assert_eq!(format_ts(jobs[0].to), "2020-06-01 09:14:00");
        assert_eq!(jobs[1].reason, "PARTIAL: missing tail coverage");
        assert_eq!(format_ts(jobs[1].from), "2026-09-10 15:31:00");
        assert_eq!(format_ts(jobs[1].to), "2026-09-17 23:59:59");
        assert_eq!(jobs[2].reason, "NOT_STARTED: full history");
        assert_eq!(
            job_order(&jobs),
            vec![
                ("PART".to_string(), "15m".to_string()),
                ("FRESH".to_string(), "15m".to_string())
            ]
        );
        assert_eq!(jobs_for(&jobs, "PART", "15m").len(), 2);

        let filtered = build_jobs(
            &infos,
            Some(&HashSet::from(["FRESH".to_string()])),
            target,
            today_end,
        );
        assert_eq!(filtered.len(), 1);
    }

    #[test]
    fn trading_day_counts_match_calendar() {
        let h = holidays();
        assert_eq!(
            count_trading_days(ts("2026-01-01 00:00:00"), ts("2026-02-01 00:00:00"), &h),
            21
        );
        assert_eq!(
            count_trading_days(ts("2026-01-26 00:00:00"), ts("2026-01-27 00:00:00"), &h),
            0
        );
        assert_eq!(
            count_trading_days(ts("2026-01-24 00:00:00"), ts("2026-01-26 00:00:00"), &h),
            0
        );
        assert!(is_trading_day(parse_date("2026-01-05").unwrap(), &h));
        assert!(!is_trading_day(parse_date("2026-01-26").unwrap(), &h));
    }

    #[test]
    fn target_and_today_end_match() {
        let today = parse_date("2026-09-17").unwrap();
        assert_eq!(format_ts(target_start(today, 10)), "2016-09-19 00:00:00");
        assert_eq!(format_ts(today_end(today)), "2026-09-17 23:59:59");
    }

    #[test]
    fn normalise_vectors_match() {
        assert_eq!(
            normalise_ts_str("2026-01-05T09:15:33"),
            "2026-01-05 09:15:00"
        );
        assert_eq!(
            normalise_ts_str("2026-01-05 09:15:33"),
            "2026-01-05 09:15:00"
        );
        assert_eq!(normalise_ts_str("2026-01-05"), "2026-01-05");
        assert_eq!(
            normalise_ts_str("2026-01-05 09:15:00"),
            "2026-01-05 09:15:00"
        );
        assert_eq!(
            parse_dt_opt("2026-01-05 09:15:00"),
            Some(ts("2026-01-05 09:15:00"))
        );
        assert_eq!(parse_dt_opt(""), None);
        assert_eq!(parse_dt_opt("junk"), None);
        assert_eq!(sanitize_symbol("RELIANCE"), "RELIANCE");
        assert_eq!(sanitize_symbol("A&B.C"), "ABC");
    }

    #[test]
    fn candle_normalise_preserves_skip_and_clamp() {
        assert!(normalise_candle("2026-01-05 09:15:33", 1.0, 2.0, 0.5, 1.5, f64::NAN).is_none());
        let neg = normalise_candle("2026-01-05 09:15:00", 1.0, 2.0, 0.5, 1.5, -3.7).unwrap();
        assert_eq!(neg.volume, 0);
        let huge = normalise_candle("2026-01-05 09:15:00", 1.0, 2.0, 0.5, 1.5, 1e19).unwrap();
        assert_eq!(huge.volume, SQLITE_INT_MAX);
        // NaN OHLC passes through (stored NULL → counted as corruption later).
        let nan = normalise_candle("2026-01-05 09:15:00", f64::NAN, 2.0, 0.5, 1.5, 10.0).unwrap();
        assert!(nan.open.is_nan());
        assert_eq!(nan.ts, "2026-01-05 09:15:00");
    }

    #[test]
    fn range_validation_matches_worker() {
        assert_eq!(
            validate_range("2026-01-01", "2026-01-02"),
            Ok((ts("2026-01-01 00:00:00"), ts("2026-01-02 23:59:59")))
        );
        assert_eq!(
            validate_range("not-a-date", "2026-01-02"),
            Err("invalid date range")
        );
        assert_eq!(
            validate_range("2026-01-03", "2026-01-02"),
            Err("from date after to date")
        );
    }

    // ── coverage decisions (logic pinned; live SCAN numbers in probe) ────

    fn params(target: Ts, today: Ts, h: &HashSet<String>) -> CoverageParams<'_> {
        CoverageParams {
            target_start: target,
            today_end: today,
            max_history_years: 10,
            head_tolerance_trading_days: 5,
            tail_lag_tolerance_days: 5,
            holidays: h,
        }
    }

    #[test]
    fn coverage_partial_and_boundary_rule() {
        let h = HashSet::new();
        let target = ts("2016-09-19 00:00:00");
        let today = ts("2026-09-17 23:59:59");
        let base = ScanObserved {
            row_count: 43,
            trading_days: 43,
            earliest: Some(ts("2020-06-01 09:15:00")),
            latest: Some(ts("2021-03-22 09:15:00")),
            corrupt: 0,
            boundary: None,
        };
        let d = decide_coverage(&base, &params(target, today, &h));
        assert_eq!(d.state, DLState::PartialDownload);
        assert!(d.missing_head && d.missing_tail && !d.listing_start_verified);
        assert!((d.coverage_pct - 1.71).abs() < 0.01);

        // Verified boundary dated <= earliest suppresses the head gap.
        let suppressed = ScanObserved {
            boundary: Some(("2020-06-01".to_string(), true)),
            ..base_like(&base)
        };
        let d2 = decide_coverage(&suppressed, &params(target, today, &h));
        assert!(!d2.missing_head && d2.listing_start_verified);
        assert_eq!(d2.state, DLState::PartialDownload); // tail still missing

        // Boundary after earliest: no suppression.
        let late = ScanObserved {
            boundary: Some(("2021-01-01".to_string(), true)),
            ..base_like(&base)
        };
        let d3 = decide_coverage(&late, &params(target, today, &h));
        assert!(d3.missing_head && !d3.listing_start_verified);

        // Unverified boundary: no suppression.
        let unverified = ScanObserved {
            boundary: Some(("2020-06-01".to_string(), false)),
            ..base_like(&base)
        };
        assert!(decide_coverage(&unverified, &params(target, today, &h)).missing_head);
    }

    #[test]
    fn coverage_complete_and_not_started() {
        let h = HashSet::new();
        let target = ts("2016-09-19 00:00:00");
        let today = ts("2026-09-15 23:59:59");
        let full = ScanObserved {
            row_count: 60000,
            trading_days: 2400,
            earliest: Some(ts("2016-09-19 09:15:00")),
            latest: Some(ts("2026-09-15 15:30:00")),
            corrupt: 0,
            boundary: None,
        };
        let d = decide_coverage(&full, &params(target, today, &h));
        assert_eq!(d.state, DLState::DownloadComplete);
        assert!(!d.missing_head && !d.missing_tail);

        // Corruption alone forces PARTIAL.
        let corrupt = ScanObserved {
            corrupt: 2,
            ..base_like(&full)
        };
        assert_eq!(
            decide_coverage(&corrupt, &params(target, today, &h)).state,
            DLState::PartialDownload
        );

        // Empty database rows → NOT_STARTED (probe SCAN3 / ghost file).
        let ghost = ScanObserved {
            row_count: 0,
            trading_days: 0,
            earliest: None,
            latest: None,
            corrupt: 0,
            boundary: None,
        };
        let g = decide_coverage(&ghost, &params(target, today, &h));
        assert_eq!(g.state, DLState::NotStarted);
        assert_eq!(g.coverage_pct, 0.0);
    }

    fn base_like(o: &ScanObserved) -> ScanObserved {
        ScanObserved {
            row_count: o.row_count,
            trading_days: o.trading_days,
            earliest: o.earliest,
            latest: o.latest,
            corrupt: o.corrupt,
            boundary: None,
        }
    }

    // ── sweep behavior ──────────────────────────────────────────────────

    fn row(day: &str, vol: f64) -> (String, f64, f64, f64, f64, f64) {
        (format!("{day} 09:15:00"), 1.0, 2.0, 0.5, 1.5, vol)
    }

    #[test]
    fn sweep_accumulates_and_reports() {
        let mut sink = MemSink::new();
        let mut seen = Vec::new();
        let mut fetch = |_s: Ts, _e: Ts| {
            FetchResult::Rows(vec![row("2026-01-01", 100.0), row("2026-01-02", 200.0)])
        };
        let abort = || false;
        let mut progress = |p: ChunkProgress| seen.push((p.index, p.total, p.new_rows, p.db_total));
        let (ok, new, zero) = forward_sweep(
            &mut fetch,
            &mut sink,
            ts("2026-01-01 00:00:00"),
            ts("2026-01-05 00:00:00"),
            200,
            false,
            &abort,
            &mut progress,
        );
        assert!(ok && !zero);
        assert_eq!((new, sink.count()), (2, 2));
        assert_eq!(seen, vec![(1, 1, 2, 2)]);
    }

    #[test]
    fn sweep_sentinels_and_abort_stop() {
        let mut sink = MemSink::new();
        let abort = || false;
        let mut nop = |_: ChunkProgress| {};
        let mut expired = |_s: Ts, _e: Ts| FetchResult::TokenExpired;
        assert_eq!(
            forward_sweep(
                &mut expired,
                &mut sink,
                ts("2026-01-01 00:00:00"),
                ts("2026-01-05 00:00:00"),
                200,
                false,
                &abort,
                &mut nop
            ),
            (false, 0, false)
        );
        let mut limited = |_s: Ts, _e: Ts| FetchResult::RateLimited;
        assert!(
            !forward_sweep(
                &mut limited,
                &mut sink,
                ts("2026-01-01 00:00:00"),
                ts("2026-01-05 00:00:00"),
                200,
                false,
                &abort,
                &mut nop
            )
            .0
        );
        // Abort between chunks: partial progress kept, ok = false.
        let mut one = |_s: Ts, _e: Ts| FetchResult::Rows(vec![row("2026-01-01", 5.0)]);
        let calls = std::cell::Cell::new(0);
        let abort_after_one = || {
            let n = calls.get() + 1;
            calls.set(n);
            n > 1
        };
        let (ok, new, _) = forward_sweep(
            &mut one,
            &mut sink,
            ts("2026-01-01 00:00:00"),
            ts("2028-01-01 00:00:00"),
            200,
            false,
            &abort_after_one,
            &mut nop,
        );
        assert!(!ok && new >= 1);
    }

    #[test]
    fn sweep_head_all_zero_rule() {
        // Empty store, empty fetch → eligible for LISTING_START.
        let mut sink = MemSink::new();
        let mut empty = |_s: Ts, _e: Ts| FetchResult::Rows(vec![]);
        let abort = || false;
        let mut nop = |_: ChunkProgress| {};
        let (_, _, zero) = forward_sweep(
            &mut empty,
            &mut sink,
            ts("2026-01-01 00:00:00"),
            ts("2026-01-05 00:00:00"),
            200,
            true,
            &abort,
            &mut nop,
        );
        assert!(zero);

        // Unchanged earliest → still eligible.
        sink.upsert(&[normalise_candle("2020-06-01 09:15:00", 1.0, 2.0, 0.5, 1.5, 9.0).unwrap()]);
        let (_, _, zero2) = forward_sweep(
            &mut empty,
            &mut sink,
            ts("2020-01-01 00:00:00"),
            ts("2020-05-01 00:00:00"),
            200,
            true,
            &abort,
            &mut nop,
        );
        assert!(zero2);

        // Moved earliest → not eligible.
        let mut newer = |_s: Ts, _e: Ts| FetchResult::Rows(vec![row("2020-04-01", 9.0)]);
        let (_, _, zero3) = forward_sweep(
            &mut newer,
            &mut sink,
            ts("2020-01-01 00:00:00"),
            ts("2020-05-01 00:00:00"),
            200,
            true,
            &abort,
            &mut nop,
        );
        assert!(!zero3);
    }

    #[test]
    fn dlstate_names_and_vocab() {
        assert_eq!(DLState::NotStarted.name(), "NOT_STARTED");
        assert_eq!(DLState::PartialDownload.name(), "PARTIAL_DOWNLOAD");
        assert_eq!(DLState::DownloadComplete.name(), "DOWNLOAD_COMPLETE");
        assert_eq!(CANONICAL_INTERVALS, ["1m", "5m", "15m", "30m", "1h"]);
        assert_eq!(ERR_AUTHENTICATION_FAILED, "AUTHENTICATION_FAILED");
        assert_eq!(INTERVAL_MINUTES[4], ("1h", 60));
    }
}
