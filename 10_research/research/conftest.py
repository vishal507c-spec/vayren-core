import pytest


@pytest.fixture
def sample_prices() -> list[float]:
    return [450.0, 452.0, 448.0, 455.0, 458.0, 456.0, 460.0, 462.0, 458.0, 461.0, 465.0, 463.0, 467.0, 470.0, 468.0]


@pytest.fixture
def sample_equity_curve() -> list[float]:
    return [100000.0, 100500.0, 101200.0, 100800.0, 102000.0, 103500.0, 103000.0]
