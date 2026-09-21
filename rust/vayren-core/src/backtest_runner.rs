//! BacktestRunner orchestration twin — fan-out, assembly, variants, batch.
//!
//! Rust port of the orchestration mechanics of
//! `06_backtest/backtest/runner.py` (everything except the already-twinned
//! `execute_bars` loop, `slice_indices` and curve/report assembly, which are
//! reused untouched from `backtest_engine`, and the `metrics` kernels).
//!
//! | Python (`runner.py`) | Rust (here) |
//! |---|---|
//! | `run` fan-out/fan-in + error strings | [` blueprint_run`] over [`RunOne`] seam |
//! | `_run_one_vm` / `_run_one_legacy` sequencing | [`run_single`] / [`run_legacy`] |
//! | result label + field mapping | [`assemble_result`] |
//! | `_package_visuals` filtering | [`package_visuals`] over [`VisualSource`] |
//! | `run_variant_backtest` defaults + metadata | [`variant_config`], [`variant_metadata`] |
//! | `BatchSpec` / `BatchTask` / `SymbolBatchResult` | same structs, same defaults |
//! | `_window_bounds` ±7d margins | [`window_bounds`] (authority; no Python copy) |
//! | `_spec_task` mapping | [`spec_task`] |
//! | `default_batch_workers` clamp | [`default_workers`] over injected cpu count (authority) |
//! | `_run_inline` cancel/progress sequencing | [`run_inline`] |
//! | `run_symbol_batch` plan + deterministic merge | [`resolve_batch`], [`merge_batch`] |
//!
//! Seams (Python-owned, injected): strategy compile/records/registry (Python
//! strategies §2), repository fetch (SQLite IO), process pool + workers
//! (processes stay Python), research datasets (strategy-owned), wall clock.
//! `BacktestWorker` (legacy thread) stays Python.
//!
//! Deliberate narrowings, all documented at the item:
//! - `raise` → `Err(String)` / `None` with identical messages (no exceptions).
//! - Logger warnings on fetch/compile failures become the honest `None` /
//!   error-string outcomes (the log call itself stays Python).
//! - `_batch_compiled` process-global sha256 cache stays Python (process
//!   boundary + hashing; the twin never recompiles behind the seam).
//! - Series keys cross the seam stringified; sorting is by string. Python
//!   `sorted` on mixed-type keys raises (killing all series); post-seam keys
//!   are homogeneous in production shapes.
//! - Strategy-owned plot objects cross as stable strings (order + membership
//!   preserved; identity stays seam-side).
//! - Progress callbacks are infallible (`FnMut` returns nothing), mirroring
//!   the `try/except: pass` guards (same precedent as `backtest_engine`).

use std::collections::HashMap;

use crate::backtest::TradeRecord;
use crate::backtest_engine::{
    assemble_curve, assemble_report, execute_bars, slice_indices, BacktestConfig as LoopConfig,
    PerformanceReport, SignalSource,
};
use crate::market::Bar;

// ── configs + result shapes ───────────────────────────────────────────────

/// Full run request (mirrors `BacktestConfig` fields as read).
#[derive(Debug, Clone, PartialEq)]
pub struct RunConfig {
    pub symbol: String,
    pub timeframe: String,
    pub start_date: String,
    pub end_date: String,
    pub initial_capital: f64,
    pub slippage_pct: f64,
    pub commission_pct: f64,
}

impl RunConfig {
    pub fn loop_config(&self) -> LoopConfig {
        LoopConfig {
            symbol: self.symbol.clone(),
            initial_capital: self.initial_capital,
            slippage_pct: self.slippage_pct,
            commission_pct: self.commission_pct,
        }
    }
}

/// One plotted series (mirrors `ChartSeries` fields as read).
#[derive(Debug, Clone, PartialEq)]
pub struct ChartSeries {
    pub title: String,
    pub values: Vec<(i64, f64)>,
    pub style: String,
    pub extend: String,
    pub strategy: String,
}

/// Per-strategy outcome (mirrors `StrategyResult` fields as read; trade,
/// curve and report objects come from the reused twins).
#[derive(Debug, Clone, PartialEq)]
pub struct StrategyResult {
    pub strategy_id: String,
    pub name: String,
    pub bars_used: usize,
    pub period_start: Option<String>,
    pub period_end: Option<String>,
    pub trades: Vec<TradeRecord>,
    pub equities: Vec<f64>,
    pub drawdowns: Vec<f64>,
    pub report: PerformanceReport,
    pub chart_series: Vec<ChartSeries>,
    pub chart_plots: Vec<String>,
    pub muted_bars: Vec<i64>,
}

