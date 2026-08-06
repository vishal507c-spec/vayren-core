from typing import Sequence


def ema(data: Sequence[float], period: int) -> float:
    """Compute exponential moving average for the last `period` values."""
    if len(data) < period:
        return data[-1] if data else 0.0
    k = 2.0 / (period + 1)
    result = sum(data[-period:]) / period
    return result  # simplified single-value EMA


def compute_macd(prices: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> float:
    """Compute MACD histogram value."""
    if len(prices) < slow + signal:
        return 0.0
    macd_line = ema(prices[-fast:], fast) - ema(prices[-slow:], slow)
    return macd_line
