"""Provider factory — resolves ``settings.provider`` to a provider instance,
delegated to the UBL registry (Phase 20 M3, M7 shim retirement).

The ONLY name→provider map is the unified ``broker.registry`` (the
``register_provider`` shim was retired in M7 — registration happens
directly in the registry at startup). Zerodha is exposed there as a
historical-data plugin; capability discovery is registry-authoritative.
Adding a provider means implementing the contract and registering a
record in the unified registry.
"""

from __future__ import annotations

from broker.adapters.zerodha import BROKER_ID as ZERODHA_BROKER_ID
from broker.adapters.zerodha import zerodha_plugin_record
from broker.capabilities import Domain
from broker.registry import default_registry

from data.provider.contract import (
    ERR_PROVIDER_UNAVAILABLE,
    Provider,
    ProviderError,
)
from data.provider.zerodha import ZerodhaProvider
from data.settings import DownloadSettings


def unavailable_provider(reason: str) -> Provider:
    """A fail-closed historical provider used when the selected broker
    cannot serve history (M4).

    Every method refuses with a normalized ``ProviderError`` carrying the
    recorded reason — the download path fails loudly and honestly instead
    of silently using another broker. No network, no state.
    """

    class _UnavailableProvider:
        def available(self) -> tuple[bool, str]:
            return False, reason

        def symbols(self) -> set[str]:
            raise ProviderError(reason, ERR_PROVIDER_UNAVAILABLE)

        def fetch_candles(
            self,
            symbol: str,  # noqa: ARG002
            interval: str,  # noqa: ARG002
            start,  # noqa: ARG002
            end,  # noqa: ARG002
        ) -> list[dict]:
            raise ProviderError(reason, ERR_PROVIDER_UNAVAILABLE)

        def new_session(self) -> None:
            raise ProviderError(reason, ERR_PROVIDER_UNAVAILABLE)

        def renew(self) -> None:
            raise ProviderError(reason, ERR_PROVIDER_UNAVAILABLE)

    return _UnavailableProvider()  # type: ignore[return-value]


def _seed_zerodha() -> None:
    """Idempotent registration of the built-in historical-data provider.

    Single registration point for the venue name — no scattered broker-name
    branching anywhere else in the codebase (enforced by the architecture-
    rules test). Identity, capability matrix and record shape come from the
    UBL adapter package; only the transport constructor stays local (it
    must — the SDK lives in this chapter).
    """

    registry = default_registry()
    if ZERODHA_BROKER_ID in registry:
        registry.unregister(ZERODHA_BROKER_ID)
    registry.register(
        zerodha_plugin_record(lambda settings: ZerodhaProvider(settings))  # type: ignore[arg-type,return-value]
    )


_seed_zerodha()


def build_provider(settings: DownloadSettings) -> Provider:
    """Return the provider configured by ``settings.provider``.

    Delegation: the unified registry holds the record; the historical face
    is constructed fresh per call (exactly what the legacy dict produced).
    Fail-closed: unknown names raise ``ValueError`` with the same message
    shape the legacy dict path produced; a registered broker WITHOUT a
    historical face fails closed with an explicit capability error — never
    a silent fallback to another broker.
    """
    from broker.vocab import BrokerNotRegisteredError, UnsupportedCapabilityError

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
