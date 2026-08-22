"""Research Engine — generic, read-only analysis of Strategy Versions and Execution Histories."""

from .advanced_validation import (
    AdvancedValidationResult,
    CPCVConfig,
    CPCVResult,
    DSRResult,
    EvidenceGrade,
    MultipleTestingResult,
    PBOResult,
    ValidationPolicy,
    correct_multiple_testing,
    grade_evidence,
    run_cpcv,
)
from .analysis import ResearchAnalysis, analyze_dataset
from .dataset import ResearchDataset
from .discovery import Discovery, create_discovery
from .engine import ResearchEngine
from .experiment import Experiment, Hypothesis
from .intelligence import CandidateHypothesis, IntelligenceRun, ResearchIntelligence
from .robustness import RobustnessResult, run_parameter_sensitivity, run_robustness
from .validation import ValidationResult, validate_experiment

__all__ = [
    "ResearchDataset",
    "ResearchAnalysis",
    "analyze_dataset",
    "Experiment",
    "Hypothesis",
    "CandidateHypothesis",
    "IntelligenceRun",
    "ResearchIntelligence",
    "RobustnessResult",
    "run_parameter_sensitivity",
    "run_robustness",
    "ValidationResult",
    "validate_experiment",
    "Discovery",
    "create_discovery",
    "ResearchEngine",
    "AdvancedValidationResult",
    "CPCVConfig",
    "CPCVResult",
    "DSRResult",
    "EvidenceGrade",
    "MultipleTestingResult",
    "PBOResult",
    "ValidationPolicy",
    "correct_multiple_testing",
    "grade_evidence",
    "run_cpcv",
]