/// Whole-request outcome (mirrors `BacktestResult` fields as read).
#[derive(Debug, Clone, PartialEq)]
pub struct BacktestResult {
    pub results: Vec<StrategyResult>,
    pub has_error: bool,
    pub error: Option<String>,
    pub error_detail: Option<String>,
}

// ── seams ─────────────────────────────────────────────────────────────────

/// Bar fetch (repository SQLite IO stays Python).
pub trait BarFetch {
    fn fetch(&mut self, symbol: &str, timeframe: &str) -> Result<Vec<Bar>, String>;
}

/// One compiled strategy (compile + record lookup stay Python).
pub struct BuiltStrategy {
    pub record_id: String,
    pub record_name: String,
    pub record_version: Option<String>,
    pub source: Box<dyn SignalSource>,
    pub series: Vec<SeriesInput>,
    pub plots: Vec<String>,
    pub muted: Vec<VisualValue>,
}

/// Raw series input (seam-normalized from the strategy's dict shapes).
#[derive(Debug, Clone, PartialEq)]
pub struct SeriesInput {
    /// `(owner_id, title)` when the key was a pair, else `None`.
    pub owner: Option<(String, String)>,
    /// The title half of the key (or the whole key).
    pub title: String,
    /// Raw `(key, value)` pairs; keys parse as `int`, `None` values skip.
    /// One unparseable key kills ALL series (mirrors the outer `except`).
    pub points: Vec<(String, Option<f64>)>,
    /// Series meta (style/extend); non-dict in Python resets to `{}`.
    pub style: Option<String>,
    pub extend: Option<String>,
}

/// Raw muted-bar values (mirrors the duck-typed filter input).
#[derive(Debug, Clone, PartialEq)]
pub enum VisualValue {
    Int(i64),
    Bool(bool),
    Float(f64),
    Str(String),
    Null,
}

pub trait RunOne {
    fn is_unknown(&mut self, strategy_id: &str) -> bool;
    fn build(&mut self, strategy_id: &str) -> Result<Option<BuiltStrategy>, String>;
    fn build_legacy(&mut self, strategy_id: &str) -> Result<Option<LegacyBuilt>, String>;
    fn on_progress(&mut self, done: usize, total: usize);
}

/// Legacy factory product (deprecated registry path).
pub struct LegacyBuilt {
    pub definition_id: String,
    pub label: String,
    pub enabled: bool,
    pub source: Box<dyn SignalSource>,
}

// ── single-run assembly ───────────────────────────────────────────────────

/// Display label (mirrors `"NAME v1.0"` / bare-name fallback).
pub fn result_label(name: &str, version: Option<&str>) -> String {
    match version {
        Some(version) => format!("{name} v{version}"),
        None => name.to_string(),
    }
}

/// Assemble one strategy outcome from executed pieces (mirrors the tail of
/// `_run_one_vm` / `_run_one_legacy` / `_run_batch_symbol`).
#[allow(clippy::too_many_arguments)]
pub fn assemble_result(
    strategy_id: &str,
    name: String,
    window: &[Bar],
    trades: Vec<TradeRecord>,
    initial_capital: f64,
    chart_series: Vec<ChartSeries>,
    chart_plots: Vec<String>,
    muted_bars: Vec<i64>,
) -> StrategyResult {
    let pnls: Vec<f64> = trades.iter().map(|t| t.pnl).collect();
    let bars_held: Vec<f64> = trades.iter().map(|t| t.bars_held as f64).collect();
    let (equities, drawdowns) = assemble_curve(initial_capital, &pnls);
    let final_equity = equities.last().copied().unwrap_or(initial_capital);
    let report = assemble_report(&pnls, &bars_held, initial_capital, final_equity);
    StrategyResult {
        strategy_id: strategy_id.to_string(),
        name,
        bars_used: window.len(),
        period_start: window.first().map(|b| b.timestamp.clone()),
        period_end: window.last().map(|b| b.timestamp.clone()),
        trades,
        equities,
        drawdowns,
        report,
        chart_series,
        chart_plots,
        muted_bars,
    }
}

