"""Provider factory — resolves ``settings.provider`` to a provider instance.

The registry is intentionally tiny: a name → provider class map plus a
register hook. Adding a provider means implementing the contract and
registering it here (or calling :func:`register_provider` at startup).
"""

from __future__ import annotations

from typing import Protocol

from data.provider.contract import Provider
from data.provider.zerodha import ZerodhaProvider
from data.settings import DownloadSettings


class ProviderFactory(Protocol):
    """A provider class: constructed with settings only."""

    def __call__(self, settings: DownloadSettings) -> Provider: ...


_PROVIDER_TYPES: dict[str, ProviderFactory] = {"zerodha": ZerodhaProvider}


def register_provider(name: str, provider_type: ProviderFactory) -> None:
    """Register a provider class under a ``settings.provider`` name."""
    _PROVIDER_TYPES[name] = provider_type


def build_provider(settings: DownloadSettings) -> Provider:
    """Return the provider configured by ``settings.provider``."""
    provider_type = _PROVIDER_TYPES.get(settings.provider)
    if provider_type is None:
        raise ValueError(
            f"unknown provider: {settings.provider!r} — available: {sorted(_PROVIDER_TYPES)}"
        )
    return provider_type(settings)
