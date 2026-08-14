"""Event graph — who emits and who consumes every event.

Component → emits → Event → consumed_by → Component.
Built from manifest declarations; the EventBus itself is untouched.
"""

from core.contracts.manifest import ComponentManifest


class EventGraph:
    """Event producers and consumers derived from component manifests."""

    def __init__(self, manifests: tuple[ComponentManifest, ...]) -> None:
        producers: dict[str, set[str]] = {}
        consumers: dict[str, set[str]] = {}
        for manifest in manifests:
            name = manifest.identity.name
            for event in manifest.events_produced:
                producers.setdefault(event, set()).add(name)
            for event in manifest.events_consumed:
                consumers.setdefault(event, set()).add(name)
        self._producers = {event: tuple(sorted(names)) for event, names in producers.items()}
        self._consumers = {event: tuple(sorted(names)) for event, names in consumers.items()}
        self._events: tuple[str, ...] = tuple(sorted(set(producers) | set(consumers)))

    def events(self) -> tuple[str, ...]:
        """Every event mentioned in any manifest, sorted."""
        return self._events

    def producers(self, event: str) -> tuple[str, ...]:
        """Components emitting this event (sorted)."""
        return self._producers.get(event, ())

    def consumers(self, event: str) -> tuple[str, ...]:
        """Components consuming this event (sorted)."""
        return self._consumers.get(event, ())

    def events_of(self, component: str, produced: bool) -> tuple[str, ...]:
        """Events a component emits (produced=True) or consumes (produced=False)."""
        source = self._producers if produced else self._consumers
        return tuple(sorted(event for event, names in source.items() if component in names))

    def unproduced(self) -> tuple[str, ...]:
        """Consumed events with no registered producer (sorted)."""
        return tuple(sorted(set(self._consumers) - set(self._producers)))

    def unconsumed(self) -> tuple[str, ...]:
        """Produced events with no registered consumer (sorted)."""
        return tuple(sorted(set(self._producers) - set(self._consumers)))
