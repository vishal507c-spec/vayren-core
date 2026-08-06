"""AI/ML Research Domain.

Feature engineering, model training, experiment tracking, reinforcement learning,
and AI research agents.

Public API:
    features: FeatureRegistry, FeaturePipeline
    models: ModelRegistry
    agents: StrategyDiscoveryAgent, AnomalyDetector
    evaluation: BacktestEvaluator
"""

from research.features.pipelines import FeaturePipeline
from research.models.registry import ModelRegistry
from research.agents.strategy_discovery import StrategyDiscoveryAgent
from research.evaluation.backtest import BacktestEvaluator

__all__ = [
    "FeaturePipeline",
    "ModelRegistry",
    "StrategyDiscoveryAgent",
    "BacktestEvaluator",
]
