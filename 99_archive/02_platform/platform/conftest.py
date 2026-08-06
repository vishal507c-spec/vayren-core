import pytest

from platform.models.engine import Engine
from platform.models.mode import Mode
from platform.services.event_bus import EventBus


@pytest.fixture
def engine() -> Engine:
    return Engine(mode=Mode.BACKTEST)


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()
