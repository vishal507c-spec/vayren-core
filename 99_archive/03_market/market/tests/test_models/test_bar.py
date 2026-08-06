from market.models.bar import Bar


class TestBar:
    def test_creates_bar(self) -> None:
        bar = Bar(symbol="SPY", open=450.0, high=455.0, low=448.0, close=453.0, volume=1000000, timestamp="2025-01-15T09:30:00Z")
        assert bar.symbol == "SPY"
        assert bar.open == 450.0
        assert bar.close == 453.0

    def test_range(self, sample_bar: Bar) -> None:
        assert sample_bar.range == 7.0

    def test_typical_price(self, sample_bar: Bar) -> None:
        expected = (455.0 + 448.0 + 453.0) / 3.0
        assert sample_bar.typical_price == expected

    def test_midpoint(self, sample_bar: Bar) -> None:
        expected = (455.0 + 448.0) / 2.0
        assert sample_bar.midpoint == expected

    def test_is_bullish(self, sample_bar: Bar) -> None:
        assert sample_bar.is_bullish is True

    def test_return_pct(self, sample_bar: Bar) -> None:
        expected = ((453.0 - 450.0) / 450.0) * 100.0
        assert sample_bar.return_pct == expected

    def test_frozen_immutable(self) -> None:
        bar = Bar(symbol="SPY", open=450.0, high=455.0, low=448.0, close=453.0, volume=1000000, timestamp="2025-01-15T09:30:00Z")
        import pytest
        with pytest.raises(Exception):
            bar.open = 460.0  # type: ignore[misc]
