//! PRESENTATION_MODEL compute twin — timeframe inference + model assembly.
//!
//! Rust equivalents of `04_chart/chart/models/timeframe.py`
//! (`infer_timeframe`) and the pure assembly half of
//! `04_chart/chart/engine/chart_engine.py` (ascending check, stable sort,
//! timeframe, default exchange). Mirrors the pattern of the earlier backend
//! slices (`download.rs`, `backtest_engine.rs`, `risk_engine.rs`,
//! `execution_engine.rs`): the Python layer stays the production authority
//! (no Python file is touched, no behavior changes); this module lets future
//! pure-Rust chart consumers reuse the exact semantics in-process.
//!
//! Parity scope: the SQLite ISO shapes actually produced by the market layer
//! (`YYYY-MM-DD HH:MM:SS`, optional `T` separator, date-only strings, ASCII).
//! Two deliberate narrowings, both outside production shapes:
//! - Sub-second fractions are truncated to whole seconds (production stamps
//!   and all parity vectors are whole seconds; Python keeps float fractions).
//! - Exotic `datetime.fromisoformat` spellings (week dates, ordinals, basic
//!   `YYYYMMDD`, single-digit fields beyond the accepted set) fall back to
//!   "1D" here instead of parsing.
//!
//! No FFI in this slice (same rationale as the risk/execution engines):
//! timestamps cross as strings and the consumers (`ChartEngine`, legacy widgets)
//! stay Python-bound, so a C string-vector ABI would add failure modes for
//! zero measured need. The kernels are exercised via in-process unit tests
//! against live-Python parity vectors.

use crate::market::Bar;

/// How many leading bars feed gap inference (mirrors `_INFERENCE_SAMPLE`).
pub const INFERENCE_SAMPLE: usize = 2048;

/// Exchange stamped on every assembled model (mirrors `_DEFAULT_EXCHANGE`).
pub const DEFAULT_EXCHANGE: &str = "NSE";

/// Gap ladder in seconds (mirrors `_SECONDS_LADDER`, in order).
pub const SECONDS_LADDER: &[i64] = &[
    60, 180, 300, 900, 1800, 2700, 3600, 7200, 10800, 14400, 21600, 43200, 86400, 604800,
];

/// Human label for a ladder granularity (mirrors `_FORMATTERS` + fallback).
pub fn format_seconds(seconds: i64) -> String {
    match seconds {
        60 => "1m".to_string(),
        180 => "3m".to_string(),
        300 => "5m".to_string(),
        900 => "15m".to_string(),
        1800 => "30m".to_string(),
        2700 => "45m".to_string(),
        3600 => "1h".to_string(),
        7200 => "2h".to_string(),
        10800 => "3h".to_string(),
        14400 => "4h".to_string(),
        21600 => "6h".to_string(),
        43200 => "12h".to_string(),
        86400 => "1D".to_string(),
        604800 => "1W".to_string(),
        // Python fallback is floor division (`//`); `div_euclid` matches it.
        _ => format!("{}D", seconds.div_euclid(86400)),
    }
}

/// Days since the Unix epoch for a civil date (Hinnant's algorithm).
fn days_from_civil(year: i64, month: i64, day: i64) -> i64 {
    let y = if month <= 2 { year - 1 } else { year };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let doy = (153 * (if month > 2 { month - 3 } else { month + 9 }) + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146097 + doe - 719468
}

fn is_leap_year(year: i64) -> bool {
    year % 4 == 0 && (year % 100 != 0 || year % 400 == 0)
}

fn days_in_month(year: i64, month: i64) -> i64 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 => {
            if is_leap_year(year) {
                29
            } else {
                28
            }
        }
        _ => 0,
    }
}

fn parse_int(text: &str, min_len: usize, max_len: usize) -> Option<i64> {
    if text.len() < min_len || text.len() > max_len || text.is_empty() {
        return None;
    }
    if !text.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    text.parse::<i64>().ok()
}

fn parse_date(text: &str) -> Option<(i64, i64, i64)> {
    if text.len() == 10 && text.as_bytes()[4] == b'-' && text.as_bytes()[7] == b'-' {
        let year = parse_int(&text[0..4], 4, 4)?;
        let month = parse_int(&text[5..7], 1, 2)?;
        let day = parse_int(&text[8..10], 1, 2)?;
        Some((year, month, day))
    } else if text.len() == 8 && text.bytes().all(|b| b.is_ascii_digit()) {
        let year = parse_int(&text[0..4], 4, 4)?;
        let month = parse_int(&text[4..6], 1, 2)?;
        let day = parse_int(&text[6..8], 1, 2)?;
        Some((year, month, day))
    } else {
        None
    }
}

