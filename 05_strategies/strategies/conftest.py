import pytest

from strategies.library.momentum import MomentumStrategy
from strategies.library.mean_reversion import MeanReversionStrategy


@pytest.fixture
def momentum_strategy() -> MomentumStrategy:
    return MomentumStrategy(rsi_threshold=70.0, shares=100)
