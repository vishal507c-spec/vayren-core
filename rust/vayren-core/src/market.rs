//! Market data types — Bar (OHLCV), symbols, timeframes.
//!
//! Core domain models for market data. Database/repository orchestration
//! stays Python per constitution §1 retention policy (SQL IO boundary).

use std::fmt;

/// OHLCV bar data point.
///
/// Immutable market data for a symbol over a time period. Rust port of
/// market/models/bar.py, matching the SQLite schema.
#[derive(Debug, Clone, PartialEq)]
pub struct Bar {
    pub symbol: String,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: i64,
    pub timestamp: String,
    pub bar_size: String,
    pub vwap: Option<f64>,
    pub trades: Option<i64>,
    pub source: String,
}

impl Bar {
    /// Create a bar with required OHLCV fields.
    pub fn new(
        symbol: impl Into<String>,
        timestamp: impl Into<String>,
        open: f64,
        high: f64,
        low: f64,
        close: f64,
        volume: i64,
    ) -> Self {
        Self {
            symbol: symbol.into(),
            open,
            high,
            low,
            close,
            volume,
            timestamp: timestamp.into(),
            bar_size: "1d".to_string(),
            vwap: None,
            trades: None,
            source: String::new(),
        }
    }

    /// Price range (high - low).
    pub fn range(&self) -> f64 {
        self.high - self.low
    }

    /// Typical price: (H + L + C) / 3.
    pub fn typical_price(&self) -> f64 {
        (self.high + self.low + self.close) / 3.0
    }

    /// Midpoint: (H + L) / 2.
    pub fn midpoint(&self) -> f64 {
        (self.high + self.low) / 2.0
    }

    /// True if close >= open (bullish candle).
    pub fn is_bullish(&self) -> bool {
        self.close >= self.open
    }

    /// Return percentage: ((close - open) / open) * 100.
    pub fn return_pct(&self) -> f64 {
        return_pct(self.open, self.close)
    }

    /// True if all OHLCV values are valid (non-zero, high >= low, etc.).
    pub fn is_valid(&self) -> bool {
        self.open > 0.0
            && self.high > 0.0
            && self.low > 0.0
            && self.close > 0.0
            && self.volume >= 0
            && self.high >= self.low
            && self.high >= self.open
            && self.high >= self.close
            && self.low <= self.open
            && self.low <= self.close
    }
}

impl fmt::Display for Bar {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{} {} O:{:.2} H:{:.2} L:{:.2} C:{:.2} V:{}",
            self.symbol, self.timestamp, self.open, self.high, self.low, self.close, self.volume
        )
    }
}

/// Intraday change of one candle, in percent.
///
/// The single owner of the `(close − open) / open` semantics the chart header
/// and the quote snapshot share; a zero open answers `0.0` rather than
/// dividing. `Bar::return_pct` and the `vy_bar_return_pct` export both route
/// here — neither restates the rule.
pub fn return_pct(open: f64, close: f64) -> f64 {
    if open == 0.0 {
        0.0
    } else {
        ((close - open) / open) * 100.0
    }
}

/// Symbol metadata.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Symbol {
    pub code: String,
    pub exchange: String,
    pub name: String,
}

impl Symbol {
    pub fn new(code: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            exchange: String::new(),
            name: String::new(),
        }
    }
}

/// Canonical timeframe ladder: (label, seconds).
///
/// Mirrors `_TIMEFRAME_LADDER` in 03_market/market/timeframe/timeframe.py.
/// The ladder is the universe of candidate granularities; whether a candidate
/// actually exists for a symbol is always decided from real SQLite rows via
/// `available_timeframes` — never hardcoded as a fixed available list.
pub const TIMEFRAME_LADDER: &[(&str, i64)] = &[
    ("1m", 60),
    ("3m", 180),
    ("5m", 300),
    ("15m", 900),
    ("30m", 1800),
    ("45m", 2700),
    ("1h", 3600),
    ("2h", 7200),
    ("4h", 14400),
    ("1D", 86400),
    ("1W", 604800),
];

/// Timeframe identifier (e.g., "1m", "5m", "1d").
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct Timeframe(pub String);

impl Timeframe {
    pub fn new(s: impl Into<String>) -> Self {
        Self(s.into())
    }

    /// Seconds for this timeframe label (ladder or generated), or None if invalid.
    ///
    /// Parity with `timeframe_seconds`: the ladder match is case-insensitive,
    /// otherwise labels like `90m`/`2D`/`2W` parse as number + unit.
    pub fn to_seconds(&self) -> Option<i64> {
        timeframe_seconds(&self.0)
    }
}

