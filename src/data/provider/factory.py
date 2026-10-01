"""Provider factory — resolves ``settings.provider`` to a provider instance,
delegated to the UBL registry.

The ONLY name→provider map is the unified ``broker.registry``.
Adding a provider means implementing the contract and registering a
record in the unified registry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from broker.capabilities import Domain
from broker.providers import (
    ensure_live_venues,
    fyers_management_spec,
    seed_providers,
    zerodha_management_spec,
)
from broker.registry import default_registry
from broker.vocab import BrokerNotRegisteredError, UnsupportedCapabilityError
from data.provider.contract import Provider

if TYPE_CHECKING:
    from data.settings import DownloadSettings

# Ensure providers are seeded in the registry
seed_providers()


def build_provider(settings: DownloadSettings) -> Provider:
    """Return the provider configured by ``settings.provider``.

    Delegation: the unified registry holds the record; the historical face
    is constructed fresh per call.
    Fail-closed: unknown names raise ``ValueError``; a registered broker
    WITHOUT a historical face fails closed with an explicit capability error.
    """
    registry = default_registry()
    try:
        record = registry.get(settings.provider)
    except BrokerNotRegisteredError:
        historical = sorted(r.name for r in registry.find_with_domain(Domain.HISTORICAL_DATA))
        raise ValueError(
            f"unknown provider: {settings.provider!r} — "
            f"historical-data providers available: {historical}"
        ) from None
    try:
        face = record.plugin.face(Domain.HISTORICAL_DATA, settings)
    except UnsupportedCapabilityError:
        raise ValueError(
            f"provider {settings.provider!r} does not provide historical-data capability "
            f"(serves: {', '.join(d.value for d in record.faces)})"
        ) from None
    if not isinstance(face, Provider):
        raise ValueError(
            f"provider {settings.provider!r} historical face does not satisfy the Provider contract"
        )
    return face


__all__ = [
    "build_provider",
    "ensure_live_venues",
    "fyers_management_spec",
    "zerodha_management_spec",
]
