//! Lab-form request validation twin — the `validate_backtest_form` kernel.
//!
//! Rust port of `06_backtest/backtest/validation.py`. The strategy-lab form
//! object stays strategy-owned; only resolved facts cross, and the exact
//! message strings are pinned in [`MESSAGES`] (same order as the Python
//! error list). `BacktestRunner` consumes the verdict before fetching
//! anything (fail fast on an invalid request). The C ABI
//! (`vy_backtest_validate_form`) reads the bitmask here.

/// Error messages in check order (mirrors the Python error list exactly).
pub const MESSAGES: [&str; 7] = [
    "No symbol loaded — select a symbol first.",
    "No strategy selected or strategy is disabled.",
    "No timeframe selected.",
    "Start date is after end date.",
    "Initial capital must be positive.",
    "Max position size must be positive.",
    "Max position size cannot exceed initial capital.",
];

/// All checks failed (fail-closed value for panics/wrong shapes).
pub const ALL_BITS: u32 = 0x7F;

/// Validate a backtest request; returns the failure bitmask (`0` = valid).
/// Branch-for-branch mirror of `validate_backtest_form`, including the
/// nested max-position checks. The capital check uses `!(x > 0.0)` so NaN
/// fails exactly like Python's `<= 0` comparison.
pub fn validate_form(
    symbol_ok: bool,
    strategy_ok: bool,
    timeframe_ok: bool,
    dates_ordered: bool,
    initial_capital: f64,
    max_position_size: Option<f64>,
) -> u32 {
    let mut bits = 0u32;
    if !symbol_ok {
        bits |= 1 << 0;
    }
    if !strategy_ok {
        bits |= 1 << 1;
    }
    if !timeframe_ok {
        bits |= 1 << 2;
    }
    if !dates_ordered {
        bits |= 1 << 3;
    }
    if !(initial_capital > 0.0) {
        bits |= 1 << 4;
    }
    if let Some(size) = max_position_size {
        if size <= 0.0 {
            bits |= 1 << 5;
        } else if size > initial_capital {
            bits |= 1 << 6;
        }
    }
    bits
}

/// Messages for a failure bitmask, in check order.
pub fn messages(mask: u32) -> Vec<String> {
    MESSAGES
        .iter()
        .enumerate()
        .filter(|(index, _)| mask & (1 << index) != 0)
        .map(|(_, message)| message.to_string())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn valid_form_is_zero() {
        assert_eq!(validate_form(true, true, true, true, 1000.0, None), 0);
        assert!(messages(0).is_empty());
    }

    #[test]
    fn all_failed_sets_every_bit() {
        // Bits 5 and 6 are mutually exclusive (mirrors the elif), so the
        // natural maximum is bits 0-5; ALL_BITS stays the unreachable
        // fail-closed sentinel for panics/wrong shapes.
        assert_eq!(ALL_BITS, 0x7F);
        let mask = validate_form(false, false, false, false, -5.0, Some(-5.0));
        assert_eq!(mask, 0x3F);
        assert_eq!(
            messages(mask),
            vec![
                "No symbol loaded — select a symbol first.".to_string(),
                "No strategy selected or strategy is disabled.".to_string(),
                "No timeframe selected.".to_string(),
                "Start date is after end date.".to_string(),
                "Initial capital must be positive.".to_string(),
                "Max position size must be positive.".to_string(),
            ]
        );
    }

    #[test]
    fn oversized_position_sets_only_its_bit() {
        let mask = validate_form(true, true, true, true, 1000.0, Some(5000.0));
        assert_eq!(mask, 1 << 6);
        assert_eq!(
            messages(mask),
            vec!["Max position size cannot exceed initial capital.".to_string()]
        );
    }

    #[test]
    fn nan_capital_fails_like_python() {
        assert_eq!(
            validate_form(true, true, true, true, f64::NAN, None),
            1 << 4
        );
    }
}
