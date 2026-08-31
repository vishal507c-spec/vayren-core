"""Strategy platform — registry, definitions, runtime and lab UI.

Depends on: core, market.
"""

from strategy.events.lab_reset import LabReset
from strategy.events.paper_trade_requested import PaperTradeRequested
from strategy.events.strategies_listed import StrategiesListed
from strategy.events.strategy_selected import StrategySelected
from strategy.manifest import strategy_manifest
from strategy.models.definition import StrategyDefinition
from strategy.models.parameters import (
    ParameterError,
    ParameterSpec,
    StrategyParameters,
)
from strategy.models.signal import Signal, SignalKind
from strategy.models.state import StrategyState
from strategy.registry import StrategyRegistry, StrategyRegistryError
from strategy.runtime import BarView, StrategyLogic, StrategyRuntime

__all__ = [
    "StrategyRegistry",
    "StrategyRegistryError",
    "StrategyDefinition",
    "StrategyParameters",
    "ParameterSpec",
    "ParameterError",
    "Signal",
    "SignalKind",
    "StrategyState",
    "StrategyRuntime",
    "StrategyLogic",
    "BarView",
    "strategy_manifest",
    "StrategiesListed",
    "StrategySelected",
    "PaperTradeRequested",
    "LabReset",
]
