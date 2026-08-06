from collections import defaultdict
from collections.abc import Callable
from logging import getLogger
from typing import Any

logger = getLogger(__name__)

Handler = Callable[[Any], None]


class EventBus:
    """In-process publish/subscribe event bus."""

    def __init__(self) -> None:
        self._subscribers: dict[type, list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: type, handler: Handler) -> None:
        self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: type, handler: Handler) -> None:
        self._subscribers[event_type].remove(handler)

    def publish(self, event: Any) -> None:
        event_type = type(event)
        handlers = self._subscribers.get(event_type, [])
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                logger.exception("Handler %s failed for %s", handler.__name__, event_type.__name__)

    def clear(self) -> None:
        self._subscribers.clear()