/// Visual packaging (mirrors `_package_visuals`).
pub fn package_visuals(
    series: &[SeriesInput],
    plots: Vec<String>,
    muted: &[VisualValue],
    fallback_owner: &str,
    fallback_name: &str,
) -> (Vec<ChartSeries>, Vec<String>, Vec<i64>) {
    let mut chart_series = Vec::new();
    let mut failed = false;
    for input in series {
        let mut points = Vec::new();
        // Keys sort as strings (homogeneous production shapes; see docs).
        let mut ordered = input.points.clone();
        ordered.sort_by(|a, b| a.0.cmp(&b.0));
        for (key, value) in &ordered {
            let value = match value {
                Some(value) => *value,
                None => continue,
            };
            let index: i64 = match key.parse() {
                Ok(index) => index,
                Err(_) => {
                    failed = true;
                    break;
                }
            };
            points.push((index, value));
        }
        if failed {
            break;
        }
        if points.is_empty() {
            continue;
        }
        let (owner_id, title) = match &input.owner {
            Some((owner, title)) => (owner.clone(), title.clone()),
            None => (fallback_owner.to_string(), input.title.clone()),
        };
        chart_series.push(ChartSeries {
            title: title.clone(),
            values: points,
            style: input.style.clone().unwrap_or_else(|| "line".to_string()),
            extend: input
                .extend
                .clone()
                .unwrap_or_else(|| "session".to_string()),
            strategy: if owner_id.is_empty() {
                fallback_name.to_string()
            } else {
                owner_id
            },
        });
    }
    if failed {
        chart_series = Vec::new();
    }
    let muted_bars: Vec<i64> = muted
        .iter()
        .filter_map(|value| match value {
            VisualValue::Int(value) if *value >= 0 => Some(*value),
            _ => None,
        })
        .collect();
    (chart_series, plots, muted_bars)
}

// ── fan-out ───────────────────────────────────────────────────────────────

/// Run one id end-to-end (mirrors `_run_one_by_id` + `_run_one_vm` minus
/// the Python-owned compile/fetch calls, which arrive via seams).
pub fn run_single<F>(
    runner: &mut F,
    strategy_id: &str,
    config: &RunConfig,
    include_visuals: bool,
) -> Result<Option<StrategyResult>, String>
where
    F: RunOne + BarFetch,
{
    let built = match runner.build(strategy_id) {
        Ok(Some(built)) => built,
        Ok(None) => return runner.run_legacy_id(strategy_id, config, include_visuals),
        Err(_) => return Ok(None),
    };
    let bars = match runner.fetch(&config.symbol, &config.timeframe) {
        Ok(bars) => bars,
        Err(_) => return Ok(None),
    };
    let stamps: Vec<&str> = bars.iter().map(|b| b.timestamp.as_str()).collect();
    let kept = slice_indices(&stamps, Some(&config.start_date), Some(&config.end_date));
    if kept.is_empty() {
        return Ok(None);
    }
    let window: Vec<Bar> = kept.into_iter().map(|i| bars[i].clone()).collect();
    let loop_config = config.loop_config();
    let mut progress = |done: usize, total: usize| runner.on_progress(done, total);
    let mut source = built.source;
    let trades = execute_bars(&window, &mut *source, &loop_config, &mut progress);
    let (chart_series, chart_plots, muted_bars) = if include_visuals {
        package_visuals(
            &built.series,
            built.plots,
            &built.muted,
            &built.record_id,
            &built.record_name,
        )
    } else {
        (Vec::new(), Vec::new(), Vec::new())
    };
    Ok(Some(assemble_result(
        &built.record_id,
        result_label(&built.record_name, built.record_version.as_deref()),
        &window,
        trades,
        config.initial_capital,
        chart_series,
        chart_plots,
        muted_bars,
    )))
}

/// Legacy registry path (mirrors `_run_one_legacy`; deprecated upstream).
pub fn run_legacy_single<F>(
    runner: &mut F,
    built: LegacyBuilt,
    config: &RunConfig,
) -> Result<Option<StrategyResult>, String>
where
    F: RunOne + BarFetch,
{
    if !built.enabled {
        return Ok(None);
    }
    let bars = match runner.fetch(&config.symbol, &config.timeframe) {
        Ok(bars) => bars,
        Err(_) => return Ok(None),
    };
    let stamps: Vec<&str> = bars.iter().map(|b| b.timestamp.as_str()).collect();
    let kept = slice_indices(&stamps, Some(&config.start_date), Some(&config.end_date));
    if kept.is_empty() {
        return Ok(None);
    }
    let window: Vec<Bar> = kept.into_iter().map(|i| bars[i].clone()).collect();
    let loop_config = config.loop_config();
    let mut progress = |done: usize, total: usize| runner.on_progress(done, total);
    let mut source = built.source;
    let trades = execute_bars(&window, &mut *source, &loop_config, &mut progress);
    Ok(Some(assemble_result(
        &built.definition_id,
        built.label,
        &window,
        trades,
        config.initial_capital,
        Vec::new(),
        Vec::new(),
        Vec::new(),
    )))
}

