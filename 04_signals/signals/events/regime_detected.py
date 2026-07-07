from dataclasses import dataclass
from signals.models.regime import Regime


@dataclass(frozen=True)
class RegimeDetected:
    """Emitted when a market regime is detected."""

    regime: Regime
