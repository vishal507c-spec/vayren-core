//! HistoricalDownloadEngine orchestration twin — MODE 1 download flow.
//!
//! Rust port of the flow mechanics of `02_data/data/downloader/engine.py`
//! (everything except the already-twinned planning kernels in `download.rs`
//! — queue/sweep/coverage/chunks — which are reused untouched here).
//!
//! | Python (`engine.py`) | Rust (here) |
//! |---|---|
//! | abort flag (`cancel`/`reset`/`_abort_check`) | [`DownloadEngine`] flag |
//! | `run_download` gates + lock/heartbeat sequencing | [`DownloadEngine::run_download`] |
//! | `_download_one` retry + summary shapes | [`DownloadEngine::download_one`] |
//! | `run_batch` gates + discovery | [`DownloadEngine::run_batch`] |
//! | `_run_batch_inner` passes/coverage/queue flow | [`DownloadEngine::run_batch_inner`] |
//! | `_execute_queue` renew-retry + jitter placement | [`DownloadEngine::execute_queue`] |
//! | `_process_symbol` head-boundary flow | [`DownloadEngine::process_symbol`] |
//! | `status()` display map | [`engine_status`] |
//! | request validation (`validation.py`) | [`validate_form`] |
//!
//! Reused untouched: `download` (`build_jobs`, `job_order`, `jobs_for`,
//! `count_chunks`, `forward_sweep`, `DLState`, `FetchResult`, `CandleSink`,
//! `MemSink`), provider error codes live behind the [`DownloadProvider`]
//! seam.
//!
//! Seams (Python-owned, injected): provider SDK (symbols/session/fetch/
//! renew), SQLite storage + scanner (IO), lock file + heartbeat thread +
//! sleeps (threads/timing), market clock, settings object, log calls.
//! `stderr` logging (`log.error`/`log.warning`/`log.info`) stays Python —
//! the twin surfaces the same outcomes, not the log lines.
//!
//! Deliberate narrowings, all documented at the item:
//! - `raise TypeError` on a missing provider → the twin takes the provider
//!   (non-optional); the `None` check stays at the Python boundary.
//! - `ProviderError` → [`ProviderFault::Auth`] / `::Other` (code match stays
//!   seam-side; only the two behaviors cross).
//! - Result dicts → [`DownloadOutcome`] (same keys documented per variant).
//! - `datetime` objects → epoch seconds (`Ts`); the IST stamp for the
//!   market-open message arrives preformatted via [`MarketState`].
//! - `threading.Event` heartbeat → [`HeartbeatCtl`] start/stop (ordering
//!   preserved on every path, mirroring `try/finally`).

use std::collections::HashSet;

use crate::download::{
    build_jobs, count_chunks, forward_sweep, job_order, jobs_for, CandleSink, ChunkProgress,
    DLState, FetchResult, Job, JobInfo, Ts,
};

// ── seams ─────────────────────────────────────────────────────────────────

/// Provider outcome for `symbols()` (code match stays seam-side).
#[derive(Debug, Clone, PartialEq)]
pub enum ProviderFault {
    Auth(String),
    Other(String),
}

/// Broker historical face (SDK + transport stay Python).
pub trait DownloadProvider {
    fn symbols(&mut self) -> Result<HashSet<String>, ProviderFault>;
    fn new_session(&mut self);
    fn renew(&mut self) -> Result<(), String>;
    fn fetch(&mut self, symbol: &str, interval: &str, from: Ts, to: Ts) -> FetchResult;
}

/// Per-symbol storage (SQLite IO stays Python; sweep surface reused).
pub trait CandleStore: CandleSink {
    fn trading_days(&self) -> usize;
    fn set_boundary(&mut self, name: &str, date: &str, verified: bool);
}

pub trait StoreFactory {
    fn open(&mut self, symbol: &str, interval: &str) -> Box<dyn CandleStore>;
}

/// Coverage scan surface (scan SQL stays Python).
#[derive(Debug, Clone, PartialEq)]
pub struct ScanInfo {
    pub symbol: String,
    pub interval: String,
    pub state: DLState,
    pub earliest: Option<Ts>,
    pub latest: Option<Ts>,
    pub missing_head: bool,
    pub missing_tail: bool,
    pub listing_start_verified: bool,
    pub rows: usize,
    pub trading_days: usize,
}

impl ScanInfo {
    pub fn job_info(&self) -> JobInfo {
        JobInfo {
            symbol: self.symbol.clone(),
            interval: self.interval.clone(),
            state: self.state.clone(),
            earliest: self.earliest,
            latest: self.latest,
            missing_head: self.missing_head,
            missing_tail: self.missing_tail,
            listing_start_verified: self.listing_start_verified,
        }
    }
}

pub trait CoverageScan {
    fn scan_all(&mut self, symbols: &[(String, String)]) -> Vec<ScanInfo>;
    fn cleanup(&mut self, infos: &[ScanInfo]);
}