/// Helper so `run_single` can fall back without exposing the seam twice.
trait RunLegacyId {
    fn run_legacy_id(
        &mut self,
        strategy_id: &str,
        config: &RunConfig,
        include_visuals: bool,
    ) -> Result<Option<StrategyResult>, String>;
}

impl<F: RunOne + BarFetch> RunLegacyId for F {
    fn run_legacy_id(
        &mut self,
        strategy_id: &str,
        config: &RunConfig,
        _include_visuals: bool,
    ) -> Result<Option<StrategyResult>, String> {
        match self.build_legacy(strategy_id) {
            Ok(Some(built)) => run_legacy_single(self, built, config),
            Ok(None) | Err(_) => Ok(None),
        }
    }
}

/// Whole-request fan-out (mirrors `run` including every error string).
pub fn blueprint_run<F>(
    runner: &mut F,
    config: &RunConfig,
    strategy_ids: &[String],
) -> BacktestResult
where
    F: RunOne + BarFetch,
{
    let mut results = Vec::new();
    let mut errors = Vec::new();
    for strategy_id in strategy_ids {
        match run_single(runner, strategy_id, config, true) {
            Ok(Some(result)) => results.push(result),
            Ok(None) => {
                if runner.is_unknown(strategy_id) {
                    errors.push(format!("unknown strategy {strategy_id}"));
                } else {
                    errors.push(format!(
                        "{strategy_id}: no bars in requested range or compilation failed"
                    ));
                }
            }
            Err(reason) => errors.push(format!("{strategy_id}: {reason}")),
        }
    }
    if errors.is_empty() {
        return BacktestResult {
            results,
            has_error: false,
            error: None,
            error_detail: None,
        };
    }
    if results.is_empty() {
        return BacktestResult {
            results,
            has_error: true,
            error: Some("no_results".to_string()),
            error_detail: Some(errors.join("; ")),
        };
    }
    BacktestResult {
        results,
        has_error: true,
        error: None,
        error_detail: Some(errors.join("; ")),
    }
}

// ── variant ───────────────────────────────────────────────────────────────

/// Variant config defaults from a data identity (mirrors
/// `run_variant_backtest`'s mapping; registry/runner/dataset stay Python).
pub fn variant_config(identity: &HashMap<String, String>, initial_capital: f64) -> RunConfig {
    RunConfig {
        symbol: identity
            .get("symbol")
            .cloned()
            .unwrap_or_else(|| "UNKNOWN".to_string()),
        timeframe: identity
            .get("timeframe")
            .cloned()
            .unwrap_or_else(|| "1D".to_string()),
        start_date: identity
            .get("start_date")
            .cloned()
            .unwrap_or_else(|| "2000-01-01".to_string()),
        end_date: identity
            .get("end_date")
            .cloned()
            .unwrap_or_else(|| "2025-12-31".to_string()),
        slippage_pct: identity
            .get("slippage_pct")
            .and_then(|v| v.parse().ok())
            .unwrap_or(0.0),
        commission_pct: identity
            .get("commission_pct")
            .and_then(|v| v.parse().ok())
            .unwrap_or(0.0),
        initial_capital,
    }
}

/// Variant metadata assembly (mirrors the metadata dict + id threading).
pub fn variant_metadata(
    execution_id: &str,
    strategy_id: &str,
    version_id: &str,
    prior_executions: &[String],
    variant: Option<String>,
    param: Option<String>,
) -> (Vec<String>, HashMap<String, String>) {
    let mut executions = vec![execution_id.to_string()];
    executions.extend(prior_executions.iter().cloned());
    let mut metadata = HashMap::new();
    metadata.insert(
        "variant".to_string(),
        variant.unwrap_or_else(|| "None".to_string()),
    );
    metadata.insert(
        "param".to_string(),
        param.unwrap_or_else(|| "None".to_string()),
    );
    metadata.insert("execution_id".to_string(), execution_id.to_string());
    metadata.insert("backtest_executed".to_string(), "True".to_string());
    metadata.insert("strategy_id".to_string(), strategy_id.to_string());
    metadata.insert("version_id".to_string(), version_id.to_string());
    (executions, metadata)
}

// ── batch ─────────────────────────────────────────────────────────────────

/// Immutable batch inputs (mirrors `BatchSpec` + defaults).
#[derive(Debug, Clone, PartialEq)]
pub struct BatchSpec {
    pub strategy_id: String,
    pub strategy_name: String,
    pub strategy_version: String,
    pub strategy_code: String,
    pub symbols: Vec<String>,
    pub timeframe: String,
    pub start_date: String,
    pub end_date: String,
    pub initial_capital: f64,
    pub slippage_pct: f64,
    pub commission_pct: f64,
    pub include_plots: bool,
    pub max_workers: usize,
}

