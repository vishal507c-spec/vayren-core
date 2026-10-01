"""Zerodha UBL provider — broker identity, capability matrix, registration, and transport.

Single source of truth for Zerodha integration.
"""

from __future__ import annotations

from collections.abc import Callable

from broker.capabilities import CapabilitySet, Caps, Domain, capability_set
from broker.faces import FactoryPlugin
from broker.providers.zerodha.adapter import (
    BROKER_ID,
    DISPLAY_NAME,
    VENUE_SUBTITLE,
    ZerodhaProvider,
)
from broker.providers.zerodha.auth import AuthEngine, AuthError
from broker.providers.zerodha.credentials import (
    ZerodhaCredentials,
    load_zerodha_credentials,
)
from broker.providers.zerodha.spec import zerodha_management_spec
from broker.registry import BrokerRecord

HISTORICAL_CAPABILITIES: CapabilitySet = capability_set(
    {Domain.HISTORICAL_DATA: (Caps.HIST_CANDLES, Caps.HIST_SYMBOLS)}
)


def zerodha_plugin_record(provider_factory: Callable[[object], object]) -> BrokerRecord:
    """Build the registry record for Zerodha from an injected factory."""
    return BrokerRecord(
        name=BROKER_ID,
        display_name=DISPLAY_NAME,
        plugin=FactoryPlugin(
            name=BROKER_ID,
            display_name=DISPLAY_NAME,
            factories={Domain.HISTORICAL_DATA: provider_factory},
            capabilities=HISTORICAL_CAPABILITIES,
        ),
        capabilities=HISTORICAL_CAPABILITIES,
        faces=(Domain.HISTORICAL_DATA,),
    )


__all__ = [
    "BROKER_ID",
    "DISPLAY_NAME",
    "HISTORICAL_CAPABILITIES",
    "VENUE_SUBTITLE",
    "AuthEngine",
    "AuthError",
    "ZerodhaCredentials",
    "ZerodhaProvider",
    "load_zerodha_credentials",
    "zerodha_management_spec",
    "zerodha_plugin_record",
]
