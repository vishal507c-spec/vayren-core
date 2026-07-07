from typing import Sequence


def compute_volatility(prices: Sequence[float], period: int = 20) -> float:
    """Compute rolling volatility (standard deviation of returns)."""
    if len(prices) < period + 1:
        return 0.0
    returns = [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(-period, 0)]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    return variance ** 0.5