impl Default for BatchSpec {
    fn default() -> Self {
        Self {
            strategy_id: String::new(),
            strategy_name: String::new(),
            strategy_version: String::new(),
            strategy_code: String::new(),
            symbols: Vec::new(),
            timeframe: String::new(),
            start_date: String::new(),
            end_date: String::new(),
            initial_capital: 1_000_000.0,
            slippage_pct: 0.02,
            commission_pct: 0.03,
            include_plots: false,
            max_workers: 0,
        }
    }
}

/// One picklable unit of batch work (mirrors `BatchTask`).
#[derive(Debug, Clone, PartialEq)]
pub struct BatchTask {
    pub data_dir: String,
    pub strategy_id: String,
    pub strategy_name: String,
    pub strategy_version: String,
    pub strategy_code: String,
    pub symbol: String,
    pub timeframe: String,
    pub start_date: String,
    pub end_date: String,
    pub initial_capital: f64,
    pub slippage_pct: f64,
    pub commission_pct: f64,
    pub include_plots: bool,
}

/// Terminal per-symbol outcome (mirrors `SymbolBatchResult`).
#[derive(Debug, Clone, PartialEq)]
pub struct SymbolBatchResult {
    pub symbol: String,
    pub result: Option<StrategyResult>,
    pub error: Option<String>,
}

fn days_from_civil(year: i64, month: i64, day: i64) -> i64 {
    let y = if month <= 2 { year - 1 } else { year };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let doy = (153 * (if month > 2 { month - 3 } else { month + 9 }) + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146097 + doe - 719468
}

fn civil_from_days(z: i64) -> (i64, i64, i64) {
    let z = z + 719468;
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

fn days_in_month(year: i64, month: i64) -> i64 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        // `month == 2` here; the other arms above cover 1..=12.
        _ => {
            if year % 4 == 0 && (year % 100 != 0 || year % 400 == 0) {
                29
            } else {
                28
            }
        }
    }
}

fn parse_date(text: &str) -> Option<(i64, i64, i64)> {
    let parts: Vec<&str> = text.split('-').collect();
    if parts.len() != 3 {
        return None;
    }
    let year: i64 = parts[0].parse().ok()?;
    let month: i64 = parts[1].parse().ok()?;
    let day: i64 = parts[2].parse().ok()?;
    if !(1..=12).contains(&month) || !(1..=days_in_month(year, month)).contains(&day) {
        return None;
    }
    Some((year, month, day))
}

/// Aggregation-window bounds for a `[start, end]` date range, with safety
/// margins: the single authority for the rule (Python bridges call this; no
/// copy of the margin arithmetic lives outside here).
///
/// Buckets are built from real rows only, so a bucket intersecting the
/// requested range needs every row it contains: weekly buckets span up to 7
/// calendar days (Monday–Sunday), hence ±7 days. Intraday/daily buckets never
/// leave their own day. Detection (base duration, session anchor) stays on
/// the unbounded latest sample, so alignment never changes.
///
/// `None` when either input is not a real `YYYY-MM-DD` day (`"00:00:00"` /
/// `"23:59:59"` stamps on success).
pub fn window_bounds(start_date: &str, end_date: &str) -> Option<(String, String)> {
    let (sy, sm, sd) = parse_date(start_date)?;
    let (ey, em, ed) = parse_date(end_date)?;
    let (ly, lm, ld) = civil_from_days(days_from_civil(sy, sm, sd) - 7);
    let (uy, um, ud) = civil_from_days(days_from_civil(ey, em, ed) + 7);
    Some((
        format!("{ly:04}-{lm:02}-{ld:02} 00:00:00"),
        format!("{uy:04}-{um:02}-{ud:02} 23:59:59"),
    ))
}

/// Batch task derivation (mirrors `_spec_task`).
pub fn spec_task(data_dir: &str, spec: &BatchSpec, symbol: &str) -> BatchTask {
    BatchTask {
        data_dir: data_dir.to_string(),
        strategy_id: spec.strategy_id.clone(),
        strategy_name: spec.strategy_name.clone(),
        strategy_version: spec.strategy_version.clone(),
        strategy_code: spec.strategy_code.clone(),
        symbol: symbol.to_string(),
        timeframe: spec.timeframe.clone(),
        start_date: spec.start_date.clone(),
        end_date: spec.end_date.clone(),
        initial_capital: spec.initial_capital,
        slippage_pct: spec.slippage_pct,
        commission_pct: spec.commission_pct,
        include_plots: spec.include_plots,
    }
}

