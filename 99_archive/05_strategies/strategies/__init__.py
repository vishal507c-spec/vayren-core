"""Trading Strategy Domain.

Defines strategy interfaces, manages strategy lifecycle,
and converts signals into order requests.

Public API:
    models: Strategy, PositionSizer, EntryExitRule
    services: StrategyRegistry
    events: OrderRequested, PositionTargeted
"""

from strategies.models.strategy import Strategy
from strategies.models.position_sizer import PositionSizer
from strategies.models.entry_exit import EntryExitRule
from strategies.services.registry import StrategyRegistry
from strategies.events.order_requested import OrderRequested
from strategies.events.position_targeted import PositionTargeted

__all__ = [
    "Strategy",
    "PositionSizer",
    "EntryExitRule",
    "StrategyRegistry",
    "OrderRequested",
    "PositionTargeted",
]
