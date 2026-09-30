"""Core Event marker — frozen-dataclass pins."""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

CHAPTER = Path(__file__).resolve().parents[2]
if str(CHAPTER) not in sys.path:
    sys.path.insert(0, str(CHAPTER))

import pytest  # noqa: E402

from core.events.event import Event  # noqa: E402


def test_event_is_frozen() -> None:
    params = getattr(Event, "__dataclass_params__")  # noqa: B009 -- direct access trips pyright (unknown attr on slots base); getattr keeps the frozen pin checkable.
    assert params.frozen is True
    # The fieldless slots base raises TypeError on mutation (CPython recreates
    # slots classes, so the generated frozen __setattr__ misses its type
    # check and falls into super()); fielded subclasses raise FrozenInstanceError
    # (pinned below). Either way the instance is immutable.
    with pytest.raises((TypeError, dataclasses.FrozenInstanceError)):
        Event().anything = 1  # type: ignore[attr-defined]


def test_event_has_slots() -> None:
    assert Event.__slots__ == ()
    assert not hasattr(Event(), "__dict__")


def test_event_subclass_stays_frozen_and_comparable() -> None:
    @dataclasses.dataclass(frozen=True, slots=True)
    class _Ping(Event):
        label: str = "ping"

    first, second = _Ping("ping"), _Ping("ping")
    assert first == second
    assert hash(first) == hash(second)
    with pytest.raises(dataclasses.FrozenInstanceError):
        second.label = "pong"  # type: ignore[misc]