/// Bounded batch worker count: the clamp rule itself, over an injected cpu
/// count (`0` = unknown machine → 4, then clamped to 2..=6). `os` stays out —
/// the caller probes the machine and Python never re-clamps.
pub fn default_workers(cpu_count: usize) -> usize {
    let cpu = if cpu_count == 0 { 4 } else { cpu_count };
    cpu.clamp(2, 6)
}

/// Inline-vs-pool plan (mirrors `run_symbol_batch`'s decision; the process
/// pool itself stays Python).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BatchPlan {
    Empty,
    Inline,
    Pool(usize),
}

pub fn resolve_batch(total_symbols: usize, max_workers: usize, cpu_count: usize) -> BatchPlan {
    if total_symbols == 0 {
        return BatchPlan::Empty;
    }
    let workers = if max_workers > 0 {
        max_workers
    } else {
        default_workers(cpu_count)
    };
    if total_symbols == 1 || workers <= 1 {
        BatchPlan::Inline
    } else {
        BatchPlan::Pool(workers)
    }
}

/// Deterministic order merge (mirrors the tail of `run_symbol_batch`:
/// spec order wins; missing symbols resolve as `"cancelled"`).
pub fn merge_batch(
    symbols: &[String],
    by_symbol: &HashMap<String, SymbolBatchResult>,
    cancelled: bool,
) -> Vec<SymbolBatchResult> {
    symbols
        .iter()
        .map(|symbol| {
            if let Some(outcome) = by_symbol.get(symbol) {
                outcome.clone()
            } else {
                // Missing symbols resolve as "cancelled" (mirrors the
                // `.get` default; `cancelled` only decides the fill loop).
                let _ = cancelled;
                SymbolBatchResult {
                    symbol: symbol.clone(),
                    result: None,
                    error: Some("cancelled".to_string()),
                }
            }
        })
        .collect()
}

/// Sequential fallback (mirrors `_run_inline`: compile-once is seam-side;
/// cancel marks the remainder; progress guarded).
pub fn run_inline<F>(
    runner: &mut F,
    data_dir: &str,
    spec: &BatchSpec,
    should_cancel: &mut dyn FnMut() -> bool,
    on_stock: &mut dyn FnMut(usize, usize),
) -> Vec<SymbolBatchResult>
where
    F: BatchSymbol,
{
    let total = spec.symbols.len();
    let mut outcomes = Vec::new();
    for (done, symbol) in spec.symbols.iter().enumerate() {
        let done = done + 1;
        if should_cancel() {
            outcomes.extend(spec.symbols[done - 1..].iter().map(|s| SymbolBatchResult {
                symbol: s.clone(),
                result: None,
                error: Some("cancelled".to_string()),
            }));
            break;
        }
        outcomes.push(runner.run_symbol(data_dir, spec, symbol));
        on_stock(done, total);
    }
    outcomes
}

/// One symbol of a batch (mirrors `_run_batch_symbol` minus the
/// repository/compile calls, which arrive via seams).
pub trait BatchSymbol {
    fn run_symbol(&mut self, data_dir: &str, spec: &BatchSpec, symbol: &str) -> SymbolBatchResult;
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backtest_engine::{Signal, SignalKind};

    struct ScriptedSource {
        warmup: usize,
        signals: Vec<(usize, Signal)>,
    }

    impl SignalSource for ScriptedSource {
        fn warmup(&self) -> usize {
            self.warmup
        }

        fn signal(
            &mut self,
            index: usize,
            _position: Option<(crate::backtest::Side, f64, usize)>,
        ) -> Option<Signal> {
            if let Some(pos) = self.signals.iter().position(|(i, _)| *i == index) {
                return Some(self.signals.remove(pos).1);
            }
            None
        }
    }

    fn bar(ts: &str, close: f64) -> Bar {
        Bar::new("A", ts, close - 1.0, close + 1.0, close - 2.0, close, 1000)
    }

    fn bars(n: usize) -> Vec<Bar> {
        (0..n)
            .map(|i| {
                bar(
                    &format!("2026-01-{:02} 09:15:00", 1 + (i % 28)),
                    100.0 + i as f64,
                )
            })
            .collect()
    }

    struct ScriptedRunner {
        bars: Vec<Bar>,
        fetch_error: bool,
        build_error: bool,
        unknown: Vec<String>,
        legacy: HashMap<String, LegacyBuilt>,
        progress: Vec<(usize, usize)>,
    }

