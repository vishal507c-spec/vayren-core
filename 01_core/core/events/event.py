"""Base class for every event flowing through the EventBus."""


class Event:
    """Marker base class for all events.

    Events are immutable facts (or, for commands, immutable requests)
    published through the EventBus.
    """
