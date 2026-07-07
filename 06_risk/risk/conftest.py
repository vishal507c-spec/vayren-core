import pytest

from risk.models.limit import RiskLimit


@pytest.fixture
def sample_limit() -> RiskLimit:
    return RiskLimit(name="max_position", max_value=1000, current_value=0)
