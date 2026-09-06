"""Live market-data provider boundary — broker-agnostic streaming.

Mirrors the historical ``data.provider.contract`` philosophy: the engine
only speaks normalized events. Streaming transports (websockets, IPC) live
behind this protocol; nothing here knows any broker SDK.
"""

from __future__ import annotations

from typing import Any, Protocol

from execution.events import MarketEvent


class MarketDataProvider(Protocol):
    """Source of normalized market events for one live session."""

    @property
    def name(self) -> str:
        """Provider identifier (e.g. ``"replay"``)."""
        ...

    @property
    def capabilities(self) -> tuple[str, ...]:
        """Subset of: tick quote trade candle-close order-book heartbeat reconnect."""
        ...

    def open(self, symbols: tuple[str, ...], timeframe: str) -> None:
        """Start streaming for the subscription set (idempotent)."""
        ...

    def poll(self) -> tuple[MarketEvent, ...]:
        """Drain currently available normalized events (never blocks)."""
        ...

    def health(self) -> tuple[bool, str]:
        """(healthy, reason) — transport + heartbeat state."""
        ...

    def close(self) -> None:
        """Stop streaming and release transport resources."""
        ...


class MarketDataError(RuntimeError):
    """Transport-level failure, normalized for the runtime."""

    def __init__(self, message: str, code: str = "PROVIDER_ERROR") -> None:
        super().__init__(message)
        self.code = code


def provider_supports(provider: Any, capability: str) -> bool:
    """True when the provider advertises a capability (duck-typed)."""
    try:
        return capability in tuple(provider.capabilities)
    except Exception:
        return False
