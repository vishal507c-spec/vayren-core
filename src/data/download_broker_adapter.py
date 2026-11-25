"""Minimal KiteConnect-shaped seam for the preserved historical engine.

The original standalone engine calls exactly two KiteConnect methods:
``instruments(exchange)`` and ``historical_data(...)``. This module supplies
those two calls from the broker that is authenticated and CONNECTED in
VAYREN's SYSTEM UI, using only the public broker entrypoints (the registry
factory and the manager's read-only adapter). It adds no download logic:
parsing, retries, throttling, chunking, database writes and recovery stay in
the preserved engine.

Broker selection is fail-closed: no CONNECTED broker -> no download.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime
from typing import Any

from data.provider.factory import build_provider
from data.settings import DownloadSettings

# Kite interval ids used by the preserved engine -> canonical VAYREN intervals.
_KITE_TO_CANONICAL: dict[str, str] = {
    "minute": "1m",
    "5minute": "5m",
    "15minute": "15m",
    "30minute": "30m",
    "60minute": "1h",
    "day": "1d",
}


def _universe_symbol_rows(symbols_csv: str) -> list[dict[str, Any]]:
    """Bare symbols from the engine's own symbols.csv.

    FYERS addresses history by symbol, not by Kite's numeric token, so stable
    synthetic tokens are used only as opaque handles; the reverse map resolves
    them back to symbols before any venue call. Zerodha likewise resolves the
    symbol through its own instrument master inside its provider.
    """
    rows: list[dict[str, Any]] = []
    if not os.path.isfile(symbols_csv):
        return rows
    with open(symbols_csv, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            symbol = str(row.get("trading_symbol", "") or "").strip().upper()
            if not symbol:
                continue
            token = sum(
                byte << (8 * index) for index, byte in enumerate(symbol.encode("utf-8")[:6])
            )
            rows.append(
                {
                    "tradingsymbol": symbol,
                    "instrument_token": token,
                    "exchange": "NSE",
                    "segment": "NSE",
                }
            )
    return rows


class SelectedBrokerKite:
    """The two-call surface the original engine expects from KiteConnect."""

    def __init__(
        self,
        broker_id: str,
        symbols_csv: str,
        provider: Any = None,
        fyers: Any = None,
    ) -> None:
        self._broker_id = broker_id
        self._provider = provider
        self._fyers = fyers
        self._rows = _universe_symbol_rows(symbols_csv)
        self._by_token = {
            int(row["instrument_token"]): str(row["tradingsymbol"]) for row in self._rows
        }

    @property
    def broker_id(self) -> str:
        return self._broker_id

    def instruments(self, exchange: str = "NSE") -> list[dict[str, Any]]:
        _ = exchange  # Kite signature compatibility; the universe is NSE-only.
        return [dict(row) for row in self._rows]

    def historical_data(
        self,
        instrument_token: int,
        from_date: datetime,
        to_date: datetime,
        interval: str,
        continuous: bool = False,
        oi: bool = False,
    ) -> list[dict[str, Any]]:
        _ = (continuous, oi)  # Kite signature compatibility; both are unused here.
        symbol = self._by_token.get(int(instrument_token))
        if symbol is None:
            raise RuntimeError(f"invalid instrument token: {instrument_token!r}")
        canonical = _KITE_TO_CANONICAL.get(str(interval))
        if canonical is None:
            raise RuntimeError(f"invalid interval: {interval!r}")
        adapter = self._provider if self._provider is not None else self._fyers
        try:
            return list(adapter.fetch_candles(symbol, canonical, from_date, to_date))
        except Exception as exc:
            message = str(exc)
            lowered = message.lower()
            if "429" in lowered or "rate" in lowered:
                raise RuntimeError(f"rate limited by venue (HTTP 429): {message}") from exc
            if "auth" in lowered or "token" in lowered or "invalid" in lowered:
                raise RuntimeError(
                    f"invalid broker token / authentication failed: {message}"
                ) from exc
            raise


def _selected_broker(broker_manager: Any) -> str:
    """Fail-closed resolution of the broker selected+CONNECTED in SYSTEM."""
    snapshot = broker_manager.snapshot() or {}
    broker_id = str(snapshot.get("active_broker") or "").strip()
    if not broker_id or broker_id == "paper":
        raise RuntimeError(
            "No broker CONNECTED in VAYREN SYSTEM UI — historical download not started. "
            "Select and connect a broker in SYSTEM first."
        )
    if not broker_manager.is_connected(broker_id):
        raise RuntimeError(
            f"Broker {broker_id!r} is not CONNECTED in VAYREN SYSTEM UI — "
            "historical download not started."
        )
    session = broker_manager.get_session_snapshot(broker_id) or {}
    state = str(session.get("state") or "").strip()
    if state and state != "CONNECTED":
        raise RuntimeError(
            f"Broker {broker_id!r} session state is {state!r}, not CONNECTED — "
            "historical download not started."
        )
    return broker_id


def build_selected_broker_shim(
    broker_manager: Any, data_dir: str | None = None
) -> SelectedBrokerKite:
    """Build the Kite-shaped seam from VAYREN's public auth infrastructure."""
    broker_id = _selected_broker(broker_manager)
    symbols_csv = os.path.join(
        str(data_dir or os.path.dirname(os.path.abspath(__file__))), "symbols.csv"
    )

    if broker_id == "zerodha":
        settings = DownloadSettings(data_dir=data_dir or os.getcwd(), provider="zerodha")
        provider = build_provider(settings)
        return SelectedBrokerKite(broker_id, symbols_csv, provider=provider)

    adapter = broker_manager.read_only_adapter(broker_id)
    if adapter is None or not hasattr(adapter, "fetch_candles"):
        raise RuntimeError(
            f"No authenticated historical session for broker {broker_id!r} — "
            "historical download not started."
        )
    return SelectedBrokerKite(broker_id, symbols_csv, fyers=adapter)


__all__ = ["SelectedBrokerKite", "build_selected_broker_shim"]