fn parse_offset(text: &str) -> Option<i64> {
    if text == "Z" || text == "z" {
        return Some(0);
    }
    let (sign, body) = match text.strip_prefix('+') {
        Some(rest) => (1i64, rest),
        None => (-1i64, text.strip_prefix('-')?),
    };
    if let Some(colon) = body.find(':') {
        let hours = parse_int(&body[..colon], 1, 2)?;
        let mins = parse_int(&body[colon + 1..], 2, 2)?;
        if hours > 23 || mins > 59 {
            return None;
        }
        Some(sign * (hours * 3600 + mins * 60))
    } else if body.len() <= 2 {
        let hours = parse_int(body, 1, 2)?;
        if hours > 23 {
            return None;
        }
        Some(sign * hours * 3600)
    } else if body.len() == 4 {
        let hours = parse_int(&body[..2], 2, 2)?;
        let mins = parse_int(&body[2..], 2, 2)?;
        if hours > 23 || mins > 59 {
            return None;
        }
        Some(sign * (hours * 3600 + mins * 60))
    } else {
        None
    }
}

/// Parse an ISO timestamp to whole UTC epoch seconds.
///
/// Accepts the production shapes (`YYYY-MM-DD[ HH:MM[:SS[.ffffff]][offset]]`
/// with a space or `T` separator, plus date-only). Garbage returns `None`,
/// which the caller maps to the `"1D"` fallback — mirroring `_parse_ts`.
pub fn parse_timestamp(timestamp: &str) -> Option<i64> {
    let s = timestamp.trim();
    if s.is_empty() {
        return None;
    }
    let split = s.find(|c| c == 'T' || c == ' ');
    let (date_part, mut rest) = match split {
        Some(idx) => (&s[..idx], &s[idx + 1..]),
        None => (s, ""),
    };
    let (year, month, day) = parse_date(date_part)?;
    if !(1..=12).contains(&month) || day < 1 || day > days_in_month(year, month) {
        return None;
    }
    if rest.is_empty() {
        return Some(days_from_civil(year, month, day) * 86400);
    }
    let mut offset_seconds = 0i64;
    if rest.ends_with('Z') || rest.ends_with('z') {
        offset_seconds = 0;
        rest = &rest[..rest.len() - 1];
    } else if let Some(idx) = rest.find(|c| c == '+' || c == '-') {
        // The time core itself never contains `+`/`-`, so this starts the zone.
        offset_seconds = parse_offset(&rest[idx..])?;
        rest = &rest[..idx];
    }
    let (core, _fraction) = match rest.find('.') {
        Some(idx) => (&rest[..idx], Some(&rest[idx + 1..])),
        None => (rest, None),
    };
    if let Some(frac) = _fraction {
        if frac.is_empty() || !frac.bytes().all(|b| b.is_ascii_digit()) {
            return None;
        }
    }
    let parts: Vec<&str> = core.split(':').collect();
    let (hour, minute, second) = match parts.len() {
        2 => (parse_int(parts[0], 1, 2)?, parse_int(parts[1], 1, 2)?, 0i64),
        3 => (
            parse_int(parts[0], 1, 2)?,
            parse_int(parts[1], 1, 2)?,
            parse_int(parts[2], 1, 2)?,
        ),
        _ => return None,
    };
    if hour > 23 || minute > 59 || second > 59 {
        return None;
    }
    Some(
        days_from_civil(year, month, day) * 86400 + hour * 3600 + minute * 60 + second
            - offset_seconds,
    )
}

/// Positive consecutive gaps over the first `INFERENCE_SAMPLE + 1` stamps.
///
/// Unparseable stamps are dropped first and non-positive diffs are skipped,
/// mirroring `_gaps`/`_parse_times`.
pub fn gaps_seconds(timestamps: &[&str]) -> Vec<i64> {
    let cap = INFERENCE_SAMPLE + 1;
    let end = timestamps.len().min(cap);
    let times: Vec<i64> = timestamps[..end]
        .iter()
        .filter_map(|ts| parse_timestamp(ts))
        .collect();
    if times.len() < 2 {
        return Vec::new();
    }
    times
        .windows(2)
        .filter_map(|w| if w[1] > w[0] { Some(w[1] - w[0]) } else { None })
        .collect()
}

