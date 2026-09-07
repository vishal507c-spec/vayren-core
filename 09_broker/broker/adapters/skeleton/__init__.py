"""Skeleton reference adapter — the FINAL production adapter template (FINAL §N).

A future real broker requires ONLY: an adapter package shaped like this
one (identity + capability matrix + record builder), an injected
transport, a credential mapping, honest capabilities, registry
registration, and contract tests. No core modifications, no
broker-name branching.

This skeleton implements all three UBL faces with transport
NOT_CONFIGURED: every operational method fails closed with the unified
vocabulary (``UnsupportedCapabilityError``); ``capabilities()`` is empty
(undeclared → NOT_CONFIGURED); ``available()``/``health()`` report
``False`` honestly. It is real fail-closed behavior — not a mock — and
the contract suite proves a venue shaped like this can never trade.

Stdlib + UBL vocabulary only. No SDK, no network, no credentials.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from broker.capabilities import CapabilitySet, Domain
from broker.registry import BrokerRecord
from broker.vocab import Environment, UnsupportedCapabilityError

BROKER_ID = "skeleton"
DISPLAY_NAME = "Skeleton (reference template)"

_UNCONFIGURED = "skeleton transport NOT_CONFIGURED"


def _refuse(operation: str) -> UnsupportedCapabilityError:
    return UnsupportedCapabilityError(f"skeleton cannot {operation}: {_UNCONFIGURED}")


class SkeletonHistorical:
    """Historical face with no transport: honest unavailability."""

    name = BROKER_ID

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet()

    def available(self) -> tuple[bool, str]:
        return False, _UNCONFIGURED

    def symbols(self) -> set[str]:
        raise _refuse("list symbols")

    def fetch_candles(
        self,
        symbol: str,  # noqa: ARG002
        interval: str,  # noqa: ARG002
        start: datetime,  # noqa: ARG002
        end: datetime,  # noqa: ARG002
    ) -> list[dict]:
        raise _refuse("fetch candles")

    def new_session(self) -> None:
        raise _refuse("open a session")

    def renew(self) -> None:
        raise _refuse("renew a session")


class SkeletonMarketData:
    """Market-data face with no transport: silent nothing, honest health."""

    name = BROKER_ID

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet()

    def connect(self) -> None:
        raise _refuse("connect market data")

    def open(self, symbols: tuple[str, ...], timeframe: str) -> None:  # noqa: ARG002
        raise _refuse("open market data")

    def subscribe(self, symbols: tuple[str, ...]) -> None:  # noqa: ARG002
        raise _refuse("subscribe")

    def unsubscribe(self, symbols: tuple[str, ...]) -> None:  # noqa: ARG002
        raise _refuse("unsubscribe")

    def poll(self) -> tuple[Any, ...]:
        return ()

    def health(self) -> tuple[bool, str]:
        return False, _UNCONFIGURED

    def disconnect(self) -> None:
        return None

    def reconnect(self) -> None:
        raise _refuse("reconnect market data")

    def close(self) -> None:
        return None


class SkeletonTrading:
    """Trading face with no transport: every mutation fails closed."""

    name = BROKER_ID
    environment = Environment.PAPER

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet()

    def connect(self) -> None:
        raise _refuse("connect trading")

    def disconnect(self) -> None:
        return None

    def health(self) -> tuple[bool, str]:
        return False, _UNCONFIGURED

    def account(self) -> dict[str, Any]:
        raise _refuse("read account")

    def funds(self) -> dict[str, float]:
        raise _refuse("read funds")

    def positions(self) -> list[dict[str, Any]]:
        raise _refuse("read positions")

    def open_orders(self) -> list[dict[str, Any]]:
        raise _refuse("read open orders")

    def place_order(
        self,
        plan: Any,  # noqa: ARG002
        client_order_id: str,  # noqa: ARG002
        idempotency_key: str | None = None,  # noqa: ARG002
    ) -> str:
        raise _refuse("place orders")

    def cancel_order(self, broker_order_id: str) -> bool:  # noqa: ARG002
        raise _refuse("cancel orders")

    def modify_order(
        self,
        broker_order_id: str,  # noqa: ARG002
        quantity: float | None,  # noqa: ARG002
        price: float | None,  # noqa: ARG002
    ) -> bool:
        raise _refuse("modify orders")

    def stream_events(self) -> tuple[dict[str, Any], ...]:
        return ()


def skeleton_record() -> BrokerRecord:
    """Registry record for the reference template (never registered live)."""
    from broker.faces import StaticPlugin

    faces: dict[Domain, object] = {
        Domain.HISTORICAL_DATA: SkeletonHistorical(),
        Domain.MARKET_DATA: SkeletonMarketData(),
        Domain.TRADING: SkeletonTrading(),
    }
    return BrokerRecord(
        name=BROKER_ID,
        display_name=DISPLAY_NAME,
        plugin=StaticPlugin(
            name=BROKER_ID,
            display_name=DISPLAY_NAME,
            face_map=faces,
            capabilities=CapabilitySet(),
        ),
        capabilities=CapabilitySet(),
        faces=(Domain.HISTORICAL_DATA, Domain.MARKET_DATA, Domain.TRADING),
    )


__all__ = [
    "BROKER_ID",
    "DISPLAY_NAME",
    "SkeletonHistorical",
    "SkeletonMarketData",
    "SkeletonTrading",
    "skeleton_record",
]
