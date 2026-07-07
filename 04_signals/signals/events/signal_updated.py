from dataclasses import dataclass
from signals.models.signal import Signal


@dataclass(frozen=True)
class SignalUpdated:
    """Emitted when a signal value changes."""

    signal: Signal
    previous_value: float | None = None
