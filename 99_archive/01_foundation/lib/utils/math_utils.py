from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence


def round_to_tick(value: float, tick_size: float = 0.01) -> float:
    """Round a price to the nearest valid tick."""
    if tick_size <= 0:
        return value
    return round(value / tick_size) * tick_size


def weight(values: Sequence[float], weights: Sequence[float]) -> float:
    """Compute weighted sum of values."""
    if len(values) != len(weights):
        msg = f"Length mismatch: {len(values)} values vs {len(weights)} weights"
        raise ValueError(msg)
    total_weight = sum(weights)
    if total_weight == 0:
        return 0.0
    return sum(v * w for v, w in zip(values, weights, strict=False)) / total_weight


def weighted_average(values: Sequence[float], weights: Sequence[float]) -> float:
    """Alias for weight()."""
    return weight(values, weights)
