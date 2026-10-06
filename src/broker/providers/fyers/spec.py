"""FYERS SYSTEM → BROKERS management specification."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from broker.common.credentials_store import provider_service
from broker.management import BrokerSpec
from broker.providers.fyers.adapter import (
    BROKER_ID,
    DISPLAY_NAME,
    VENUE_SUBTITLE,
    FyersProvider,
)
from broker.providers.fyers.auto_auth import FyersAutoAuthEngine
from broker.providers.fyers.credentials import FyersCredentials
from broker.providers.fyers.live_auth import (
    DEFAULT_CALLBACK_PORT as FYERS_CALLBACK_PORT,
)
from broker.providers.fyers.live_auth import (
    SESSION_SERVICE as FYERS_SESSION_SERVICE,
)
from broker.providers.fyers.live_auth import (
    FyersAuthFlow,
    FyersSessionStore,
)
from broker.providers.fyers.live_auth import (
    default_redirect_url as fyers_redirect_url,
)
from broker.providers.fyers.live_market_data import FyersLiveMarketData
from broker.providers.fyers.selenium_auth import (
    BrowserUnavailableError,
    FyersSeleniumAuthEngine,
    fyers_selenium_interactive_login,
)
from broker.providers.fyers.session_adapter import FyersSessionAdapter


def _schema_rows(fields: Iterable[Any] | None) -> tuple[dict[str, object], ...]:
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


def fyers_management_spec() -> BrokerSpec:
    """The FYERS venue's SYSTEM → BROKERS management wiring."""

    def _register_session(adapter: object, _market_data: object | None) -> tuple[bool, str]:
        if adapter is None:
            return False, "no authenticated adapter provided"
        return True, "authenticated FYERS session (auth-only — no trading venue in this phase)"

    def _unregister() -> None:
        return None

    def _auto_authenticate(config: dict[str, str], session_store: object) -> tuple[bool, str]:
        """Startup/check-time automatic login (API-first, Selenium fallback)."""
        credentials = FyersCredentials(
            app_id=config.get("app_id", ""),
            secret=config.get("secret", ""),
            redirect_uri=config.get("redirect_uri", ""),
            pin=config.get("pin", ""),
            client_id=config.get("client_id", ""),
            totp_secret=config.get("totp_secret", ""),
        )
        api_engine = FyersAutoAuthEngine(credentials)
        api_possible, api_why = api_engine.auto_login_possible()
        message = ""
        if api_possible:
            ok, message = api_engine.ensure_session(session_store)
            if ok:
                return ok, message
            fatal_keywords = (
                "mismatch",
                "unauthorized",
                "invalid",
                "rejected",
                "HTTP 400",
                "HTTP 401",
                "HTTP 403",
                "pin",
                "totp",
                "otp",
            )
            if any(k in message.lower() for k in fatal_keywords):
                return False, message
            logging.getLogger(__name__).info(
                "FYERS API auto-auth failed (%s) — trying Selenium fallback", message
            )
        try:
            return FyersSeleniumAuthEngine(credentials).ensure_session(session_store)
        except BrowserUnavailableError as exc:
            logging.getLogger(__name__).info("FYERS Selenium unavailable (%s)", exc)
            if api_possible:
                return False, message
            return False, api_why

    return BrokerSpec(
        broker_id=BROKER_ID,
        display_name=DISPLAY_NAME,
        config_service=provider_service(BROKER_ID),
        session_service=FYERS_SESSION_SERVICE,
        required_config=("app_id", "secret"),
        masked_config=("app_id", "secret"),
        credential_schema=_schema_rows(FyersProvider.credential_fields),
        key_field="app_id",
        secret_field="secret",
        redirect_uri_field="redirect_uri",
        config_key_map={"api_key": "app_id", "api_secret": "secret"},
        build_adapter=lambda app_id, token: FyersSessionAdapter(app_id=app_id, access_token=token),
        build_market_data=lambda app_id, token: FyersLiveMarketData(
            app_id=app_id, access_token=token
        ),
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


__all__ = ["fyers_management_spec"]
