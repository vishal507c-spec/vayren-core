from typing import Any, Optional

import httpx

from market.services.ingestion import MarketDataIngestion


class PolygonAdapter:
    """Polygon.io market data adapter."""

    BASE_URL = "https://api.polygon.io"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client = httpx.Client(base_url=self.BASE_URL, timeout=30)
        self._ingestion = MarketDataIngestion()

    def get_bars(self, symbol: str, timespan: str = "day", multiplier: int = 1, from_date: str = "", to_date: str = "") -> list[dict[str, Any]]:
        params = {
            "apikey": self._api_key,
            "timespan": timespan,
            "multiplier": multiplier,
            "from": from_date,
            "to": to_date,
            "adjusted": "true",
            "limit": 5000,
        }
        response = self._client.get(f"/v2/aggs/ticker/{symbol}/range/{multiplier}/{timespan}/{from_date}/{to_date}", params=params)
        response.raise_for_status()
        data = response.json()
        return data.get("results", [])

    def close(self) -> None:
        self._client.close()