/// Lock file + heartbeat thread + sleeps (threads/timing stay Python).
pub trait RunLock {
    fn acquire(&mut self) -> bool;
    fn release(&mut self);
}

pub trait HeartbeatCtl {
    fn start(&mut self);
    fn stop(&mut self);
}

pub trait JitterSleep {
    fn sleep(&mut self, lo: f64, hi: f64);
}

/// Observation boundary (mirrors the reporter methods the engine calls).
pub trait DownloadReporter {
    fn on_status(&mut self, message: &str);
    fn on_error(&mut self, symbol: &str, interval: &str, message: &str);
    fn on_symbol_started(
        &mut self,
        symbol: &str,
        interval: &str,
        reason: &str,
        from: Ts,
        to: Ts,
        total_chunks: usize,
    );
    fn on_symbol_finished(
        &mut self,
        symbol: &str,
        interval: &str,
        new_rows: usize,
        db_total: usize,
        trading_days: usize,
    );
    fn on_coverage(&mut self, info: &ScanInfo);
    fn on_chunk(&mut self, symbol: &str, interval: &str, index: usize, total: usize);
}

/// Frozen settings values (the settings object stays Python).
#[derive(Debug, Clone, PartialEq)]
pub struct EngineSettings {
    pub chunk_days: i64,
    pub max_passes: usize,
    pub delay_min: f64,
    pub delay_max: f64,
    pub provider: String,
    pub exchange: String,
    pub data_dir: String,
    pub target_start: Ts,
    pub today_end: Ts,
}

/// Market clock reading (clock stays Python).
#[derive(Debug, Clone, PartialEq)]
pub struct MarketState {
    pub open: bool,
    pub now_label: String,
}

// ── outcomes + status ─────────────────────────────────────────────────────

/// Terminal outcome (mirrors the result-dict keys per variant).
/// Unexpected provider failures propagate as `Err` (mirrors bare `raise`;
/// never mistaken for a clean `Failed`).
#[derive(Debug, Clone, PartialEq)]
pub enum DownloadOutcome {
    Aborted,
    BlockedMarketOpen,
    Locked,
    AuthFailed {
        message: String,
    },
    UnknownSymbol,
    NoSymbols,
    OkSingle {
        symbol: String,
        interval: String,
        new_rows: usize,
        db_total: usize,
        trading_days: usize,
    },
    Failed,
    OkBatch,
}

/// Architecture-facing status (mirrors `status()`; secrets never appear).
#[derive(Debug, Clone, PartialEq)]
pub struct EngineStatus {
    pub provider: String,
    pub exchange: String,
    pub data_dir: String,
    pub provider_ready: bool,
    pub provider_status: String,
}

pub fn engine_status(
    provider: &str,
    exchange: &str,
    data_dir: &str,
    available: bool,
    reason: &str,
) -> EngineStatus {
    EngineStatus {
        provider: provider.to_string(),
        exchange: exchange.to_string(),
        data_dir: data_dir.to_string(),
        provider_ready: available,
        provider_status: if available {
            "ready".to_string()
        } else {
            reason.to_string()
        },
    }
}

// ── the engine ────────────────────────────────────────────────────────────

pub struct DownloadEngine {
    settings: EngineSettings,
    abort: bool,
}

impl DownloadEngine {
    pub fn new(settings: EngineSettings) -> Self {
        Self {
            settings,
            abort: false,
        }
    }

    pub fn cancel(&mut self) {
        self.abort = true;
    }

    pub fn reset(&mut self) {
        self.abort = false;
    }

    fn should_abort(&self) -> bool {
        self.abort
    }

    fn market_blocked(&self, reporter: &mut dyn DownloadReporter, market: &MarketState) -> bool {
        if market.open {
            reporter.on_status(&format!(
                "⚠  MARKET IS OPEN — HISTORICAL DOWNLOAD DISABLED (IST {})",
                market.now_label
            ));
            return true;
        }
        false
    }

    /// Single explicit range (mirrors `run_download` incl. lock/heartbeat
    /// sequencing on every path, including propagation).
    #[allow(clippy::too_many_arguments)]
    pub fn run_download(
        &mut self,
        provider: &mut dyn DownloadProvider,
        stores: &mut dyn StoreFactory,
        reporter: &mut dyn DownloadReporter,
        lock: &mut dyn RunLock,
        heartbeat: &mut dyn HeartbeatCtl,
        market: &MarketState,
        symbol: &str,
        interval: &str,
        from: Ts,
        to: Ts,
    ) -> Result<DownloadOutcome, String> {
        if self.abort {
            return Ok(DownloadOutcome::Aborted);
        }
        if self.market_blocked(reporter, market) {
            return Ok(DownloadOutcome::BlockedMarketOpen);
        }
        if !lock.acquire() {
            reporter.on_error(
                symbol,
                interval,
                "Engine already running (lock file held by another process).",
            );
            return Ok(DownloadOutcome::Locked);
        }
        heartbeat.start();
        let outcome = self.download_one(provider, stores, reporter, symbol, interval, from, to);
        heartbeat.stop();
        lock.release();
        outcome
    }

