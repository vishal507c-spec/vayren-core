//! Strategy Lab data-completeness facts (section 02 `DATA COMPLETENESS`).
//!
//! The Lab config card shows one honest number: how much of the REQUESTED
//! window the store actually holds for the SELECTED universe. The math is
//! domain math, so it lives in the Rust kernel (`AI_ENTRY.md` §1) and the Python
//! side only counts real rows — it never derives a percentage.
//!
//! Two rules this module exists to enforce:
//!
//! 1. **No invented expectations.** `expected_bars` counts the bars a
//!    Mon–Fri session grid over `[start, end]` can hold at one timeframe. It
//!    never guesses 100% and never invents a minimum.
//! 2. **Unknown stays unknown.** A caller that has no measurement passes
//!    `bars_expected == 0`; [`coverage_facts`] then reports `pct == 0` with
//!    `measured == false`, and the UI hides the strip instead of painting a
//!    fake `0%`.
//!
//! The weekday/holiday rule is [`crate::download`]'s — reused, never
//! reimplemented.

use crate::download::{is_trading_day, SECONDS_PER_DAY};
use std::collections::HashSet;

/// Session length in seconds: 09:15 → 15:30 inclusive-open window, the same
/// bounds the aggregate kernel anchors on (`anchor_seconds("09:15")`).
pub const SESSION_SECS: i64 = (15 * 3600 + 30 * 60) - (9 * 3600 + 15 * 60);

/// Raw measured counts for one Lab coverage probe. Every field is a count the
/// caller actually read — none of them is a display value.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct CoverageInput {
    /// Symbols in the current selection.
    pub symbols_total: u32,
    /// Selected symbols that hold AT LEAST ONE bar inside the window. A stock
    /// listed after the window opened is normal, so this is a data-presence
    /// question, never a "not enough history" judgement.
    pub symbols_covering: u32,
    /// Bars actually present in the window across the probed symbols.
    pub bars_present: u64,
    /// Bars the window can hold at the selected timeframe
    /// ([`expected_bars`]); `0` means "not measured".
    pub bars_expected: u64,
    /// Interior holes between the first and last present bar of a symbol.
    pub gaps: u32,
    /// The probe covered a bounded sample, not the whole selection.
    pub sampled: bool,
    /// How many symbols the probe actually read (`sampled` is derived from
    /// this being less than `symbols_total`, never guessed).
    pub symbols_probed: u32,
}

/// The presentation-ready facts. `pct` is an integer percentage because the UI
/// has no float formatting (Slint 1.17) — the decimal the mock showed is
/// dropped, not rounded into a lie.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct CoverageFacts {
    /// Integer percent 0–100; `0` whenever `bars_expected == 0`.
    pub pct: u8,
    pub present: u64,
    pub expected: u64,
    pub gaps: u32,
    /// Selected symbols with NO data at all inside the window — the real
    /// "these stocks cannot be tested" count.
    pub short_history: u32,
    pub sampled: bool,
    /// False when the caller measured nothing; the strip must stay hidden.
    pub measured: bool,
    /// How many symbols the probe actually read.
    pub symbols_probed: u32,
}

impl CoverageFacts {
    /// How many symbols the probe reported on, for an honest caption. The
    /// caller is the one that knows whether this was the whole selection or a
    /// bounded slice, so the number comes from the measurement.
    pub fn symbols_covered_of(&self) -> u32 {
        self.symbols_probed
    }
}

/// Derive the facts from measured counts.
///
/// `pct` is `present / expected` capped at 100. `expected == 0` yields
/// `pct == 0` and `measured == false` — never 100, never a fabricated 0% bar.
pub fn coverage_facts(input: &CoverageInput) -> CoverageFacts {
    let measured = input.bars_expected > 0;
    let pct = if measured {
        let ratio = input.bars_present.saturating_mul(100) / input.bars_expected;
        ratio.min(100) as u8
    } else {
        0
    };
    CoverageFacts {
        pct,
        present: input.bars_present,
        expected: input.bars_expected,
        gaps: input.gaps,
        short_history: input.symbols_total.saturating_sub(input.symbols_covering),
        sampled: input.sampled,
        symbols_probed: input.symbols_probed,
        measured,
    }
}

