from typing import Any

from execution.models.order import Order


class AlpacaBroker:
    """Alpaca Markets broker adapter (paper + live)."""

    BASE_URL_PAPER = "https://paper-api.alpaca.markets"
    BASE_URL_LIVE = "https://api.alpaca.markets"

    def __init__(self, api_key: str, secret_key: str, paper: bool = True) -> None:
        import httpx
        self._api_key = api_key
        self._secret_key = secret_key
        base = self.BASE_URL_PAPER if paper else self.BASE_URL_LIVE
        self._client = httpx.Client(base_url=base, timeout=30)
        self._client.headers.update({
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
        })

    def submit_order(self, order: Order) -> dict[str, Any]:
        payload = {
            "symbol": order.symbol,
            "qty": order.quantity,
            "side": order.side,
            "type": order.order_type,
            "time_in_force": order.time_in_force,
        }
        if order.price:
            payload["limit_price"] = str(order.price)
        if order.stop_price:
            payload["stop_price"] = str(order.stop_price)
        response = self._client.post("/v2/orders", json=payload)
        response.raise_for_status()
        return response.json()

    def get_positions(self) -> list[dict[str, Any]]:
        response = self._client.get("/v2/positions")
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self._client.close()
