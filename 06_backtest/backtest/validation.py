"""Request validation for the strategy-lab form."""

from strategy import BacktestForm


def validate_backtest_form(
    form: BacktestForm,
    symbol: str | None,
    enabled_ids: set[str],
) -> list[str]:
    """Validate a backtest request; returns the error messages (empty when valid)."""
    errors: list[str] = []
    if symbol is None or not symbol.strip():
        errors.append("No symbol loaded — select a symbol first.")
    if not form.strategy_id or form.strategy_id not in enabled_ids:
        errors.append("No strategy selected or strategy is disabled.")
    if not form.timeframe:
        errors.append("No timeframe selected.")
    if form.start_date > form.end_date:
        errors.append("Start date is after end date.")
    if form.initial_capital <= 0:
        errors.append("Initial capital must be positive.")
    if form.max_position_size is not None:
        if form.max_position_size <= 0:
            errors.append("Max position size must be positive.")
        elif form.max_position_size > form.initial_capital:
            errors.append("Max position size cannot exceed initial capital.")
    return errors
