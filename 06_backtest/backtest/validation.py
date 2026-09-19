"""Request validation for the strategy-lab form (Rust-canonical).

The decision lives in Rust (`rust/vayren-core`, `backtest_validation`
module); this module renders the exact messages in order. No independent
Python checks here (constitution §1, migration §12).
"""

from strategy import BacktestForm

from backtest.native_validation import MESSAGES, validate_mask_for


def validate_backtest_form(
    form: BacktestForm,
    symbol: str | None,
    enabled_ids: set[str],
) -> list[str]:
    """Validate a backtest request; returns the error messages (empty when valid)."""
    mask = validate_mask_for(form, symbol, enabled_ids)
    return [message for bit, message in enumerate(MESSAGES) if mask & (1 << bit)]
