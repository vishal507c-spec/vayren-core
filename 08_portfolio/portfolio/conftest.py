import pytest

from portfolio.models.portfolio import Portfolio


@pytest.fixture
def sample_portfolio() -> Portfolio:
    return Portfolio(name="test", total_capital=100000.0, cash=100000.0)
