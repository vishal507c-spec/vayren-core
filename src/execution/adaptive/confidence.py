"""Surprise monitor + adaptive confidence — diagnostics and preferences only.

Prediction-error signals (expected vs observed spread/latency/slippage/
volatility) feed diagnostics and execution PREFERENCES. The confidence
policy can shrink size, prefer limits, or halt — it can NEVER raise limits,
release the kill switch, or approve anything risk denied. That separation
is structural: this module outputs advisory values consumed downstream of
risk, and risk re-validates the final order.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum


@dataclass
class ExpectationWindow:
    """Rolling mean observer for one metric (spread, latency, slippage...)."""

    maxlen: int = 200

    def __post_init__(self) -> None:
        self._samples: deque[float] = deque(maxlen=self.maxlen)

    def observe(self, value: float) -> None:
        self._samples.append(float(value))

    @property
    def expected(self) -> float | None:
        if not self._samples:
            return None
        return sum(self._samples) / len(self._samples)

    def surprise(self, observed: float) -> float | None:
        """Relative deviation |observed-expected|/|expected|; None when blind.

        A zero baseline with non-zero observed is maximal surprise
        (`inf`, never `None`): the old `exp == 0 -> None` mistook the most
        surprising case for "no baseline". `0` vs `0` is no surprise.
        """
        exp = self.expected
        if exp is None:
            return None
        seen = float(observed)
        if exp == 0:
            return 0.0 if seen == 0.0 else float("inf")
        return abs(seen - exp) / abs(exp)


class SurpriseMonitor:
    """Tracks expectation windows per metric; reports named surprises."""

    def __init__(self, threshold: float = 1.0) -> None:
        self._threshold = float(threshold)
        self._windows: dict[str, ExpectationWindow] = {}

    def observe(self, metric: str, value: float) -> float | None:
        """Record and return surprise ratio (None while warming up)."""
        window = self._windows.setdefault(metric, ExpectationWindow())
        surprise = window.surprise(value)
        window.observe(value)
        return surprise

    def is_surprising(self, metric: str, value: float) -> bool:
        """Pure read-only check: never records (use `observe` to record)."""
        window = self._windows.get(metric)
        if window is None:
            return False
        surprise = window.surprise(value)
        return surprise is not None and surprise >= self._threshold


class ExecutionPosture(Enum):
    NORMAL = "NORMAL"
    REDUCED = "REDUCED"
    HALT = "HALT"


@dataclass
class ConfidenceInput:
    spread_surprise: float | None = None
    latency_surprise: float | None = None
    slippage_surprise: float | None = None
    data_stale: bool = False
    broker_unstable: bool = False
    halt_threshold: float = 3.0
    reduce_threshold: float = 1.0

    def __post_init__(self) -> None:
        if self.halt_threshold < self.reduce_threshold:
            raise ValueError(
                f"halt_threshold ({self.halt_threshold}) must be >= "
                f"reduce_threshold ({self.reduce_threshold})"
            )


@dataclass
class ConfidencePolicy:
    """Maps surprise signals to an execution posture (advisory only)."""

    def evaluate(self, signals: ConfidenceInput) -> ExecutionPosture:
        if signals.halt_threshold < signals.reduce_threshold:
            raise ValueError(
                f"halt_threshold ({signals.halt_threshold}) must be >= "
                f"reduce_threshold ({signals.reduce_threshold})"
            )
        if signals.data_stale or signals.broker_unstable:
            return ExecutionPosture.HALT
        surprises = [
            s
            for s in (
                signals.spread_surprise,
                signals.latency_surprise,
                signals.slippage_surprise,
            )
            if s is not None
        ]
        if not surprises:
            # Blind (no baseline yet): shrink, never trade at full size.
            return ExecutionPosture.REDUCED
        worst = max(surprises)
        if worst >= signals.halt_threshold:
            return ExecutionPosture.HALT
        if worst >= signals.reduce_threshold:
            return ExecutionPosture.REDUCED
        return ExecutionPosture.NORMAL

    def preferences(self, posture: ExecutionPosture) -> dict[str, object]:
        """Planner preferences for a posture. Shrink-only, limit-preferring."""
        if posture == ExecutionPosture.HALT:
            return {"halt": True, "prefer_limit": True, "size_multiplier": 0.0}
        if posture == ExecutionPosture.REDUCED:
            return {"halt": False, "prefer_limit": True, "size_multiplier": 0.5}
        return {"halt": False, "prefer_limit": False, "size_multiplier": 1.0}