/// Seconds for a timeframe label (ladder or generated), or None if invalid.
///
/// Parity with `timeframe_seconds` in 03_market/market/timeframe/timeframe.py.
pub fn timeframe_seconds(name: &str) -> Option<i64> {
    if !name.is_ascii() {
        return None;
    }
    let lower = name.to_lowercase();
    for (label, seconds) in TIMEFRAME_LADDER {
        if *label == name || label.to_lowercase() == lower {
            return Some(*seconds);
        }
    }
    parse_generated_label(name)
}

/// Parse labels like `90m`, `2D`, `2W` produced for non-ladder granularities.
///
/// Parity with `_parse_generated_label`: positive integer + known unit only.
fn parse_generated_label(name: &str) -> Option<i64> {
    if name.len() < 2 {
        return None;
    }
    let (number, unit) = name.split_at(name.len() - 1);
    let number: i64 = number.parse().ok()?;
    if number <= 0 {
        return None;
    }
    let factor = match unit {
        "s" => 1,
        "m" => 60,
        "h" => 3600,
        "D" | "d" => 86400,
        "W" | "w" => 604800,
        _ => return None,
    };
    Some(number * factor)
}

/// Ladder label for a granularity, or None if it is not in the ladder.
///
/// Parity with `timeframe_name`.
pub fn timeframe_name(seconds: i64) -> Option<&'static str> {
    TIMEFRAME_LADDER
        .iter()
        .find(|(_, ladder_seconds)| *ladder_seconds == seconds)
        .map(|(label, _)| *label)
}

/// Human label for a granularity outside the ladder, derived from seconds.
///
/// Parity with `generate_label`.
pub fn generate_label(seconds: i64) -> String {
    if seconds % 604800 == 0 {
        format!("{}W", seconds / 604800)
    } else if seconds % 86400 == 0 {
        format!("{}D", seconds / 86400)
    } else if seconds % 3600 == 0 {
        format!("{}h", seconds / 3600)
    } else if seconds % 60 == 0 {
        format!("{}m", seconds / 60)
    } else {
        format!("{}s", seconds)
    }
}

/// Timeframes a database can produce from its detected base bar duration.
///
/// Parity with `available_timeframes`: a candidate exists when it is a whole
/// multiple of the base; the base itself is always included (generated label
/// when off-ladder). Never cached — callers re-run detection against SQLite.
pub fn available_timeframes(base_seconds: i64) -> Vec<String> {
    if base_seconds <= 0 {
        return Vec::new();
    }
    let mut pairs: Vec<(i64, String)> = TIMEFRAME_LADDER
        .iter()
        .filter(|(_, seconds)| *seconds >= base_seconds && *seconds % base_seconds == 0)
        .map(|(label, seconds)| (*seconds, label.to_string()))
        .collect();
    if timeframe_name(base_seconds).is_none() {
        pairs.push((base_seconds, generate_label(base_seconds)));
    }
    pairs.sort();
    pairs.into_iter().map(|(_, label)| label).collect()
}

/// Latest close state for a symbol, derived from its last real candle.
///
/// Parity with market/models/symbol_quote.py: `price` is the latest close,
/// `change_pct` the intraday change of that candle (same (close − open)/open
/// semantics the chart header uses). Never fabricated: construct only from a
/// real `Bar` via `from_bar` (mirrors `SymbolRepository.get_quotes`, which
/// skips symbols with no database/candles).
#[derive(Debug, Clone, PartialEq)]
pub struct SymbolQuote {
    pub symbol: String,
    pub price: f64,
    pub change_pct: f64,
    pub timestamp: String,
}

impl SymbolQuote {
    /// Map the latest real bar to its quote.
    pub fn from_bar(bar: &Bar) -> Self {
        Self {
            symbol: bar.symbol.clone(),
            price: bar.close,
            change_pct: bar.return_pct(),
            timestamp: bar.timestamp.clone(),
        }
    }
}

/// Base-row fetch size for a limited higher-timeframe query.
///
/// Parity with `CandleRepository.get_candles_timeframe`: `(limit + 1) * ratio`
/// over-fetches one extra bucket so the partial oldest bucket can be dropped
/// by `tail_bars`. `None` (full history) passes through as `None` — it is
/// never bound as SQL `LIMIT NULL` (SQLite `datatype mismatch`).
pub fn fetch_window(limit: Option<usize>, ratio: usize) -> Option<usize> {
    limit.map(|l| (l + 1) * ratio)
}

/// Keep the newest `limit` bars after aggregation.
///
/// Parity with `bars[-limit:]`, including the `limit = 0` quirk (`bars[-0:]`
/// is the whole list in Python) — preserved, pinned by test, not redesigned.
pub fn tail_bars(mut bars: Vec<Bar>, limit: Option<usize>) -> Vec<Bar> {
    match limit {
        None | Some(0) => bars,
        Some(l) if l >= bars.len() => bars,
        Some(l) => bars.split_off(bars.len() - l),
    }
}

