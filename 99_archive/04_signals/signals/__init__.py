"""Signal Generation Domain.

Transforms market data into actionable trading signals.
Publishes SignalUpdated and RegimeDetected events.

Public API:
    models: Signal, Indicator, Regime
    services: SignalRegistry
    events: SignalUpdated, RegimeDetected
"""

from signals.models.signal import Signal
from signals.models.indicator import Indicator
from signals.models.regime import Regime
from signals.services.registry import SignalRegistry
from signals.events.signal_updated import SignalUpdated
from signals.events.regime_detected import RegimeDetected

__all__ = [
    "Signal",
    "Indicator",
    "Regime",
    "SignalRegistry",
    "SignalUpdated",
    "RegimeDetected",
]
