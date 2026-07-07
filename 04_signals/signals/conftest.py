import pytest

from signals.models.signal import Signal


@pytest.fixture
def sample_signal() -> Signal:
    return Signal(name="rsi_14", value=65.0, timestamp="2025-01-15T09:30:00Z", symbol="SPY")


@pytest.fixture
def sample_prices() -> list[float]:
    return [450.0, 452.0, 448.0, 455.0, 458.0, 456.0, 460.0, 462.0, 458.0, 461.0, 465.0, 463.0, 467.0, 470.0, 468.0, 472.0, 475.0, 473.0, 478.0, 480.0]
