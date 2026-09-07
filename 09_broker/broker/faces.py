"""The three protocol faces + BrokerPlugin (design §4.3).

One venue = one plugin exposing any subset of the three faces. Faces are
protocols: adapters implement them without importing UBL internals beyond
this package's vocabulary. Nothing here knows any broker SDK.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from broker.capabilities import CapabilitySet, Domain
from broker.vocab import Environment, UnsupportedCapabilityError

# Historical-face sentinel vocabulary. The OBJECTS live in
# ``data.provider.contract`` (the historical engine performs ``is`` checks
# against them); this module accepts them opaquely and never recreates them.
Sentinel = Any


@runtime_checkable
class HistoricalFace(Protocol):
    """Canonical-vocabulary historical candle access (today's Provider)."""

    name: str

    def capabilities(self) -> CapabilitySet: ...

    def available(self) -> tuple[bool, str]: ...

    def symbols(self) -> set[str]: ...

    def fetch_candles(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> list[dict] | Sentinel: ...

    def new_session(self) -> None: ...

    def renew(self) -> None: ...


@runtime_checkable
class MarketDataFace(Protocol):
    """Normalized market-event streaming (today's MarketDataProvider).

    Full lifecycle (FINAL §F): authenticate → connect/open → subscribe →
    poll → health → unsubscribe → disconnect → reconnect → close.

    - Authentication is credential-gated: adapters resolve a
      ``CredentialRef`` (scope ``MARKET_DATA``) through a
      ``CredentialResolver`` BEFORE ``connect``; this face never sees
      secret values.
    - ``open(symbols, timeframe)`` is the legacy bulk-subscription entry
      and stays compatible (it connects implicitly).
    - ``connect`` prepares transport without subscribing; ``disconnect``
      drops transport but keeps subscription memory; ``reconnect`` drops
      and re-establishes transport, resuming the subscription set.
    - ``subscribe`` / ``unsubscribe`` adjust the subscription set
      incrementally (idempotent).
    Raw broker-native events must be normalized to VAYREN domain events
    before crossing this face — core never sees broker-native objects.
    """

    name: str

    def capabilities(self) -> CapabilitySet: ...

    def connect(self) -> None:
        """Establish transport without subscribing (idempotent)."""
        ...

    def open(self, symbols: tuple[str, ...], timeframe: str) -> None: ...

    def subscribe(self, symbols: tuple[str, ...]) -> None:
        """Add symbols to the subscription set (idempotent)."""
        ...

    def unsubscribe(self, symbols: tuple[str, ...]) -> None:
        """Remove symbols from the subscription set (idempotent)."""
        ...

    def poll(self) -> tuple[Any, ...]: ...

    def health(self) -> tuple[bool, str]: ...

    def disconnect(self) -> None:
        """Drop transport, keeping subscription memory for reconnect."""
        ...

    def reconnect(self) -> None:
        """Drop and re-establish transport, resuming subscriptions."""
        ...

    def close(self) -> None: ...


@runtime_checkable
class TradingFace(Protocol):
    """Order/fill/account surface (today's BrokerAdapter + funds)."""

    name: str
    environment: Environment

    def capabilities(self) -> CapabilitySet: ...

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def health(self) -> tuple[bool, str]: ...

    def account(self) -> dict[str, Any]: ...

    def funds(self) -> dict[str, float]: ...

    def positions(self) -> list[dict[str, Any]]: ...

    def open_orders(self) -> list[dict[str, Any]]: ...

    def place_order(
        self, plan: Any, client_order_id: str, idempotency_key: str | None = None
    ) -> str:
        """Submit; returns the broker order id.

        ``idempotency_key`` (M8 §8, optional) lets a production adapter
        deduplicate retries across timeout/disconnect. ``None`` (default)
        preserves the existing ``client_order_id``-only contract. Unknown
        submission states must be reconciled first — never blindly retried.
        """
        ...

    def cancel_order(self, broker_order_id: str) -> bool: ...

    def modify_order(
        self, broker_order_id: str, quantity: float | None, price: float | None
    ) -> bool: ...

    def stream_events(self) -> tuple[dict[str, Any], ...]: ...


@runtime_checkable
class BrokerPlugin(Protocol):
    """One venue as seen by the registry: faces + capability declaration."""

    name: str
    display_name: str

    def faces(self) -> tuple[Domain, ...]: ...

    def face(self, domain: Domain) -> object:
        """Return the face object for ``domain``.

        Raises :class:`UnsupportedCapabilityError` when the plugin does not
        serve that domain — the fail-closed contract (design §3.2 rule 3).
        """

    def capability_set(self) -> CapabilitySet:
        """Union across served faces; no phantom capabilities (§6 rule 3)."""
        return CapabilitySet()


class PluginLike(Protocol):
    """Structural view of any plugin for registry consumers.

    ``face`` implementations may accept extra positional arguments (the
    historical shim passes caller settings); extra args are ignored by
    structural typing, so both plugin flavors satisfy this protocol.
    """

    name: str
    display_name: str

    def faces(self) -> tuple[Domain, ...]: ...

    def face(self, domain: Domain, *args: object) -> object: ...

    def capability_set(self) -> CapabilitySet: ...


class StaticPlugin:
    """Plugin holding ready face objects (stateful venues, tests, wrappers).

    The face objects are injected — this class imports nothing beyond UBL
    vocabulary, so composition roots can wrap legacy brokers (PaperBroker,
    SandboxBroker, data providers) without any UBL→chapter dependency.
    """

    def __init__(
        self,
        name: str,
        display_name: str,
        face_map: dict[Domain, object],
        capabilities: CapabilitySet,
    ) -> None:
        if not name or not display_name:
            raise ValueError("plugin requires name and display_name")
        if not face_map:
            raise ValueError(f"plugin {name!r} must serve at least one face")
        unknown_domains = set(face_map) - set(Domain)
        if unknown_domains:
            raise ValueError(f"plugin {name!r} has unknown domains: {sorted(unknown_domains)}")
        self.name = name
        self.display_name = display_name
        self._faces = dict(face_map)
        self._capabilities = capabilities

    def faces(self) -> tuple[Domain, ...]:
        return tuple(self._faces)

    def face(self, domain: Domain, *_args: object) -> object:
        target = self._faces.get(domain)
        if target is None:
            raise UnsupportedCapabilityError(
                f"broker {self.name!r} does not provide {domain.value} capability"
            )
        return target

    def capability_set(self) -> CapabilitySet:
        return self._capabilities


class FactoryPlugin:
    """Plugin whose faces are built fresh per request (design §5).

    Historical factories receive the caller's settings as the single
    positional argument (legacy ``ProviderFactory`` shape); trading and
    market-data factories are zero-arg (legacy ``_AdapterFactory`` shape).
    ``capabilities`` may be ``None`` for opaque legacy factories — such
    records advertise nothing until a face is constructed (construction is
    the authority; discovery stays honest, never invented).
    """

    def __init__(
        self,
        name: str,
        display_name: str,
        factories: dict[Domain, object],
        capabilities: CapabilitySet | None,
    ) -> None:
        if not name or not display_name:
            raise ValueError("plugin requires name and display_name")
        if not factories:
            raise ValueError(f"plugin {name!r} must serve at least one face")
        unknown_domains = set(factories) - set(Domain)
        if unknown_domains:
            raise ValueError(f"plugin {name!r} has unknown domains: {sorted(unknown_domains)}")
        self.name = name
        self.display_name = display_name
        self._factories = dict(factories)
        self._capabilities = capabilities

    def faces(self) -> tuple[Domain, ...]:
        return tuple(self._factories)

    def face(self, domain: Domain, *args: object) -> object:
        factory = self._factories.get(domain)
        if factory is None:
            raise UnsupportedCapabilityError(
                f"broker {self.name!r} does not provide {domain.value} capability"
            )
        assert callable(factory), f"plugin {self.name!r}: {domain.value} factory not callable"
        return factory(*args)

    def capability_set(self) -> CapabilitySet:
        if self._capabilities is None:
            return CapabilitySet()
        return self._capabilities
