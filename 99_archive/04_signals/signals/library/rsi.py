from typing import Sequence

from signals.models.indicator import Indicator


def compute_rsi(prices: Sequence[float], period: int = 14) -> float:
    """Compute RSI for a price series."""
    if len(prices) < period + 1:
        return 50.0
    gains, losses = 0.0, 0.0
    for i in range(-period, 0):
        change = prices[i] - prices[i - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


rsi_indicator = Indicator(
    name="rsi_14",
    description="Relative Strength Index (14-period)",
    parameters={"period": 14},
    _compute_fn=lambda data, period=14: compute_rsi(data, period),
)
