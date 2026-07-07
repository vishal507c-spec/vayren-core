import pytest

from market.models.bar import Bar


@pytest.fixture
def sample_bar() -> Bar:
    return Bar(
        symbol="SPY",
        open=450.0,
        high=455.0,
        low=448.0,
        close=453.0,
        volume=1000000,
        timestamp="2025-01-15T09:30:00Z",
    )


@pytest.fixture
def sample_bars() -> list[Bar]:
    return [
        Bar(symbol="SPY", open=450.0, high=455.0, low=448.0, close=453.0, volume=1000000, timestamp="2025-01-15T09:30:00Z"),
        Bar(symbol="SPY", open=453.0, high=458.0, low=452.0, close=457.0, volume=1200000, timestamp="2025-01-15T09:31:00Z"),
        Bar(symbol="SPY", open=457.0, high=460.0, low=455.0, close=459.0, volume=900000, timestamp="2025-01-15T09:32:00Z"),
    ]