    /// Core single-symbol flow (mirrors `_download_one`).
    pub fn download_one(
        &mut self,
        provider: &mut dyn DownloadProvider,
        stores: &mut dyn StoreFactory,
        reporter: &mut dyn DownloadReporter,
        symbol: &str,
        interval: &str,
        from: Ts,
        to: Ts,
    ) -> Result<DownloadOutcome, String> {
        let known = match provider.symbols() {
            Ok(known) => known,
            Err(ProviderFault::Auth(message)) => {
                reporter.on_error(
                    symbol,
                    interval,
                    &format!("authentication failed: {message}"),
                );
                return Ok(DownloadOutcome::AuthFailed { message });
            }
            Err(ProviderFault::Other(reason)) => return Err(reason),
        };
        if !known.contains(symbol) {
            reporter.on_error(symbol, interval, "symbol not found in NSE instrument map");
            return Ok(DownloadOutcome::UnknownSymbol);
        }
        let mut store = stores.open(symbol, interval);
        let total_chunks = count_chunks(from, to, self.settings.chunk_days);
        reporter.on_symbol_started(symbol, interval, "explicit range", from, to, total_chunks);
        provider.new_session();
        let (mut ok, mut new_rows, _) = self.sweep(
            provider,
            &mut *store,
            reporter,
            symbol,
            interval,
            from,
            to,
            false,
        );
        if !ok && !self.abort {
            // Token expired or rate-limited → renew once, retry once.
            match provider.renew() {
                Ok(()) => {
                    let (ok2, new_rows2, _) = self.sweep(
                        provider,
                        &mut *store,
                        reporter,
                        symbol,
                        interval,
                        from,
                        to,
                        false,
                    );
                    ok = ok2;
                    new_rows += new_rows2;
                }
                Err(reason) => {
                    reporter.on_error(symbol, interval, &format!("token renewal failed: {reason}"));
                    self.abort = true;
                    return Ok(DownloadOutcome::AuthFailed {
                        message: "auth".to_string(),
                    });
                }
            }
        }
        let db_total = store.count();
        let trading_days = store.trading_days();
        if ok {
            reporter.on_symbol_finished(symbol, interval, new_rows, db_total, trading_days);
            return Ok(DownloadOutcome::OkSingle {
                symbol: symbol.to_string(),
                interval: interval.to_string(),
                new_rows,
                db_total,
                trading_days,
            });
        }
        reporter.on_error(
            symbol,
            interval,
            "download aborted (rate-limit, token or cancel)",
        );
        self.abort = true;
        Ok(DownloadOutcome::Failed)
    }

    /// One sweep with reporter/progress wiring (mirrors the `forward_sweep`
    /// call sites). The abort flag reads live (mirrors the bound
    /// `should_abort` method, so a mid-sweep cancel still stops the run).
    #[allow(clippy::too_many_arguments)]
    fn sweep(
        &self,
        provider: &mut dyn DownloadProvider,
        store: &mut dyn CandleStore,
        reporter: &mut dyn DownloadReporter,
        symbol: &str,
        interval: &str,
        from: Ts,
        to: Ts,
        is_head: bool,
    ) -> (bool, usize, bool) {
        let mut fetch =
            |fetch_from: Ts, fetch_to: Ts| provider.fetch(symbol, interval, fetch_from, fetch_to);
        let should_abort = || self.should_abort();
        let mut progress = |update: ChunkProgress| {
            reporter.on_chunk(symbol, interval, update.index, update.total);
        };
        let (ok, new_rows, all_zero) = forward_sweep(
            &mut fetch,
            store as &mut dyn CandleSink,
            from,
            to,
            self.settings.chunk_days,
            is_head,
            &should_abort,
            &mut progress,
        );
        (ok, new_rows, all_zero)
    }

    /// Multi-pass queue flow (mirrors `run_batch` incl. lock sequencing).
    #[allow(clippy::too_many_arguments)]
    pub fn run_batch(
        &mut self,
        provider: &mut dyn DownloadProvider,
        stores: &mut dyn StoreFactory,
        scanner: &mut dyn CoverageScan,
        reporter: &mut dyn DownloadReporter,
        lock: &mut dyn RunLock,
        heartbeat: &mut dyn HeartbeatCtl,
        jitter: &mut dyn JitterSleep,
        discover: &mut dyn FnMut() -> Vec<(String, String)>,
        symbols: Option<Vec<(String, String)>>,
        market: &MarketState,
    ) -> Result<DownloadOutcome, String> {
        if self.abort {
            return Ok(DownloadOutcome::Aborted);
        }
        let symbols = symbols.unwrap_or_else(|| discover());
        if symbols.is_empty() {
            return Ok(DownloadOutcome::NoSymbols);
        }
        if !lock.acquire() {
            reporter.on_status("⚠ Engine already running (lock file held by another process).");
            return Ok(DownloadOutcome::Locked);
        }
        heartbeat.start();
        let outcome = self.run_batch_inner(
            provider, stores, scanner, reporter, jitter, &symbols, market,
        );
        heartbeat.stop();
        lock.release();
        outcome
    }

