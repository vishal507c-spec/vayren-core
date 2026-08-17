"""Provider — the engine's provider boundary.

The core download engine imports only ``data.provider.contract``. Concrete
providers live in isolated per-broker packages (``data.provider.zerodha`` and
future adapters); ``data.provider.factory.build_provider`` resolves
``settings.provider`` to an instance.

This package ``__init__`` deliberately imports only the contract so that
importing the core engine never pulls in a concrete provider or its SDKs.
"""

from data.provider.contract import RATE_LIMITED, TOKEN_EXPIRED, Provider, ProviderError

__all__ = [
    "Provider",
    "ProviderError",
    "RATE_LIMITED",
    "TOKEN_EXPIRED",
]
