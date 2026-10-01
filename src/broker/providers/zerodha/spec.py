"""Zerodha SYSTEM → BROKERS management specification."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from broker.common.credentials_store import provider_service
from broker.management import BrokerSpec
from broker.providers.zerodha.adapter import (
    BROKER_ID,
    DISPLAY_NAME,
    VENUE_SUBTITLE,
    ZerodhaProvider,
)
from broker.providers.zerodha.live_activation import (
    LIVE_VENUE_ID,
    register_zerodha_live_instance,
)
from broker.providers.zerodha.live_auth import (
    DEFAULT_CALLBACK_PORT,
    SESSION_SERVICE,
    KiteAuthFlow,
    ZerodhaSessionStore,
    redirect_url,
    wait_for_login_token,
)
from broker.providers.zerodha.live_market_data import ZerodhaMarketDataFace
from broker.providers.zerodha.live_trading import ZerodhaTradingAdapter
from broker.registry import default_registry


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


def zerodha_management_spec() -> BrokerSpec:
    """The Zerodha venue's SYSTEM → BROKERS management wiring."""

    def _unregister() -> None:
        registry = default_registry()
        if LIVE_VENUE_ID in registry:
            registry.unregister(LIVE_VENUE_ID)

    return BrokerSpec(
        broker_id=BROKER_ID,
        display_name=DISPLAY_NAME,
        config_service=provider_service(BROKER_ID),
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


__all__ = ["zerodha_management_spec"]
