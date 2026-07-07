"""Portfolio Management Domain.

Manages portfolio construction, capital allocation, rebalancing, and P&L tracking.

Public API:
    models: Portfolio, Allocation, PnL
    services: PortfolioConstructor, PortfolioTracker
    events: AllocationUpdated, RebalanceTriggered
"""

from portfolio.models.portfolio import Portfolio
from portfolio.models.allocation import Allocation
from portfolio.models.pnl import PnL
from portfolio.services.constructor import PortfolioConstructor
from portfolio.services.tracker import PortfolioTracker
from portfolio.events.allocation_updated import AllocationUpdated
from portfolio.events.rebalance_triggered import RebalanceTriggered

__all__ = [
    "Portfolio", "Allocation", "PnL",
    "PortfolioConstructor", "PortfolioTracker",
    "AllocationUpdated", "RebalanceTriggered",
]
