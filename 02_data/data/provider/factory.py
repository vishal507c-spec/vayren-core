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

from collections.abc import Iterable
from typing import Any

from broker.adapters.zerodha import BROKER_ID as ZERODHA_BROKER_ID
from broker.adapters.zerodha import zerodha_plugin_record
from broker.capabilities import Domain
from broker.management import BrokerSpec
from broker.registry import default_registry

from data.provider.contract import (
    ERR_PROVIDER_UNAVAILABLE,
    Provider,
    ProviderError,
)
from data.provider.credentials_store import provider_service
from data.provider.fyers import FyersProvider
from data.provider.fyers.live_auth import (
    DEFAULT_CALLBACK_PORT as FYERS_CALLBACK_PORT,
)
from data.provider.fyers.live_auth import (
    SESSION_SERVICE as FYERS_SESSION_SERVICE,
)
from data.provider.fyers.live_auth import (
    FyersAuthFlow,
    FyersSessionStore,
)
from data.provider.fyers.live_auth import (
    default_redirect_url as fyers_redirect_url,
)
from data.provider.fyers.selenium_auth import fyers_selenium_interactive_login
from data.provider.fyers.session_adapter import FyersSessionAdapter
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


def _seed_fyers() -> None:
    """Idempotent registration of the FYERS venue (authentication phase).

    Same single-registration-point discipline as Zerodha: identity and
    capability matrix come from the UBL adapter package; only the
    transport constructor stays local. The record advertises no history
    capabilities, so history resolution fails closed with the recorded
    reason while broker management + selection work.
    """
    from broker.adapters.fyers import BROKER_ID as FYERS_BROKER_ID
    from broker.adapters.fyers import fyers_plugin_record

    registry = default_registry()
    if FYERS_BROKER_ID in registry:
        registry.unregister(FYERS_BROKER_ID)
    registry.register(
        fyers_plugin_record(lambda settings: FyersProvider(settings))  # type: ignore[arg-type,return-value]
    )


_seed_fyers()


def _schema_rows(fields: Iterable[Any] | None) -> tuple[dict[str, object], ...]:
    """Reduce a provider ``credential_fields`` tuple to spec plain data.

    Home-chapter helper (concrete venue knowledge lives here): keeps only
    the render shape (key/label/secret/required) — values never cross.
    """
    rows: list[dict[str, object]] = []
    for field in fields or ():
        key = str(getattr(field, "key", "") or "")
        if not key:
            continue
        rows.append(
            {
                "key": key,
                "label": str(getattr(field, "label", "") or key),
                "secret": bool(getattr(field, "secret", False)),
                "required": bool(getattr(field, "required", False)),
            }
        )
    return tuple(rows)


def zerodha_management_spec() -> BrokerSpec:
    """The bundled venue's SYSTEM → BROKERS management wiring.

    Lives here (not in app) because every import below is concrete SDK
    boundary code the architecture tests pin to this chapter: adapter +
    auth flow + session store + venue registration. ``display_name`` comes
    from the adapter package's single source of truth — no alias literals.
    """
    from broker.adapters.zerodha import DISPLAY_NAME, VENUE_SUBTITLE

    from data.provider.zerodha.live_activation import (
        LIVE_VENUE_ID,
        register_zerodha_live_instance,
    )
    from data.provider.zerodha.live_auth import (
        DEFAULT_CALLBACK_PORT,
        SESSION_SERVICE,
        KiteAuthFlow,
        ZerodhaSessionStore,
        redirect_url,
        wait_for_login_token,
    )
    from data.provider.zerodha.live_market_data import ZerodhaMarketDataFace
    from data.provider.zerodha.live_trading import ZerodhaTradingAdapter

    def _unregister() -> None:
        registry = default_registry()
        if LIVE_VENUE_ID in registry:
            registry.unregister(LIVE_VENUE_ID)

    return BrokerSpec(
        broker_id="zerodha",
        display_name=DISPLAY_NAME,
        config_service=provider_service("zerodha"),
        session_service=SESSION_SERVICE,
        required_config=("api_key", "api_secret"),
        masked_config=("api_key", "api_secret"),
        credential_schema=_schema_rows(ZerodhaProvider.credential_fields),
        build_adapter=lambda api_key, token: ZerodhaTradingAdapter(
            api_key=api_key, access_token=token
        ),
        build_market_data=lambda api_key, token: ZerodhaMarketDataFace(
            api_key=api_key, access_token=token
        ),
        build_flow=KiteAuthFlow,
        build_session_store=lambda store, service: ZerodhaSessionStore(store, service),
        venue_register=register_zerodha_live_instance,
        venue_unregister=_unregister,
        interactive_login=wait_for_login_token,
        callback_port=DEFAULT_CALLBACK_PORT,
        callback_url=redirect_url(DEFAULT_CALLBACK_PORT),
        extra={"venue_subtitle": VENUE_SUBTITLE},
    )


