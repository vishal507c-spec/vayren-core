"""Runtime Platform Domain.

Provides runtime abstraction (backtest/paper/live), event bus, clock, configuration,
and dependency injection. Depends only on lib/.

Public API:
    models: Engine, Mode, Clock
    services: EventBus, ConfigLoader, LifecycleManager
"""

from platform.models.engine import Engine
from platform.models.mode import Mode
from platform.models.clock import Clock
from platform.services.event_bus import EventBus
from platform.services.config_loader import ConfigLoader
from platform.services.lifecycle import LifecycleManager

__all__ = [
    "Engine",
    "Mode",
    "Clock",
    "EventBus",
    "ConfigLoader",
    "LifecycleManager",
]
