//! Timeframe aggregation kernel — the Rust-owned authority (constitution §1:
//! Market/Data processing / Numerical calculations / Performance-critical).
//!
//! Python parses timestamps into `(day_ordinal, seconds_of_day)` and formats
//! the resulting bar timestamps (domain/IO concerns). The hot numeric loop —
//! bucket grouping and single-pass OHLCV accumulation — lives here.
//!
//! Semantics are bit-for-bit the original: open = first row's open, high =
//! running max, low = running min, close = last row's close, volume = running
//! sum (accumulated as f64, truncated to int by the caller), buckets keyed by
//! `(day_ordinal, index)`.

/// One aggregated bucket. `#[repr(C)]` so Python can read it via ctypes.
#[repr(C)]
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct AggBucket {
    pub day: i32,
    pub index: i32,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
}

const DAY_SECONDS: i64 = 86_400;

/// Bucket key for one bar, matching `_bucket_key` exactly.
///
/// * intraday (`tf < DAY`): session-anchored index when at/after session
///   start, otherwise wall-clock index (mirrors the Python branch).
/// * daily (`tf == DAY`): `(day, 0)`.
/// * weekly (`tf > DAY`): Monday-anchored `(monday_day, 0)`.
pub fn bucket_key(day: i32, sec_of_day: i32, tf: i64, session_start: i64) -> (i32, i32) {
    if tf < DAY_SECONDS {
        let sec = sec_of_day as i64;
        let index = if sec >= session_start {
            (sec - session_start) / tf
        } else {
            sec / tf
        };
        (day, index as i32)
    } else if tf == DAY_SECONDS {
        (day, 0)
    } else {
        // weekday: Python date.toordinal() 1 == 0001-01-01 == Monday (weekday 0)
        let weekday = (day - 1).rem_euclid(7);
        (day - weekday, 0)
    }
}

/// Aggregate ascending base rows into buckets. Inputs are parallel arrays of
/// equal length `n`. Output is one `AggBucket` per distinct key, in
/// first-seen order (the caller sorts by timestamp afterwards).
pub fn aggregate(
    days: &[i32],
    secs: &[i32],
    opens: &[f64],
    highs: &[f64],
    lows: &[f64],
    closes: &[f64],
    volumes: &[f64],
    tf: i64,
    session_start: i64,
) -> Vec<AggBucket> {
    let n = days.len();
    let mut order: Vec<(i32, i32)> = Vec::new();
    let mut acc: Vec<AggBucket> = Vec::new();
    // HashMap for O(1) bucket lookup (a linear scan degrades to O(n*buckets)
    // on multi-thousand-bucket inputs, e.g. 60k rows over 2400 days).
    let mut index_of: std::collections::HashMap<(i32, i32), usize> =
        std::collections::HashMap::with_capacity(n.min(8192));
    for i in 0..n {
        let key = bucket_key(days[i], secs[i], tf, session_start);
        if let Some(&pos) = index_of.get(&key) {
            let b = &mut acc[pos];
            if highs[i] > b.high {
                b.high = highs[i];
            }
            if lows[i] < b.low {
                b.low = lows[i];
            }
            b.close = closes[i];
            b.volume += volumes[i];
        } else {
            index_of.insert(key, order.len());
            order.push(key);
            acc.push(AggBucket {
                day: key.0,
                index: key.1,
                open: opens[i],
                high: highs[i],
                low: lows[i],
                close: closes[i],
                volume: volumes[i],
            });
        }
    }
    acc
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn intraday_session_anchored() {
        // 15m bars from a 09:15 session start.
        let start = 9 * 3600 + 15 * 60;
        assert_eq!(bucket_key(100, start, 900, start as i64), (100, 0));
        assert_eq!(bucket_key(100, start + 900, 900, start as i64), (100, 1));
        assert_eq!(bucket_key(100, start + 3600, 900, start as i64), (100, 4));
    }

    #[test]
    fn intraday_before_session_uses_wall_clock() {
        let start = 9 * 3600 + 15 * 60;
        assert_eq!(bucket_key(100, 9 * 3600, 900, start as i64), (100, 36));
    }

    #[test]
    fn daily_bucket_is_day_zero() {
        assert_eq!(bucket_key(100, 40000, 86_400, 0), (100, 0));
    }

    #[test]
    fn weekly_anchors_to_monday() {
        // ordinal 1 = Monday. ordinal 7 = Sunday -> Monday ordinal 1.
        assert_eq!(bucket_key(7, 0, 604_800, 0).0, 1);
        // ordinal 8 = Monday -> stays 8.
        assert_eq!(bucket_key(8, 0, 604_800, 0).0, 8);
    }

    #[test]
    fn accumulates_ohlcv_in_single_pass() {
        let days = [100, 100, 100];
        let secs = [33300, 33600, 34100]; // three sub-900s bars, same 15m bucket
        let opens = [10.0, 11.0, 12.0];
        let highs = [10.5, 12.0, 11.0];
        let lows = [9.5, 10.0, 10.5];
        let closes = [10.2, 11.5, 11.8];
        let vols = [100.0, 200.0, 300.0];
        let out = aggregate(
            &days, &secs, &opens, &highs, &lows, &closes, &vols, 900, 33300,
        );
        assert_eq!(out.len(), 1);
        let b = &out[0];
        assert_eq!(b.open, 10.0); // first
        assert_eq!(b.high, 12.0); // max
        assert_eq!(b.low, 9.5); // min
        assert_eq!(b.close, 11.8); // last
        assert_eq!(b.volume, 600.0); // sum
    }

    #[test]
    fn empty_input_is_empty_output() {
        let out = aggregate(&[], &[], &[], &[], &[], &[], &[], 900, 0);
        assert!(out.is_empty());
    }
}
