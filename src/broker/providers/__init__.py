"""Broker providers — concrete venue integrations and registration."""

from __future__ import annotations

from broker.providers.fyers import (
    BROKER_ID as FYERS_BROKER_ID,
)
from broker.providers.fyers import (
    FyersProvider,
    fyers_management_spec,
    fyers_plugin_record,
)
from broker.providers.zerodha import (
    BROKER_ID as ZERODHA_BROKER_ID,
)
from broker.providers.zerodha import (
    ZerodhaProvider,
    zerodha_management_spec,
    zerodha_plugin_record,
)
from broker.registry import default_registry


def seed_providers() -> None:
    """Idempotent registration of the built-in venue providers."""
    registry = default_registry()
    if ZERODHA_BROKER_ID in registry:
        registry.unregister(ZERODHA_BROKER_ID)
    registry.register(
        zerodha_plugin_record(lambda settings: ZerodhaProvider(settings))  # type: ignore[arg-type,return-value]
    )

    if FYERS_BROKER_ID in registry:
        registry.unregister(FYERS_BROKER_ID)
    registry.register(
        fyers_plugin_record(lambda settings: FyersProvider(settings))  # type: ignore[arg-type,return-value]
    )


def ensure_live_venues() -> tuple[bool, str]:
    """Best-effort explicit live-venue activation (idempotent, no network)."""
    results: list[str] = []
    registered = False
    try:
        from broker.providers.zerodha.live_activation import register_zerodha_live

        ok, reason = register_zerodha_live()
        registered = registered or ok
        results.append(f"zerodha-live: {'registered' if ok else reason}")
    except ImportError as exc:
        results.append(f"zerodha-live: unavailable ({exc})")
    return registered, "; ".join(results)


# Seed providers on package import
seed_providers()

__all__ = [
    "ensure_live_venues",
    "fyers_management_spec",
    "seed_providers",
    "zerodha_management_spec",
]
