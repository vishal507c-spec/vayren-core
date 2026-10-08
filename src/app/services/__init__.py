"""App services — composition-root helpers (no domain logic)."""

from app.services.control_plane_bridge import ControlPlaneBridge
from app.services.trading_dashboard import (
    LiveWorkspaceView,
    MarketWorkspaceView,
    PortfolioWorkspaceView,
    ResearchWorkspaceView,
    RiskPanelWidget,
    StrategyLabWorkspaceView,
    SystemWorkspaceView,
    TradingDashboardWidget,
)

__all__ = [
    "ControlPlaneBridge",
    "LiveWorkspaceView",
    "MarketWorkspaceView",
    "PortfolioWorkspaceView",
    "ResearchWorkspaceView",
    "RiskPanelWidget",
    "StrategyLabWorkspaceView",
    "SystemWorkspaceView",
    "TradingDashboardWidget",
]
