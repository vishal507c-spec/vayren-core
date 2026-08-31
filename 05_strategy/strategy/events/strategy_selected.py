"""StrategySelected — fact: one strategy became the active lab strategy."""

from dataclasses import dataclass

from core.events.event import Event


@dataclass(frozen=True)
class StrategySelected(Event):
    """The active strategy selection changed to `strategy_id`."""

    strategy_id: str
