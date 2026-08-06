"""Core domain — EventBus, events, logging, registry.

The foundation of the platform. Depends on nothing.
"""

from core.event_bus.event_bus import EventBus
from core.events.app_started import AppStarted
from core.events.event import Event
from core.logger import configure_logging, get_logger
from core.registry.registry import Registry

__all__ = [
    "EventBus",
    "Event",
    "AppStarted",
    "Registry",
    "configure_logging",
    "get_logger",
]
