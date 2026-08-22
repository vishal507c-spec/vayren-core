"""ResearchEngine — generic, read-only, version-aware."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .analysis import ResearchAnalysis, analyze_dataset
from .dataset import ResearchDataset
from .discovery import Discovery, create_discovery
from .experiment import Experiment, create_experiment
from .robustness import RobustnessResult, run_robustness
from .validation import ValidationResult, validate_experiment


@dataclass
class ResearchReport:
    """Generic research report — references exact executions."""

    strategy_id: str
    version_id: str
    execution_ids: tuple[str, ...]
    baseline: ResearchAnalysis
    experiments: list[Experiment]
    robustness: list[RobustnessResult]
    validation: ValidationResult | None
    discoveries: list[Discovery]
    conclusion: str


class ResearchEngine:
    """Generic Research Engine — operates on immutable histories."""

    def create_dataset(
        self,
        strategy_id: str,
        version_id: str,
        histories: list[Any],
        parameters: dict[str, Any] | None = None,
    ) -> ResearchDataset:
        # histories are ExecutionHistory objects
        return ResearchDataset.from_histories(strategy_id, version_id, histories, parameters)

    def analyze(self, dataset: ResearchDataset) -> ResearchAnalysis:
        return analyze_dataset(dataset)

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

    def generate_report(
        self,
        strategy_id: str,
        version_id: str,
        execution_ids: list[str],
        dataset: ResearchDataset,
        experiments: list[Experiment],
        robustness: list[RobustnessResult],
        validation: ValidationResult | None,
        discoveries: list[Discovery],
    ) -> ResearchReport:
        analysis = self.analyze(dataset)
        # Simple conclusion based on validation
        if validation and validation.status == "PASS":
            conclusion = "Evidence supports edge — robust across tested dimensions."
        elif validation and validation.status == "WARNING":
            conclusion = f"Marginal evidence — {validation.summary}"
        elif validation:
            conclusion = f"Insufficient — {validation.summary}"
        else:
            conclusion = "No validation — descriptive analysis only."
        return ResearchReport(
            strategy_id=strategy_id,
            version_id=version_id,
            execution_ids=tuple(execution_ids),
            baseline=analysis,
            experiments=experiments,
            robustness=robustness,
            validation=validation,
            discoveries=discoveries,
            conclusion=conclusion,
        )
