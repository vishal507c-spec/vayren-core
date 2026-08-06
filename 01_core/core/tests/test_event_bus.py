"""EventBus tests."""

from dataclasses import dataclass

import pytest

from core.event_bus.event_bus import EventBus
from core.events.event import Event


@dataclass(frozen=True)
class SomethingHappened(Event):
    value: int


def test_publish_dispatches_to_subscribed_handlers() -> None:
    bus = EventBus()
    received: list[SomethingHappened] = []
    bus.subscribe(SomethingHappened, received.append)
    bus.publish(SomethingHappened(value=42))
    assert received == [SomethingHappened(value=42)]


def test_publish_ignores_unrelated_event_types() -> None:
    bus = EventBus()
    received: list[SomethingHappened] = []
    bus.subscribe(SomethingHappened, received.append)
    bus.publish("not an event")
    assert received == []


def test_unsubscribe_removes_handler() -> None:
    bus = EventBus()
    received: list[SomethingHappened] = []
    bus.subscribe(SomethingHappened, received.append)
    bus.unsubscribe(SomethingHappened, received.append)
    bus.publish(SomethingHappened(value=1))
    assert received == []


def test_handler_exception_is_isolated() -> None:
    bus = EventBus()
    received: list[SomethingHappened] = []

    def failing(_event: SomethingHappened) -> None:
        raise RuntimeError("boom")

    bus.subscribe(SomethingHappened, failing)
    bus.subscribe(SomethingHappened, received.append)
    bus.publish(SomethingHappened(value=7))
    assert received == [SomethingHappened(value=7)]


def test_clear_removes_all_handlers() -> None:
    bus = EventBus()
    received: list[SomethingHappened] = []
    bus.subscribe(SomethingHappened, received.append)
    bus.clear()
    bus.publish(SomethingHappened(value=1))
    assert received == []


def test_publish_raises_for_unknown_subscriber_on_unsubscribe() -> None:
    bus = EventBus()
    with pytest.raises(ValueError):
        bus.unsubscribe(SomethingHappened, lambda _event: None)
