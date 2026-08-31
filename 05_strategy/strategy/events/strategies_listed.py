"""StrategiesListed — fact: the registry contents changed or was listed."""

from dataclasses import dataclass

from core.events.event import Event

from strategy.models.definition import StrategyDefinition


@dataclass(frozen=True)
class StrategiesListed(Event):
    """The current strategy registry contents (published after any change)."""

    strategies: tuple[StrategyDefinition, ...]