fn mode_gap(gaps: &[i64]) -> Option<i64> {
    if gaps.is_empty() {
        return None;
    }
    let mut counts: std::collections::HashMap<i64, usize> = std::collections::HashMap::new();
    for &gap in gaps {
        *counts.entry(gap).or_insert(0) += 1;
    }
    let max_count = counts.values().copied().max()?;
    // Tie-break takes the smallest gap (mirrors `min(candidates)`).
    counts
        .into_iter()
        .filter(|(_, count)| *count == max_count)
        .map(|(gap, _)| gap)
        .min()
}

fn nearest_ladder(gap: i64) -> i64 {
    let mut best = SECONDS_LADDER[0];
    let mut best_dist = (gap - best).abs();
    for &step in &SECONDS_LADDER[1..] {
        let dist = (gap - step).abs();
        // Strict `<` keeps the earlier (smaller) step on ties, mirroring
        // Python `min(ladder, key=...)` over the ascending ladder.
        if dist < best_dist {
            best = step;
            best_dist = dist;
        }
    }
    best
}

/// Timeframe label inferred from the dominant bar-to-bar gap.
///
/// Falls back to `"1D"` with fewer than two stamps or no usable gaps,
/// mirroring `infer_timeframe` (including the 4h median-trap fix: the mode,
/// not the median, drives the label).
pub fn infer_timeframe(timestamps: &[&str]) -> String {
    if timestamps.len() < 2 {
        return "1D".to_string();
    }
    let gaps = gaps_seconds(timestamps);
    match mode_gap(&gaps) {
        Some(gap) => format_seconds(nearest_ladder(gap)),
        None => "1D".to_string(),
    }
}

/// True when stamps are already ascending (lexicographic `<=`, no copy).
///
/// Mirrors `_ascending`: the comparison is on the raw strings, not parsed
/// times, so ISO spellings sort exactly like the Python check.
pub fn is_ascending(timestamps: &[&str]) -> bool {
    timestamps.windows(2).all(|w| w[0] <= w[1])
}

/// Immutable chart-ready candle data (mirrors `ChartModel`).
#[derive(Debug, Clone, PartialEq)]
pub struct ChartModel {
    pub symbol: String,
    pub bars: Vec<Bar>,
    pub timeframe: String,
    pub exchange: String,
}