    impl BarFetch for ScriptedRunner {
        fn fetch(&mut self, _symbol: &str, _timeframe: &str) -> Result<Vec<Bar>, String> {
            if self.fetch_error {
                return Err("db gone".to_string());
            }
            Ok(self.bars.clone())
        }
    }

    impl RunOne for ScriptedRunner {
        fn is_unknown(&mut self, strategy_id: &str) -> bool {
            self.unknown.contains(&strategy_id.to_string())
        }

        fn build(&mut self, strategy_id: &str) -> Result<Option<BuiltStrategy>, String> {
            if self.build_error {
                return Err("compile boom".to_string());
            }
            if strategy_id == "missing" || strategy_id == "ghost" {
                return Ok(None);
            }
            Ok(Some(BuiltStrategy {
                record_id: "rec-1".to_string(),
                record_name: "Demo".to_string(),
                record_version: Some("1.0".to_string()),
                source: Box::new(ScriptedSource {
                    warmup: 0,
                    signals: vec![(
                        2,
                        Signal {
                            kind: SignalKind::Buy,
                            stop_loss: None,
                            take_profit: None,
                        },
                    )],
                }),
                series: Vec::new(),
                plots: Vec::new(),
                muted: Vec::new(),
            }))
        }

        fn build_legacy(&mut self, _strategy_id: &str) -> Result<Option<LegacyBuilt>, String> {
            Ok(self.legacy.remove(_strategy_id))
        }

        fn on_progress(&mut self, done: usize, total: usize) {
            self.progress.push((done, total));
        }
    }

    fn config() -> RunConfig {
        RunConfig {
            symbol: "A".to_string(),
            timeframe: "1D".to_string(),
            start_date: "2026-01-01".to_string(),
            end_date: "2026-01-31".to_string(),
            initial_capital: 100_000.0,
            slippage_pct: 0.02,
            commission_pct: 0.03,
        }
    }

    fn runner() -> ScriptedRunner {
        ScriptedRunner {
            bars: bars(10),
            fetch_error: false,
            build_error: false,
            unknown: vec!["ghost".to_string()],
            legacy: HashMap::new(),
            progress: Vec::new(),
        }
    }

    // ── assembly ──────────────────────────────────────────────────

    #[test]
    fn label_prefers_versioned_name() {
        assert_eq!(result_label("Demo", Some("1.0")), "Demo v1.0");
        assert_eq!(result_label("Demo", None), "Demo");
    }

    #[test]
    fn single_run_assembles_result() {
        let mut runner = runner();
        let result = run_single(&mut runner, "demo", &config(), true)
            .unwrap()
            .unwrap();
        assert_eq!(result.strategy_id, "rec-1");
        assert_eq!(result.name, "Demo v1.0");
        assert_eq!(result.bars_used, 10);
        assert_eq!(result.period_start.as_deref(), Some("2026-01-01 09:15:00"));
        assert!(!runner.progress.is_empty());
    }

    #[test]
    fn empty_window_returns_none() {
        let mut runner = runner();
        runner.bars = Vec::new();
        assert!(run_single(&mut runner, "demo", &config(), true)
            .unwrap()
            .is_none());
    }

    #[test]
    fn fetch_failure_returns_none() {
        let mut runner = runner();
        runner.fetch_error = true;
        assert!(run_single(&mut runner, "demo", &config(), true)
            .unwrap()
            .is_none());
    }

    #[test]
    fn build_failure_returns_none() {
        let mut runner = runner();
        runner.build_error = true;
        assert!(run_single(&mut runner, "demo", &config(), true)
            .unwrap()
            .is_none());
    }

    // ── fan-out ───────────────────────────────────────────────────

    #[test]
    fn fan_out_mixes_results_and_errors() {
        let mut runner = runner();
        let out = blueprint_run(
            &mut runner,
            &config(),
            &[
                "demo".to_string(),
                "ghost".to_string(),
                "missing".to_string(),
            ],
        );
        assert_eq!(out.results.len(), 1);
        assert!(out.has_error);
        assert!(out.error.is_none());
        let detail = out.error_detail.unwrap();
        assert!(detail.contains("unknown strategy ghost"));
        assert!(detail.contains("missing: no bars in requested range or compilation failed"));
    }

    #[test]
    fn all_failing_returns_no_results() {
        let mut runner = runner();
        let out = blueprint_run(&mut runner, &config(), &["ghost".to_string()]);
        assert!(out.results.is_empty());
        assert!(out.has_error);
        assert_eq!(out.error.as_deref(), Some("no_results"));
    }

    // ── visuals ───────────────────────────────────────────────────

