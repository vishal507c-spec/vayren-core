import pytest
from execution.models.order import Order


@pytest.fixture
def sample_order() -> Order:
    return Order(symbol="SPY", side="buy", quantity=100, order_type="market", id="test-001")