/// Assemble a model from loaded candles.
///
/// Mirrors `ChartEngine.on_data_loaded` minus the bus: empty input returns
/// `None` (Python logs and publishes nothing); otherwise the bars are
/// stable-sorted ascending by timestamp string, the timeframe is inferred,
/// and the exchange defaults to NSE.
pub fn build_model(symbol: &str, mut bars: Vec<Bar>) -> Option<ChartModel> {
    if bars.is_empty() {
        return None;
    }
    // `sort_by` is stable, matching Python's stable `sorted`.
    bars.sort_by(|a, b| a.timestamp.cmp(&b.timestamp));
    let stamps: Vec<&str> = bars.iter().map(|bar| bar.timestamp.as_str()).collect();
    let timeframe = infer_timeframe(&stamps);
    Some(ChartModel {
        symbol: symbol.to_string(),
        bars,
        timeframe,
        exchange: DEFAULT_EXCHANGE.to_string(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

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

    fn stamp(epoch: i64) -> String {
        let days = epoch.div_euclid(86400);
        let secs = epoch.rem_euclid(86400);
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

    fn base_epoch() -> i64 {
        days_from_civil(2026, 4, 8) * 86400 + 9 * 3600 + 15 * 60
    }

    fn series(count: usize, step_secs: i64) -> Vec<String> {
        let base = base_epoch();
        (0..count as i64)
            .map(|i| stamp(base + i * step_secs))
            .collect()
    }

    fn refs(values: &[String]) -> Vec<&str> {
        values.iter().map(|s| s.as_str()).collect()
    }

    fn bar(symbol: &str, ts: &str) -> Bar {
        Bar::new(symbol, ts, 100.0, 105.0, 95.0, 102.0, 1000)
    }

    #[test]
    fn empty_returns_default() {
        assert_eq!(infer_timeframe(&[]), "1D");
    }

    #[test]
    fn single_bar_returns_default() {
        let stamps = ["2026-04-08 09:15:00"];
        assert_eq!(infer_timeframe(&stamps), "1D");
    }

    #[test]
    fn garbage_timestamps_return_default() {
        let stamps = ["garbage", "also garbage"];
        assert_eq!(infer_timeframe(&stamps), "1D");
    }

    #[test]
    fn daily_bars_return_1d() {
        let stamps = series(50, 86400);
        assert_eq!(infer_timeframe(&refs(&stamps)), "1D");
    }

    #[test]
    fn intraday_15m() {
        let stamps = series(100, 900);
        assert_eq!(infer_timeframe(&refs(&stamps)), "15m");
    }

    #[test]
    fn intraday_30m() {
        let stamps = series(100, 1800);
        assert_eq!(infer_timeframe(&refs(&stamps)), "30m");
    }

    #[test]
    fn intraday_45m() {
        let stamps = series(100, 2700);
        assert_eq!(infer_timeframe(&refs(&stamps)), "45m");
    }

    #[test]
    fn intraday_1h() {
        let stamps = series(100, 3600);
        assert_eq!(infer_timeframe(&refs(&stamps)), "1h");
    }

    #[test]
    fn intraday_2h() {
        let stamps = series(100, 7200);
        assert_eq!(infer_timeframe(&refs(&stamps)), "2h");
    }

    #[test]
    fn weekly_bars_return_1w() {
        let base = days_from_civil(2026, 1, 6) * 86400;
        let stamps: Vec<String> = (0..10i64).map(|i| stamp(base + i * 604800)).collect();
        assert_eq!(infer_timeframe(&refs(&stamps)), "1W");
    }

    #[test]
    fn sparse_4h_survives_overnight_gaps() {
        // 4h has 2 bars/day; the median would drift to "1D" on even counts.
        // Live-Python parity: 21 bars and 40 bars both infer "4h".
        let day0 = days_from_civil(2026, 4, 8);
        let mut stamps = Vec::new();
        for day in 0..20i64 {
            let base = (day0 + day) * 86400;
            stamps.push(stamp(base + 9 * 3600 + 15 * 60));
            stamps.push(stamp(base + 13 * 3600 + 15 * 60));
        }
        let first21: Vec<&str> = stamps.iter().take(21).map(|s| s.as_str()).collect();
        assert_eq!(infer_timeframe(&first21), "4h");
        assert_eq!(infer_timeframe(&refs(&stamps)), "4h");
    }

    #[test]
    fn space_and_t_separators_agree() {
        let space = ["2026-04-08 09:15:00", "2026-04-08 09:30:00"];
        let tee = ["2026-04-08T09:15:00", "2026-04-08T09:30:00"];
        assert_eq!(infer_timeframe(&space), "15m");
        assert_eq!(infer_timeframe(&tee), "15m");
    }

    #[test]
    fn ascending_uses_string_order() {
        assert!(is_ascending(&["2026-01-01", "2026-01-02"]));
        assert!(!is_ascending(&["2026-01-03", "2026-01-01"]));
        assert!(is_ascending(&["2026-01-01", "2026-01-01"]));
        assert!(is_ascending(&[]));
        assert!(is_ascending(&["2026-01-01"]));
    }

    #[test]
    fn build_model_sorts_and_labels() {
        // Mirrors `test_on_data_loaded_publishes_sorted_chart_ready`.
        let bars = vec![
            bar("SPY", "2026-01-03"),
            bar("SPY", "2026-01-01"),
            bar("SPY", "2026-01-02"),
        ];
        let model = build_model("SPY", bars).expect("non-empty builds a model");
        let stamps: Vec<&str> = model.bars.iter().map(|b| b.timestamp.as_str()).collect();
        assert_eq!(stamps, vec!["2026-01-01", "2026-01-02", "2026-01-03"]);
        assert_eq!(model.symbol, "SPY");
        assert_eq!(model.timeframe, "1D");
        assert_eq!(model.exchange, "NSE");
    }

    #[test]
    fn build_model_empty_returns_none() {
        assert_eq!(build_model("SPY", Vec::new()), None);
    }

    #[test]
    fn build_model_sort_is_stable() {
        // Python `sorted` is stable: equal timestamps keep input order.
        let mut first = bar("SPY", "2026-01-01 09:15:00");
        first.close = 101.0;
        let mut second = bar("SPY", "2026-01-01 09:15:00");
        second.close = 102.0;
        let model = build_model("SPY", vec![first, second]).expect("builds");
        assert_eq!(model.bars[0].close, 101.0);
        assert_eq!(model.bars[1].close, 102.0);
    }

    #[test]
    fn fallback_format_uses_floor_days() {
        assert_eq!(format_seconds(172800), "2D");
        assert_eq!(format_seconds(10800), "3h");
        assert_eq!(format_seconds(21600), "6h");
        assert_eq!(format_seconds(43200), "12h");
    }
}
