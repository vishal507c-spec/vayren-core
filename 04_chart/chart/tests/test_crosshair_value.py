"""CrosshairValue tests — construction and field immutability."""

import dataclasses

import pytest
from market.models.bar import Bar

from chart.models.crosshair_value import CrosshairValue


def _bar() -> Bar:
    return Bar(
        symbol="SPY",
        open=100.0,
        high=105.0,
        low=95.0,
        close=102.0,
        volume=1000,
        timestamp="2026-04-08 10:15:00",
    )


def test_fields_stored() -> None:
    value = CrosshairValue(
        bar_index=42,
        price=102.5,
        timestamp=_bar().timestamp,
        open=100.0,
        high=105.0,
        low=95.0,
        close=102.0,
    )
    assert value.bar_index == 42
    assert value.price == 102.5
    assert value.timestamp == "2026-04-08 10:15:00"
    assert value.open == 100.0
    assert value.high == 105.0
    assert value.low == 95.0
    assert value.close == 102.0


def test_is_frozen() -> None:
    value = CrosshairValue(
        bar_index=0,
        price=102.0,
        timestamp=_bar().timestamp,
        open=100.0,
        high=105.0,
        low=95.0,
        close=102.0,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        value.price = 999.0  # type: ignore[misc]
