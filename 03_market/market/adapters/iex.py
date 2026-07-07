from typing import Any

import httpx


class IEXAdapter:
    """IEX Cloud market data adapter."""

    BASE_URL = "https://cloud.iexapis.com"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client = httpx.Client(base_url=self.BASE_URL, timeout=30)

    def get_quote(self, symbol: str) -> dict[str, Any]:
        response = self._client.get(f"/stable/stock/{symbol}/quote", params={"token": self._api_key})
        response.raise_for_status()
        return response.json()

    def get_bars(self, symbol: str, range_param: str = "1m") -> list[dict[str, Any]]:
        response = self._client.get(f"/stable/stock/{symbol}/chart/{range_param}", params={"token": self._api_key})
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self._client.close()