    /// Batch passes (mirrors `_run_batch_inner`).
    #[allow(clippy::too_many_arguments)]
    pub fn run_batch_inner(
        &mut self,
        provider: &mut dyn DownloadProvider,
        stores: &mut dyn StoreFactory,
        scanner: &mut dyn CoverageScan,
        reporter: &mut dyn DownloadReporter,
        jitter: &mut dyn JitterSleep,
        symbols: &[(String, String)],
        market: &MarketState,
    ) -> Result<DownloadOutcome, String> {
        if self.market_blocked(reporter, market) {
            for info in scanner.scan_all(symbols) {
                reporter.on_coverage(&info);
            }
            return Ok(DownloadOutcome::BlockedMarketOpen);
        }
        reporter.on_status(
            "Historical Download Engine — MODE 1\nPurpose  : Download historical OHLCV data only\nRecovery : State derived from candle DBs — no progress files",
        );
        let known = match provider.symbols() {
            Ok(known) => known,
            Err(ProviderFault::Auth(message)) => {
                reporter.on_status(&format!("⚠ Authentication failed: {message}"));
                return Ok(DownloadOutcome::AuthFailed { message });
            }
            Err(ProviderFault::Other(reason)) => return Err(reason),
        };
        for _ in 1..=self.settings.max_passes {
            if self.abort {
                break;
            }
            let infos = scanner.scan_all(symbols);
            scanner.cleanup(&infos);
            for info in &infos {
                reporter.on_coverage(info);
            }
            let remaining: Vec<&ScanInfo> = infos
                .iter()
                .filter(|si| si.state != DLState::DownloadComplete)
                .collect();
            if remaining.is_empty() {
                reporter.on_status("✔  All symbols DOWNLOAD_COMPLETE.");
                return Ok(DownloadOutcome::OkBatch);
            }
            let job_infos: Vec<JobInfo> = infos.iter().map(|si| si.job_info()).collect();
            let jobs = build_jobs(
                &job_infos,
                Some(&known),
                self.settings.target_start,
                self.settings.today_end,
            );
            if jobs.is_empty() {
                reporter.on_status("✔  No download jobs — all ranges covered.");
                return Ok(DownloadOutcome::OkBatch);
            }
            self.execute_queue(provider, stores, reporter, jitter, &jobs, &known);
        }
        for info in scanner.scan_all(symbols) {
            reporter.on_coverage(&info);
        }
        Ok(DownloadOutcome::OkBatch)
    }

    /// Ordered queue execution with renew-once retry (mirrors
    /// `_execute_queue`, including jitter placement between symbols only).
    #[allow(clippy::too_many_arguments)]
    pub fn execute_queue(
        &mut self,
        provider: &mut dyn DownloadProvider,
        stores: &mut dyn StoreFactory,
        reporter: &mut dyn DownloadReporter,
        jitter: &mut dyn JitterSleep,
        jobs: &[Job],
        known: &HashSet<String>,
    ) {
        let ordered = job_order(jobs);
        let last = ordered.len();
        for (idx, (symbol, interval)) in ordered.iter().enumerate() {
            if self.abort {
                break;
            }
            if !known.contains(symbol) {
                continue;
            }
            if !self.process_symbol(provider, stores, reporter, symbol, interval, jobs) {
                match provider.renew() {
                    Ok(()) => {
                        if !self.process_symbol(provider, stores, reporter, symbol, interval, jobs)
                        {
                            self.abort = true;
                            break;
                        }
                    }
                    Err(_) => {
                        self.abort = true;
                        break;
                    }
                }
            }
            if !self.abort && idx < last - 1 {
                jitter.sleep(self.settings.delay_min, self.settings.delay_max);
            }
        }
    }

