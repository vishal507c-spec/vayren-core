from market.services.ingestion import MarketDataIngestion


class TestMarketDataIngestion:
    def setup_method(self) -> None:
        self.ingestion = MarketDataIngestion()

    def test_normalize_bar(self) -> None:
        raw = {"symbol": "SPY", "open": 450.0, "high": 455.0, "low": 448.0, "close": 453.0, "volume": 1000000, "timestamp": "2025-01-15T09:30:00Z"}
        bar = self.ingestion.normalize_bar(raw, source="polygon")
        assert bar.symbol == "SPY"
        assert bar.open == 450.0
        assert bar.source == "polygon"

    def test_normalize_bar_short_keys(self) -> None:
        raw = {"S": "SPY", "o": 450.0, "h": 455.0, "l": 448.0, "c": 453.0, "v": 1000000, "t": "2025-01-15T09:30:00Z"}
        bar = self.ingestion.normalize_bar(raw)
        assert bar.symbol == "SPY"
        assert bar.volume == 1000000