def fyers_management_spec() -> BrokerSpec:
    """The FYERS venue's SYSTEM → BROKERS management wiring.

    Lives here (not in app) for the same chapter-pinning reason as the
    Zerodha spec: adapter + auth flow + session store are concrete venue
    boundary code. ``display_name`` comes from the UBL adapter package's
    single source of truth — no alias literals.

    Authentication phase: the venue is auth-only. ``venue_register``
    records the authenticated session WITHOUT touching the UBL registry
    (no ``fyers-live`` trading venue exists yet), so no execution path
    can resolve FYERS for orders. Health/account verification still runs
    through the read-only session adapter.
    """
    from broker.adapters.fyers import DISPLAY_NAME, VENUE_SUBTITLE

    def _register_session(adapter: object, _market_data: object | None) -> tuple[bool, str]:
        if adapter is None:
            return False, "no authenticated adapter provided"
        return True, "authenticated FYERS session (auth-only — no trading venue in this phase)"

    def _unregister() -> None:
        return None

    def _auto_authenticate(config: dict[str, str], session_store: object) -> tuple[bool, str]:
        """Startup/check-time automatic login (Selenium-first).

        Runs on the manager's worker thread: valid stored session → True
        with one probe; missing/expired + complete auto-login triple →
        Chrome drives FYERS' own login pages (TOTP + PIN, no copy/paste);
        Chrome/driver missing on the host → the official API flow as a
        fallback (logged); anything else → False with the exact reason
        (the manager shows LOGIN_REQUIRED and the user can still CONNECT
        through the button flow — capture stays automatic either way).
        """
        import logging

        from data.provider.fyers.auto_auth import FyersAutoAuthEngine
        from data.provider.fyers.credentials import FyersCredentials
        from data.provider.fyers.selenium_auth import (
            BrowserUnavailableError,
            FyersSeleniumAuthEngine,
        )

        credentials = FyersCredentials(
            app_id=config.get("app_id", ""),
            secret=config.get("secret", ""),
            redirect_uri=config.get("redirect_uri", ""),
            pin=config.get("pin", ""),
            client_id=config.get("client_id", ""),
            totp_secret=config.get("totp_secret", ""),
        )
        try:
            return FyersSeleniumAuthEngine(credentials).ensure_session(session_store)
        except BrowserUnavailableError as exc:
            logging.getLogger(__name__).info("FYERS Selenium unavailable (%s) — API fallback", exc)
            return FyersAutoAuthEngine(credentials).ensure_session(session_store)

    return BrokerSpec(
        broker_id="fyers",
        display_name=DISPLAY_NAME,
        config_service=provider_service("fyers"),
        session_service=FYERS_SESSION_SERVICE,
        required_config=("app_id", "secret"),
        masked_config=("app_id", "secret"),
        credential_schema=_schema_rows(FyersProvider.credential_fields),
        key_field="app_id",
        secret_field="secret",
        redirect_uri_field="redirect_uri",
        config_key_map={"api_key": "app_id", "api_secret": "secret"},
        build_adapter=lambda app_id, token: FyersSessionAdapter(app_id=app_id, access_token=token),
        build_market_data=None,
        build_flow=FyersAuthFlow,
        build_session_store=lambda store, service: FyersSessionStore(store, service),
        venue_register=_register_session,
        venue_unregister=_unregister,
        interactive_login=fyers_selenium_interactive_login,
        auto_authenticate=_auto_authenticate,
        callback_port=FYERS_CALLBACK_PORT,
        callback_url=fyers_redirect_url(FYERS_CALLBACK_PORT),
        extra={"venue_subtitle": VENUE_SUBTITLE, "auth_only": "true"},
    )


def ensure_live_venues() -> tuple[bool, str]:
    """Best-effort explicit live-venue activation (idempotent, no network).

    Each venue module decides for itself from operator configuration
    (environment flags + credentials); unconfigured venues stay
    unregistered (fail-closed). Called from the LIVE START path only —
    never at import, so merely importing this factory changes nothing.
    Returns (any_registered, summary).
    """
    results: list[str] = []
    registered = False
    try:
        from data.provider.zerodha.live_activation import register_zerodha_live

        ok, reason = register_zerodha_live()
        registered = registered or ok
        results.append(f"zerodha-live: {'registered' if ok else reason}")
    except ImportError as exc:
        results.append(f"zerodha-live: unavailable ({exc})")
    return registered, "; ".join(results)


_seed_zerodha()
_seed_fyers()


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
