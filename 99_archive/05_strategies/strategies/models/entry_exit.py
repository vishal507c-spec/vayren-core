from abc import ABC, abstractmethod
from typing import Any


class EntryExitRule(ABC):
    """Defines entry and exit conditions for a strategy."""

    @abstractmethod
    def should_enter(self, context: Any) -> bool:
        ...

    @abstractmethod
    def should_exit(self, context: Any) -> bool:
        ...


class ThresholdEntryExit(EntryExitRule):
    """Entry/exit based on signal threshold values."""

    def __init__(self, entry_threshold: float = 70.0, exit_threshold: float = 30.0) -> None:
        self._entry = entry_threshold
        self._exit = exit_threshold

    def should_enter(self, context: Any) -> bool:
        signal = getattr(context, "signal_value", 0)
        return signal > self._entry

    def should_exit(self, context: Any) -> bool:
        signal = getattr(context, "signal_value", 0)
        return signal < self._exit