/// One higher-timeframe query: how to read base rows, what to keep.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FetchPlan {
    /// Read the stored rows as-is: timeframe at or below the detected base
    /// bar duration (or either side unknown) needs no aggregation.
    pub plain: bool,
    /// Base rows to fetch for aggregation (`None` = whole history).
    pub row_budget: Option<usize>,
    /// Newest aggregated bars to keep (`None` = keep all of them).
    pub keep_last: Option<usize>,
}

/// The query plan behind `CandleRepository.get_candles_timeframe`.
///
/// Owns the three rules that decision is made of: the plain-fetch fallback
/// (`seconds <= base`), the `(limit + 1) * ratio` base-row over-fetch, and
/// the newest-`limit` tail. A `limit` of `0` keeps everything, because
/// Python's `bars[-0:]` does. A base duration that is not positive cannot be
/// divided into, so it also falls back to the plain fetch.
pub fn timeframe_fetch_plan(
    seconds: Option<i64>,
    base: Option<i64>,
    limit: Option<usize>,
) -> FetchPlan {
    let (ratio, limit) = match (seconds, base, limit) {
        (Some(s), Some(b), limit) if b > 0 && s > b => ((s / b) as usize, limit),
        _ => {
            return FetchPlan {
                plain: true,
                row_budget: None,
                keep_last: None,
            }
        }
    };
    FetchPlan {
        plain: false,
        row_budget: fetch_window(limit, ratio),
        keep_last: match limit {
            Some(0) | None => None,
            Some(l) => Some(l),
        },
    }
}

impl fmt::Display for Timeframe {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bar_creation() {
        let bar = Bar::new(
            "AAPL",
            "2024-01-01 09:30:00",
            150.0,
            152.0,
            149.0,
            151.5,
            1_000_000,
        );
        assert_eq!(bar.symbol, "AAPL");
        assert_eq!(bar.open, 150.0);
        assert_eq!(bar.close, 151.5);
    }

    #[test]
    fn bar_calculations() {
        let bar = Bar::new("TEST", "2024-01-01", 100.0, 110.0, 95.0, 105.0, 1000);
        assert_eq!(bar.range(), 15.0);
        assert_eq!(bar.midpoint(), 102.5);
        assert!((bar.typical_price() - 103.33).abs() < 0.01);
        assert!(bar.is_bullish());
        assert_eq!(bar.return_pct(), 5.0);
    }

    #[test]
    fn bar_validation() {
        let valid = Bar::new("AAPL", "2024-01-01", 100.0, 110.0, 95.0, 105.0, 1000);
        assert!(valid.is_valid());

        let invalid_high = Bar::new("AAPL", "2024-01-01", 100.0, 90.0, 95.0, 105.0, 1000);
        assert!(!invalid_high.is_valid());

        let invalid_zero = Bar::new("AAPL", "2024-01-01", 0.0, 110.0, 95.0, 105.0, 1000);
        assert!(!invalid_zero.is_valid());
    }

    #[test]
    fn timeframe_to_seconds() {
        assert_eq!(Timeframe::new("1m").to_seconds(), Some(60));
        assert_eq!(Timeframe::new("1h").to_seconds(), Some(3600));
        assert_eq!(Timeframe::new("1d").to_seconds(), Some(86400));
        assert_eq!(Timeframe::new("invalid").to_seconds(), None);
    }

    // ── Python parity: 03_market/market/timeframe/timeframe.py ──────────
    // Vectors below mirror the live Python module (verified 2026-09-17).

    #[test]
    fn timeframe_seconds_full_ladder() {
        assert_eq!(timeframe_seconds("1m"), Some(60));
        assert_eq!(timeframe_seconds("3m"), Some(180));
        assert_eq!(timeframe_seconds("5m"), Some(300));
        assert_eq!(timeframe_seconds("15m"), Some(900));
        assert_eq!(timeframe_seconds("30m"), Some(1800));
        assert_eq!(timeframe_seconds("45m"), Some(2700));
        assert_eq!(timeframe_seconds("1h"), Some(3600));
        assert_eq!(timeframe_seconds("2h"), Some(7200));
        assert_eq!(timeframe_seconds("4h"), Some(14400));
        assert_eq!(timeframe_seconds("1D"), Some(86400));
        assert_eq!(timeframe_seconds("1W"), Some(604800));
    }

    #[test]
    fn timeframe_seconds_case_insensitive_and_generated() {
        assert_eq!(timeframe_seconds("1d"), Some(86400));
        assert_eq!(timeframe_seconds("1H"), Some(3600));
        assert_eq!(timeframe_seconds("90m"), Some(5400));
        assert_eq!(timeframe_seconds("2D"), Some(172800));
        assert_eq!(timeframe_seconds("2W"), Some(1209600));
        assert_eq!(timeframe_seconds("90s"), Some(90));
        assert_eq!(timeframe_seconds("0m"), None);
        assert_eq!(timeframe_seconds("x"), None);
        assert_eq!(timeframe_seconds("m"), None);
        assert_eq!(timeframe_seconds(""), None);
    }

