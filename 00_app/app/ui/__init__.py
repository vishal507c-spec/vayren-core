"""App UI chrome.

Legacy Qt workspaces that production never constructs (Live/Portfolio —
native Slint only) are intentionally NOT re-exported here: their modules
remain importable for their direct-widget tests, but no production import
edge may reference them.
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
