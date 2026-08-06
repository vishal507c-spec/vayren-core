from typing import Sequence

from signals.models.signal import Signal


class SignalNormalizer:
    """Normalizes and combines multiple signals into composite values."""

    def normalize(self, value: float, min_val: float, max_val: float) -> float:
        if max_val == min_val:
            return 0.5
        return (value - min_val) / (max_val - min_val)

    def combine(self, signals: Sequence[Signal], weights: Sequence[float] | None = None) -> float:
        if not signals:
            return 0.0
        if weights is None:
            weights = [1.0] * len(signals)
        if len(signals) != len(weights):
            msg = f"Signal/weight count mismatch: {len(signals)} vs {len(weights)}"
            raise ValueError(msg)
        total_weight = sum(weights)
        if total_weight == 0:
            return 0.0
        return sum(s.value * w for s, w in zip(signals, weights, strict=False)) / total_weight