    #[test]
    fn timeframe_name_and_generate_label() {
        assert_eq!(timeframe_name(900), Some("15m"));
        assert_eq!(timeframe_name(2700), Some("45m"));
        assert_eq!(timeframe_name(123), None);
        assert_eq!(generate_label(5400), "90m");
        assert_eq!(generate_label(172800), "2D");
        assert_eq!(generate_label(1209600), "2W");
        assert_eq!(generate_label(45), "45s");
        assert_eq!(generate_label(950), "950s");
    }

    #[test]
    fn available_timeframes_mirror_python() {
        assert_eq!(
            available_timeframes(900),
            vec!["15m", "30m", "45m", "1h", "2h", "4h", "1D", "1W"]
        );
        assert_eq!(
            available_timeframes(60),
            vec!["1m", "3m", "5m", "15m", "30m", "45m", "1h", "2h", "4h", "1D", "1W"]
        );
        assert_eq!(
            available_timeframes(300),
            vec!["5m", "15m", "30m", "45m", "1h", "2h", "4h", "1D", "1W"]
        );
        // Off-ladder base: no ladder multiple, generated label stands alone.
        assert_eq!(available_timeframes(950), vec!["950s"]);
        assert!(available_timeframes(0).is_empty());
        assert!(available_timeframes(-5).is_empty());
    }

    // ── Python parity: symbol_quote.py + repository window semantics ─────

    #[test]
    fn quote_from_bar_mirrors_get_quotes() {
        let bar = Bar::new(
            "RELIANCE",
            "2026-01-05 09:15:00",
            100.0,
            102.0,
            99.0,
            101.0,
            1000,
        );
        let quote = SymbolQuote::from_bar(&bar);
        assert_eq!(quote.symbol, "RELIANCE");
        assert_eq!(quote.price, 101.0);
        assert_eq!(quote.change_pct, 1.0);
        assert_eq!(quote.timestamp, "2026-01-05 09:15:00");
    }

    #[test]
    fn fetch_window_over_fetches_one_bucket() {
        assert_eq!(fetch_window(None, 2), None);
        assert_eq!(fetch_window(Some(500), 2), Some(1002));
        assert_eq!(fetch_window(Some(0), 4), Some(4));
    }

    #[test]
    fn tail_bars_keeps_newest() {
        let bars: Vec<Bar> = (0..5)
            .map(|i| {
                Bar::new(
                    "T",
                    format!("2026-01-0{} 09:15:00", i + 1),
                    100.0,
                    101.0,
                    99.0,
                    100.0,
                    10,
                )
            })
            .collect();
        assert_eq!(tail_bars(bars.clone(), None).len(), 5);
        // Preserved Python quirk: bars[-0:] is the whole list.
        assert_eq!(tail_bars(bars.clone(), Some(0)).len(), 5);
        assert_eq!(tail_bars(bars.clone(), Some(10)).len(), 5);
        let tail = tail_bars(bars, Some(2));
        assert_eq!(tail.len(), 2);
        assert_eq!(tail[0].timestamp, "2026-01-04 09:15:00");
        assert_eq!(tail[1].timestamp, "2026-01-05 09:15:00");
    }

    #[test]
    fn timeframe_fetch_plan_matches_the_repository_decision() {
        let plain = FetchPlan {
            plain: true,
            row_budget: None,
            keep_last: None,
        };
        // At or below the detected base bar: read the rows as stored.
        assert_eq!(timeframe_fetch_plan(Some(60), Some(60), Some(10)), plain);
        assert_eq!(timeframe_fetch_plan(Some(15), Some(60), Some(10)), plain);
        assert_eq!(timeframe_fetch_plan(None, Some(60), Some(10)), plain);
        assert_eq!(timeframe_fetch_plan(Some(60), None, Some(10)), plain);
        assert_eq!(timeframe_fetch_plan(Some(60), Some(0), Some(10)), plain);
        // Higher timeframe: ratio over-fetch + newest-limit tail.
        assert_eq!(
            timeframe_fetch_plan(Some(900), Some(60), Some(500)),
            FetchPlan {
                plain: false,
                row_budget: Some(7515),
                keep_last: Some(500),
            }
        );
        assert_eq!(
            timeframe_fetch_plan(Some(86_400), Some(900), None),
            FetchPlan {
                plain: false,
                row_budget: None,
                keep_last: None,
            }
        );
        // bars[-0:] keeps everything, so no tail is cut.
        assert_eq!(
            timeframe_fetch_plan(Some(300), Some(60), Some(0)),
            FetchPlan {
                plain: false,
                row_budget: Some(5),
                keep_last: None,
            }
        );
    }
}
