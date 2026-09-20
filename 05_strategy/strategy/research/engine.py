"""ResearchEngine — generic, read-only, version-aware."""

from __future__ import annotations

from typing import Any

from .analysis import ResearchAnalysis
from .dataset import ResearchDataset
from .discovery import Discovery, create_discovery
from .experiment import Experiment, create_experiment
from .robustness import RobustnessResult, run_robustness
from .validation import ValidationResult, validate_experiment


class ResearchEngine:
    """Generic Research Engine — operates on immutable histories."""

    def run_experiment(
        self,
        strategy_id: str,
        version_id: str,
        execution_ids: list[str],
        hypothesis: str,
        configuration: dict[str, Any] | None = None,
    ) -> Experiment:
        exp = create_experiment(strategy_id, version_id, execution_ids, hypothesis, configuration)
        # For Phase 6, experiment result is initially empty; analysis will fill it
        return exp

    def run_robustness(
        self, dataset: ResearchDataset, experiment: Experiment | None = None
    ) -> list[RobustnessResult]:
        return run_robustness(dataset, experiment)

    def validate(
        self, analysis: ResearchAnalysis, robustness: list[RobustnessResult]
    ) -> ValidationResult:
        return validate_experiment(analysis, robustness)

    def create_discovery(
        self,
        strategy_id: str,
        version_id: str,
        experiment_id: str,
        evidence: dict[str, Any],
        observed_effect: str,
        confidence: str = "low",
    ) -> Discovery:
        return create_discovery(
            strategy_id, version_id, experiment_id, evidence, observed_effect, confidence
        )