/// Bars a Mon–Fri session grid can hold over the inclusive day range
/// `[start_days, end_days]` at `tf_secs`.
///
/// Intraday: trading days × `SESSION_SECS / tf_secs` (floor — a partial bucket
/// is not a bar). Daily and above: one bar per trading day, so a weekly
/// timeframe yields `days / 7`. `end_days < start_days` or a non-positive
/// timeframe yields 0 (an empty window expects nothing).
pub fn expected_bars(start_days: i64, end_days: i64, tf_secs: i64) -> u64 {
    if tf_secs <= 0 || end_days < start_days {
        return 0;
    }
    let holidays: HashSet<String> = HashSet::new();
    let trading = trading_days_between(start_days, end_days, &holidays);
    if tf_secs >= SECONDS_PER_DAY {
        let days_per_bar = (tf_secs / SECONDS_PER_DAY).max(1) as u64;
        return trading / days_per_bar;
    }
    let bars_per_day = SESSION_SECS / tf_secs;
    if bars_per_day <= 0 {
        return 0;
    }
    trading.saturating_mul(bars_per_day as u64)
}

/// Mon–Fri days in the inclusive range, minus any holiday in `holidays`.
fn trading_days_between(start_days: i64, end_days: i64, holidays: &HashSet<String>) -> u64 {
    let mut count = 0u64;
    let mut day = start_days;
    while day <= end_days {
        if is_trading_day(day, holidays) {
            count += 1;
        }
        day += 1;
    }
    count
}

#[cfg(test)]
mod tests {
    use super::*;

    fn full() -> CoverageInput {
        CoverageInput {
            symbols_total: 3,
            symbols_covering: 3,
            bars_present: 940,
            bars_expected: 1000,
            gaps: 1,
            sampled: false,
            symbols_probed: 3,
        }
    }

    #[test]
    fn zero_expected_is_unmeasured_not_a_zero_percent_bar() {
        let facts = coverage_facts(&CoverageInput::default());
        assert!(!facts.measured);
        assert_eq!(facts.pct, 0);
        assert_eq!(facts.expected, 0);
    }

    #[test]
    fn full_coverage_is_one_hundred() {
        let facts = coverage_facts(&full());
        assert!(facts.measured);
        assert_eq!(facts.pct, 94);
        assert_eq!(facts.present, 940);
        assert_eq!(facts.gaps, 1);
    }

    #[test]
    fn present_above_expected_is_capped_at_one_hundred() {
        let facts = coverage_facts(&CoverageInput {
            bars_present: 5000,
            ..full()
        });
        assert_eq!(facts.pct, 100);
    }

    #[test]
    fn short_history_is_the_symbols_that_do_not_reach_the_window() {
        let facts = coverage_facts(&CoverageInput {
            symbols_total: 10,
            symbols_covering: 7,
            ..full()
        });
        assert_eq!(facts.short_history, 3);
    }

    #[test]
    fn short_history_never_underflows_when_all_symbols_cover() {
        let facts = coverage_facts(&CoverageInput {
            symbols_total: 2,
            symbols_covering: 5,
            ..full()
        });
        assert_eq!(facts.short_history, 0);
    }

    #[test]
    fn sampled_propagates() {
        assert!(
            coverage_facts(&CoverageInput {
                sampled: true,
                ..full()
            })
            .sampled
        );
        assert!(!coverage_facts(&full()).sampled);
    }

    #[test]
    fn expected_bars_counts_weekday_sessions_only() {
        // 2026-01-05 (Mon) → 2026-01-09 (Fri) is five sessions, two weekends
        // inside the calendar range would otherwise inflate the count.
        let start = 20_458; // 2026-01-05 (Mon)
        let end = start + 4; // 2026-01-09
        let fifteen_min = 900;
        assert_eq!(
            expected_bars(start, end, fifteen_min),
            5 * (SESSION_SECS / 900) as u64
        );
        // A Sat→Sun range holds nothing.
        assert_eq!(expected_bars(start + 5, start + 6, fifteen_min), 0);
    }

    #[test]
    fn daily_timeframe_is_one_bar_per_session() {
        let start = 20_458; // 2026-01-05 (Mon) Mon
                            // 2026-01-05 (Mon) → 2026-01-16 (Fri): ten sessions, not twelve days.
        assert_eq!(expected_bars(start, start + 11, SECONDS_PER_DAY), 10);
    }

    #[test]
    fn expected_bars_rejects_an_inverted_or_invalid_window() {
        assert_eq!(expected_bars(20_458, 20_451, 900), 0);
        assert_eq!(expected_bars(20_458, 20_461, 0), 0);
        assert_eq!(expected_bars(20_458, 20_461, -900), 0);
    }
}
