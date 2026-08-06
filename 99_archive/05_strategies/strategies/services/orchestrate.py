from logging import getLogger
from typing import Any

from strategies.models.strategy import Strategy
from strategies.events.order_requested import OrderRequested
from strategies.events.position_targeted import PositionTargeted

logger = getLogger(__name__)


class StrategyOrchestrator:
    """Orchestrates strategy execution lifecycle."""

    def __init__(self) -> None:
        self._strategies: dict[str, Strategy] = {}

    def add_strategy(self, strategy: Strategy) -> None:
        self._strategies[strategy.name] = strategy
        strategy.on_start()
        logger.info("Strategy started: %s", strategy.name)

    def remove_strategy(self, name: str) -> None:
        if name in self._strategies:
            self._strategies[name].on_stop()
            del self._strategies[name]
            logger.info("Strategy stopped: %s", name)

    def evaluate_all(self, context: Any) -> list[OrderRequested]:
        orders: list[OrderRequested] = []
        for name, strategy in self._strategies.items():
            try:
                if strategy.should_enter(context) or strategy.should_exit(context):
                    strategy_orders = strategy.generate_orders(context)
                    orders.extend(strategy_orders)
            except Exception:
                logger.exception("Error evaluating strategy %s", name)
        return orders
