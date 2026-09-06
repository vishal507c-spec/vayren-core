"""Future ML extension points — protocols with deterministic defaults.

ML models may be plugged in later WITHOUT rewriting execution
infrastructure. Today every slot has a deterministic implementation:
regime → StatisticalRegimeDetector, prediction/execution/anomaly →
disabled-by-default pass-throughs that never influence orders.
"""

from __future__ import annotations

from typing import Any, Protocol


class PredictionModel(Protocol):
    """Future price/signal predictor. No implementation ships today."""

    def predict(self, features: dict[str, Any]) -> dict[str, Any]: ...


class RegimeModel(Protocol):
    """Regime source behind the detector interface."""

    def classify(self, closes: tuple[float, ...], volumes: tuple[float, ...]) -> str: ...


class ExecutionModel(Protocol):
    """Future smart-order-routing advisor. Advisory only, like confidence."""

    def advise(self, intent: Any, market: dict[str, Any]) -> dict[str, Any]: ...


class AnomalyModel(Protocol):
    """Future anomaly scorer feeding diagnostics (never risk)."""

    def score(self, observation: dict[str, Any]) -> float: ...


class DisabledPredictionModel:
    """Default: no prediction. Returns empty advisory output."""

    def predict(self, features: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        return {}


class DisabledExecutionModel:
    """Default: no routing advice. Planner uses its own deterministic plan."""

    def advise(self, intent: Any, market: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        return {}


class DisabledAnomalyModel:
    """Default: never anomalous."""

    def score(self, observation: dict[str, Any]) -> float:  # noqa: ARG002
        return 0.0
