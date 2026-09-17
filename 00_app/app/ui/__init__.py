"""App UI chrome.

Removed legacy Qt workspaces (Live/Portfolio/Brokers — native Slint only):
these were never constructed in production and have been deleted along with
their direct-widget tests. Only the panels below remain as Qt chrome.
"""

from app.ui.event_log_panel import EventLogPanel
from app.ui.market_status_panel import MarketStatusPanel
from app.ui.research_workspace import ResearchWorkspace
from app.ui.system_health_panel import SystemHealthPanel
from app.ui.top_nav_bar import TopNavBar
from app.ui.ui_kit import Badge, EmptyState, GateRow, KVBlock, Section

__all__ = [
    "TopNavBar",
    "EventLogPanel",
    "SystemHealthPanel",
    "MarketStatusPanel",
    "ResearchWorkspace",
    "Section",
    "Badge",
    "KVBlock",
    "GateRow",
    "EmptyState",
]