    /// All missing ranges for one symbol (mirrors `_process_symbol`,
    /// including the LISTING_START boundary write).
    #[allow(clippy::too_many_arguments)]
    pub fn process_symbol(
        &mut self,
        provider: &mut dyn DownloadProvider,
        stores: &mut dyn StoreFactory,
        reporter: &mut dyn DownloadReporter,
        symbol: &str,
        interval: &str,
        jobs: &[Job],
    ) -> bool {
        let mine: Vec<Job> = jobs_for(jobs, symbol, interval)
            .into_iter()
            .cloned()
            .collect();
        if mine.is_empty() {
            return true;
        }
        let mut store = stores.open(symbol, interval);
        let mut total_new = 0usize;
        for job in &mine {
            let is_head = job.reason.to_lowercase().contains("head");
            let total_chunks = count_chunks(job.from, job.to, self.settings.chunk_days);
            reporter.on_symbol_started(
                symbol,
                interval,
                job.reason,
                job.from,
                job.to,
                total_chunks,
            );
            provider.new_session();
            let (ok, new_rows, all_zero) = self.sweep(
                provider,
                &mut *store,
                reporter,
                symbol,
                interval,
                job.from,
                job.to,
                is_head,
            );
            total_new += new_rows;
            if !ok {
                return false;
            }
            // Listing boundary: head sweep found nothing and the DB has an
            // earliest row (mirrors the `all_chunks_zero` branch exactly —
            // the flag comes from `forward_sweep`, not the row count, so
            // duplicate-only sweeps do not write it).
            if is_head && all_zero {
                if let Some(earliest) = store.earliest() {
                    let date = crate::download::format_date(earliest.div_euclid(86400));
                    store.set_boundary("LISTING_START", &date, true);
                    reporter.on_status(&format!("✔  LISTING_START boundary written: {date}"));
                }
            }
        }
        let db_total = store.count();
        let trading_days = store.trading_days();
        reporter.on_symbol_finished(symbol, interval, total_new, db_total, trading_days);
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::download::{Candle, MemSink};
    use std::collections::HashMap;

    // ── scripted seams ──────────────────────────────────────────────

    struct FakeProvider {
        universe: HashSet<String>,
        symbols_error: Option<ProviderFault>,
        renew_error: Option<String>,
        renewed: usize,
        sessions: usize,
        // Preloaded rows per call: each fetch drains one scripted outcome.
        script: Vec<FetchResult>,
        calls: Vec<(Ts, Ts)>,
    }

    impl FakeProvider {
        fn with_rows(rows: Vec<(String, f64, f64, f64, f64, f64)>) -> Self {
            Self {
                universe: ["A".to_string()].into_iter().collect(),
                symbols_error: None,
                renew_error: None,
                renewed: 0,
                sessions: 0,
                script: vec![FetchResult::Rows(rows)],
                calls: Vec::new(),
            }
        }
    }

    impl DownloadProvider for FakeProvider {
        fn symbols(&mut self) -> Result<HashSet<String>, ProviderFault> {
            match self.symbols_error.clone() {
                Some(fault) => Err(fault),
                None => Ok(self.universe.clone()),
            }
        }

        fn new_session(&mut self) {
            self.sessions += 1;
        }

        fn renew(&mut self) -> Result<(), String> {
            self.renewed += 1;
            match self.renew_error.clone() {
                Some(reason) => Err(reason),
                None => Ok(()),
            }
        }

        fn fetch(&mut self, _symbol: &str, _interval: &str, from: Ts, to: Ts) -> FetchResult {
            self.calls.push((from, to));
            if self.script.is_empty() {
                return FetchResult::Rows(Vec::new());
            }
            self.script.remove(0)
        }
    }

    use std::cell::RefCell;
    use std::rc::Rc;

    #[derive(Default)]
    struct InnerStore {
        sink: MemSink,
        days: usize,
        boundaries: Vec<(String, String, bool)>,
    }

    #[derive(Clone, Default)]
    struct SharedStore {
        inner: Rc<RefCell<InnerStore>>,
    }

    impl CandleSink for SharedStore {
        fn upsert(&mut self, rows: &[Candle]) -> usize {
            self.inner.borrow_mut().sink.upsert(rows)
        }

        fn earliest(&self) -> Option<Ts> {
            self.inner.borrow().sink.earliest()
        }

        fn count(&self) -> usize {
            self.inner.borrow().sink.count()
        }
    }

    impl CandleStore for SharedStore {
        fn trading_days(&self) -> usize {
            self.inner.borrow().days
        }

        fn set_boundary(&mut self, name: &str, date: &str, verified: bool) {
            self.inner
                .borrow_mut()
                .boundaries
                .push((name.to_string(), date.to_string(), verified));
        }
    }

    #[derive(Default)]
    struct FakeStores {
        days: usize,
        cells: HashMap<(String, String), SharedStore>,
    }

    impl FakeStores {
        fn with_days(days: usize) -> Self {
            Self {
                days,
                cells: HashMap::new(),
            }
        }

        fn boundaries(&self, symbol: &str, interval: &str) -> Vec<(String, String, bool)> {
            self.cells
                .get(&(symbol.to_string(), interval.to_string()))
                .map(|cell| cell.inner.borrow().boundaries.clone())
                .unwrap_or_default()
        }
    }

    impl StoreFactory for FakeStores {
        fn open(&mut self, symbol: &str, interval: &str) -> Box<dyn CandleStore> {
            let key = (symbol.to_string(), interval.to_string());
            if !self.cells.contains_key(&key) {
                let mut inner = InnerStore::default();
                inner.days = self.days;
                self.cells.insert(
                    key.clone(),
                    SharedStore {
                        inner: Rc::new(RefCell::new(inner)),
                    },
                );
            }
            Box::new(self.cells[&key].clone())
        }
    }

    #[derive(Default)]
    struct MemReporter {
        statuses: Vec<String>,
        errors: Vec<(String, String, String)>,
        started: Vec<(String, String, String)>,
        finished: Vec<(String, String, usize, usize, usize)>,
        coverage: usize,
        chunks: Vec<(String, String, usize, usize)>,
    }

    impl DownloadReporter for MemReporter {
        fn on_status(&mut self, message: &str) {
            self.statuses.push(message.to_string());
        }

        fn on_error(&mut self, symbol: &str, interval: &str, message: &str) {
            self.errors.push((
                symbol.to_string(),
                interval.to_string(),
                message.to_string(),
            ));
        }

        fn on_symbol_started(
            &mut self,
            symbol: &str,
            interval: &str,
            reason: &str,
            _from: Ts,
            _to: Ts,
            _total: usize,
        ) {
            self.started
                .push((symbol.to_string(), interval.to_string(), reason.to_string()));
        }

        fn on_symbol_finished(
            &mut self,
            symbol: &str,
            interval: &str,
            new_rows: usize,
            db_total: usize,
            trading_days: usize,
        ) {
            self.finished.push((
                symbol.to_string(),
                interval.to_string(),
                new_rows,
                db_total,
                trading_days,
            ));
        }

        fn on_coverage(&mut self, _info: &ScanInfo) {
            self.coverage += 1;
        }

        fn on_chunk(&mut self, symbol: &str, interval: &str, index: usize, total: usize) {
            self.chunks
                .push((symbol.to_string(), interval.to_string(), index, total));
        }
    }

    struct FakeLock {
        grant: bool,
        released: usize,
    }

    impl RunLock for FakeLock {
        fn acquire(&mut self) -> bool {
            self.grant
        }

        fn release(&mut self) {
            self.released += 1;
        }
    }

    struct FakeHeartbeat {
        stops: usize,
    }

    impl HeartbeatCtl for FakeHeartbeat {
        fn start(&mut self) {}

        fn stop(&mut self) {
            self.stops += 1;
        }
    }

    struct FakeJitter {
        sleeps: Vec<(f64, f64)>,
    }

    impl JitterSleep for FakeJitter {
        fn sleep(&mut self, lo: f64, hi: f64) {
            self.sleeps.push((lo, hi));
        }
    }

    struct FakeScan {
        infos: Vec<ScanInfo>,
        scans: usize,
        cleanups: usize,
    }

    impl CoverageScan for FakeScan {
        fn scan_all(&mut self, _symbols: &[(String, String)]) -> Vec<ScanInfo> {
            self.scans += 1;
            self.infos.clone()
        }

        fn cleanup(&mut self, _infos: &[ScanInfo]) {
            self.cleanups += 1;
        }
    }

    fn settings() -> EngineSettings {
        EngineSettings {
            chunk_days: 200,
            max_passes: 5,
            delay_min: 0.0,
            delay_max: 0.0,
            provider: "zerodha".to_string(),
            exchange: "NSE".to_string(),
            data_dir: "D:\\data".to_string(),
            target_start: 0,
            today_end: i64::MAX,
        }
    }

    fn engine() -> DownloadEngine {
        DownloadEngine::new(settings())
    }

    fn closed_market() -> MarketState {
        MarketState {
            open: false,
            now_label: "2026-01-01 10:00:00".to_string(),
        }
    }

    fn ts(day: i64) -> Ts {
        day * 86400 + 9 * 3600 + 15 * 60
    }

    fn row(day: i64) -> (String, f64, f64, f64, f64, f64) {
        let base = ts(day);
        let stamp = crate::download::format_ts(base);
        (stamp, 100.0, 101.0, 99.0, 102.0, 1000.0)
    }

    // ── gates ─────────────────────────────────────────────────────

    #[test]
    fn aborted_short_circuits() {
        let mut engine = engine();
        engine.cancel();
        let mut provider = FakeProvider::with_rows(vec![row(1)]);
        let mut stores = FakeStores::with_days(1);
        let mut reporter = MemReporter::default();
        let mut lock = FakeLock {
            grant: true,
            released: 0,
        };
        let mut heartbeat = FakeHeartbeat { stops: 0 };
        let out = engine.run_download(
            &mut provider,
            &mut stores,
            &mut reporter,
            &mut lock,
            &mut heartbeat,
            &closed_market(),
            "A",
            "15m",
            ts(1),
            ts(2),
        );
        assert_eq!(out.unwrap(), DownloadOutcome::Aborted);
        assert_eq!(lock.released, 0);
    }

    #[test]
    fn market_open_blocks_with_stamp() {
        let mut engine = engine();
        let mut provider = FakeProvider::with_rows(vec![]);
        let mut stores = FakeStores::with_days(0);
        let mut reporter = MemReporter::default();
        let mut lock = FakeLock {
            grant: true,
            released: 0,
        };
        let mut heartbeat = FakeHeartbeat { stops: 0 };
        let out = engine.run_download(
            &mut provider,
            &mut stores,
            &mut reporter,
            &mut lock,
            &mut heartbeat,
            &MarketState {
                open: true,
                now_label: "2026-01-01 10:00:00".to_string(),
            },
            "A",
            "15m",
            ts(1),
            ts(2),
        );
        assert_eq!(out.unwrap(), DownloadOutcome::BlockedMarketOpen);
        assert!(reporter
            .statuses
            .iter()
            .any(|m| m.contains("MARKET IS OPEN") && m.contains("2026-01-01 10:00:00")));
    }

    #[test]
    fn locked_reports_and_skips() {
        let mut engine = engine();
        let mut provider = FakeProvider::with_rows(vec![]);
        let mut stores = FakeStores::with_days(0);
        let mut reporter = MemReporter::default();
        let mut lock = FakeLock {
            grant: false,
            released: 0,
        };
        let mut heartbeat = FakeHeartbeat { stops: 0 };
        let out = engine.run_download(
            &mut provider,
            &mut stores,
            &mut reporter,
            &mut lock,
            &mut heartbeat,
            &closed_market(),
            "A",
            "15m",
            ts(1),
            ts(2),
        );
        assert_eq!(out.unwrap(), DownloadOutcome::Locked);
        assert_eq!(reporter.errors.len(), 1);
        assert_eq!(heartbeat.stops, 0);
    }

    #[test]
    fn happy_path_downloads_and_releases() {
        let mut engine = engine();
        let mut provider = FakeProvider::with_rows(vec![row(1), row(2)]);
        let mut stores = FakeStores::with_days(2);
        let mut reporter = MemReporter::default();
        let mut lock = FakeLock {
            grant: true,
            released: 0,
        };
        let mut heartbeat = FakeHeartbeat { stops: 0 };
        let out = engine.run_download(
            &mut provider,
            &mut stores,
            &mut reporter,
            &mut lock,
            &mut heartbeat,
            &closed_market(),
            "A",
            "15m",
            ts(1),
            ts(2),
        );
        match out.unwrap() {
            DownloadOutcome::OkSingle {
                new_rows, db_total, ..
            } => {
                assert_eq!(new_rows, 2);
                assert_eq!(db_total, 2);
            }
            other => panic!("unexpected {other:?}"),
        }
        assert_eq!(lock.released, 1);
        assert_eq!(heartbeat.stops, 1);
        assert_eq!(provider.sessions, 1);
        assert!(!reporter.chunks.is_empty());
        assert_eq!(reporter.finished.len(), 1);
    }

    #[test]
    fn unknown_symbol_reports() {
        let mut engine = engine();
        let mut provider = FakeProvider::with_rows(vec![]);
        let mut stores = FakeStores::with_days(0);
        let mut reporter = MemReporter::default();
        let out = engine.download_one(
            &mut provider,
            &mut stores,
            &mut reporter,
            "GHOST",
            "15m",
            ts(1),
            ts(2),
        );
        assert_eq!(out.unwrap(), DownloadOutcome::UnknownSymbol);
        assert!(reporter
            .errors
            .iter()
            .any(|(_, _, m)| m.contains("NSE instrument map")));
    }

    #[test]
    fn auth_symbols_maps_to_auth_failed() {
        let mut engine = engine();
        let mut provider = FakeProvider::with_rows(vec![]);
        provider.symbols_error = Some(ProviderFault::Auth("expired".to_string()));
        let mut stores = FakeStores::with_days(0);
        let mut reporter = MemReporter::default();
        let out = engine.download_one(
            &mut provider,
            &mut stores,
            &mut reporter,
            "A",
            "15m",
            ts(1),
            ts(2),
        );
        assert!(matches!(out.unwrap(), DownloadOutcome::AuthFailed { .. }));
        assert!(reporter
            .errors
            .iter()
            .any(|(_, _, m)| m.contains("authentication failed")));
    }

    #[test]
    fn token_expiry_renews_once_and_retries() {
        let mut engine = engine();
        let mut provider = FakeProvider::with_rows(vec![row(1)]);
        // First sweep sees TokenExpired, the retry sees rows.
        provider.script.insert(0, FetchResult::TokenExpired);
        let mut stores = FakeStores::with_days(1);
        let mut reporter = MemReporter::default();
        let out = engine.download_one(
            &mut provider,
            &mut stores,
            &mut reporter,
            "A",
            "15m",
            ts(1),
            ts(1),
        );
        match out.unwrap() {
            DownloadOutcome::OkSingle { new_rows, .. } => assert_eq!(new_rows, 1),
            other => panic!("unexpected {other:?}"),
        }
        assert_eq!(provider.renewed, 1);
        assert_eq!(provider.sessions, 1);
    }

    #[test]
    fn failed_renew_aborts_with_auth() {
        let mut engine = engine();
        let mut provider = FakeProvider::with_rows(vec![]);
        provider.script.insert(0, FetchResult::TokenExpired);
        provider.renew_error = Some("no network".to_string());
        let mut stores = FakeStores::with_days(0);
        let mut reporter = MemReporter::default();
        let out = engine.download_one(
            &mut provider,
            &mut stores,
            &mut reporter,
            "A",
            "15m",
            ts(1),
            ts(1),
        );
        assert!(matches!(out.unwrap(), DownloadOutcome::AuthFailed { .. }));
        assert!(reporter
            .errors
            .iter()
            .any(|(_, _, m)| m.contains("token renewal failed")));
    }

    // ── batch ─────────────────────────────────────────────────────

    fn scan_info(state: DLState) -> ScanInfo {
        ScanInfo {
            symbol: "A".to_string(),
            interval: "15m".to_string(),
            state,
            earliest: None,
            latest: None,
            missing_head: false,
            missing_tail: true,
            listing_start_verified: false,
            rows: 0,
            trading_days: 0,
        }
    }

    #[test]
    fn batch_completes_when_nothing_remaining() {
        let mut engine = engine();
        let mut provider = FakeProvider::with_rows(vec![]);
        let mut stores = FakeStores::with_days(0);
        let mut scan = FakeScan {
            infos: vec![scan_info(DLState::DownloadComplete)],
            scans: 0,
            cleanups: 0,
        };
        let mut reporter = MemReporter::default();
        let mut lock = FakeLock {
            grant: true,
            released: 0,
        };
        let mut heartbeat = FakeHeartbeat { stops: 0 };
        let mut jitter = FakeJitter { sleeps: Vec::new() };
        let symbols = vec![("A".to_string(), "15m".to_string())];
        let out = engine.run_batch(
            &mut provider,
            &mut stores,
            &mut scan,
            &mut reporter,
            &mut lock,
            &mut heartbeat,
            &mut jitter,
            &mut || symbols.clone(),
            None,
            &closed_market(),
        );
        assert_eq!(out.unwrap(), DownloadOutcome::OkBatch);
        assert!(reporter
            .statuses
            .iter()
            .any(|m| m.contains("DOWNLOAD_COMPLETE")));
        assert_eq!(lock.released, 1);
        assert_eq!(heartbeat.stops, 1);
    }

    #[test]
    fn batch_empty_symbols_is_no_symbols() {
        let mut engine = engine();
        let mut provider = FakeProvider::with_rows(vec![]);
        let mut stores = FakeStores::with_days(0);
        let mut scan = FakeScan {
            infos: Vec::new(),
            scans: 0,
            cleanups: 0,
        };
        let mut reporter = MemReporter::default();
        let mut lock = FakeLock {
            grant: true,
            released: 0,
        };
        let mut heartbeat = FakeHeartbeat { stops: 0 };
        let mut jitter = FakeJitter { sleeps: Vec::new() };
        let out = engine.run_batch(
            &mut provider,
            &mut stores,
            &mut scan,
            &mut reporter,
            &mut lock,
            &mut heartbeat,
            &mut jitter,
            &mut || Vec::new(),
            None,
            &closed_market(),
        );
        assert_eq!(out.unwrap(), DownloadOutcome::NoSymbols);
    }

    #[test]
    fn head_zero_writes_listing_boundary() {
        let mut engine = engine();
        let mut provider = FakeProvider::with_rows(vec![row(1)]);
        provider.script.push(FetchResult::Rows(Vec::new()));
        let mut stores = FakeStores::with_days(1);
        let mut reporter = MemReporter::default();
        let tail = vec![Job {
            symbol: "A".to_string(),
            interval: "15m".to_string(),
            from: ts(1),
            to: ts(1),
            reason: "tail-fill",
        }];
        assert!(engine.process_symbol(
            &mut provider,
            &mut stores,
            &mut reporter,
            "A",
            "15m",
            &tail
        ));
        assert!(stores.boundaries("A", "15m").is_empty());
        let head = vec![Job {
            symbol: "A".to_string(),
            interval: "15m".to_string(),
            from: ts(1),
            to: ts(1),
            reason: "head-fill",
        }];
        assert!(engine.process_symbol(
            &mut provider,
            &mut stores,
            &mut reporter,
            "A",
            "15m",
            &head
        ));
        assert_eq!(
            stores.boundaries("A", "15m"),
            vec![("LISTING_START".to_string(), "1970-01-02".to_string(), true)]
        );
        assert!(reporter
            .statuses
            .iter()
            .any(|m| m == "✔  LISTING_START boundary written: 1970-01-02"));
    }

    // ── status + validation ───────────────────────────────────────

    #[test]
    fn status_maps_ready_and_reason() {
        let ready = engine_status("zerodha", "NSE", "D:\\data", true, "whatever");
        assert_eq!(ready.provider_status, "ready");
        let down = engine_status("zerodha", "NSE", "D:\\data", false, "no creds");
        assert_eq!(down.provider_status, "no creds");
        assert!(!down.provider_ready);
    }
}