    #[test]
    fn visuals_package_series_plots_muted() {
        let (series, plots, muted) = package_visuals(
            &[SeriesInput {
                owner: Some(("o1".to_string(), "MA".to_string())),
                title: "MA".to_string(),
                points: vec![
                    ("2".to_string(), Some(3.0)),
                    ("1".to_string(), Some(2.0)),
                    ("5".to_string(), None),
                ],
                style: None,
                extend: None,
            }],
            vec!["p1".to_string()],
            &[
                VisualValue::Int(3),
                VisualValue::Bool(true),
                VisualValue::Int(-1),
            ],
            "rec-1",
            "Demo",
        );
        assert_eq!(series.len(), 1);
        assert_eq!(series[0].values, vec![(1, 2.0), (2, 3.0)]);
        assert_eq!(series[0].style, "line");
        assert_eq!(series[0].extend, "session");
        assert_eq!(series[0].strategy, "o1");
        assert_eq!(plots, vec!["p1".to_string()]);
        assert_eq!(muted, vec![3]);
    }

    #[test]
    fn bad_series_key_kills_all_series() {
        let (series, _, _) = package_visuals(
            &[SeriesInput {
                owner: None,
                title: "MA".to_string(),
                points: vec![("abc".to_string(), Some(1.0))],
                style: None,
                extend: None,
            }],
            Vec::new(),
            &[],
            "rec-1",
            "Demo",
        );
        assert!(series.is_empty());
    }

    // ── variant ───────────────────────────────────────────────────

    #[test]
    fn variant_defaults_mirror_python() {
        let identity = HashMap::new();
        let config = variant_config(&identity, 10000.0);
        assert_eq!(config.symbol, "UNKNOWN");
        assert_eq!(config.timeframe, "1D");
        assert_eq!(config.start_date, "2000-01-01");
        assert_eq!(config.end_date, "2025-12-31");
        assert_eq!(config.slippage_pct, 0.0);
        assert_eq!(config.commission_pct, 0.0);
        assert_eq!(config.initial_capital, 10000.0);
        let (executions, metadata) = variant_metadata("e1", "s", "v", &[], None, None);
        assert_eq!(executions, vec!["e1".to_string()]);
        assert_eq!(metadata["backtest_executed"], "True");
        assert_eq!(metadata["variant"], "None");
    }

    // ── batch ─────────────────────────────────────────────────────

    #[test]
    fn window_bounds_add_weekly_margins() {
        assert_eq!(
            window_bounds("2026-01-08", "2026-01-10"),
            Some((
                "2026-01-01 00:00:00".to_string(),
                "2026-01-17 23:59:59".to_string()
            ))
        );
        // Month and year boundaries, plus a leap day.
        assert_eq!(
            window_bounds("2026-01-03", "2026-01-03"),
            Some((
                "2025-12-27 00:00:00".to_string(),
                "2026-01-10 23:59:59".to_string()
            ))
        );
        assert_eq!(
            window_bounds("2024-02-25", "2024-02-25"),
            Some((
                "2024-02-18 00:00:00".to_string(),
                "2024-03-03 23:59:59".to_string()
            ))
        );
        assert_eq!(window_bounds("junk", "2026-01-01"), None);
        // A day the month never has is rejected, not normalised into March.
        assert_eq!(window_bounds("2026-02-30", "2026-03-01"), None);
        assert_eq!(window_bounds("2024-02-30", "2024-03-01"), None);
        assert_eq!(window_bounds("2026-04-31", "2026-04-30"), None);
        assert!(window_bounds("2024-02-29", "2024-02-29").is_some());
    }

    #[test]
    fn batch_plan_and_merge_are_deterministic() {
        assert_eq!(resolve_batch(0, 0, 8), BatchPlan::Empty);
        assert_eq!(resolve_batch(1, 0, 8), BatchPlan::Inline);
        assert_eq!(resolve_batch(5, 1, 8), BatchPlan::Inline);
        assert_eq!(resolve_batch(5, 0, 8), BatchPlan::Pool(6));
        assert_eq!(resolve_batch(5, 3, 8), BatchPlan::Pool(3));
        let merged = merge_batch(&["a".to_string(), "b".to_string()], &HashMap::new(), false);
        assert_eq!(merged[0].symbol, "a");
        assert_eq!(merged[0].error.as_deref(), Some("cancelled"));
    }

    #[test]
    fn default_workers_clamps() {
        assert_eq!(default_workers(0), 4);
        assert_eq!(default_workers(1), 2);
        assert_eq!(default_workers(4), 4);
        assert_eq!(default_workers(32), 6);
    }
}
