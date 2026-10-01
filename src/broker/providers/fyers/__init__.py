"""FYERS UBL provider — broker identity, capability matrix, registration, and transport.

Single source of truth for FYERS integration.
"""

from __future__ import annotations

from collections.abc import Callable

from broker.capabilities import CapabilitySet, Caps, Domain, capability_set
from broker.faces import FactoryPlugin
from broker.providers.fyers.adapter import (
    BROKER_ID,
    DISPLAY_NAME,
    VENUE_SUBTITLE,
    FyersProvider,
)
from broker.providers.fyers.auto_auth import FyersAutoAuthEngine
from broker.providers.fyers.credentials import (
    FyersCredentials,
    load_fyers_credentials,
)
from broker.providers.fyers.live_auth import (
    FyersAuthFlow,
    FyersSessionStore,
)
from broker.providers.fyers.session_adapter import FyersSessionAdapter
from broker.providers.fyers.spec import fyers_management_spec
from broker.registry import BrokerRecord

HISTORICAL_CAPABILITIES: CapabilitySet = capability_set(
    {Domain.HISTORICAL_DATA: (Caps.HIST_CANDLES, Caps.HIST_SYMBOLS)}
)


def fyers_plugin_record(provider_factory: Callable[[object], object]) -> BrokerRecord:
    """Build the registry record for FYERS from an injected factory."""
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
    "FyersAuthFlow",
    "FyersAutoAuthEngine",
    "FyersCredentials",
    "FyersProvider",
    "FyersSessionAdapter",
    "FyersSessionStore",
    "fyers_management_spec",
    "fyers_plugin_record",
    "load_fyers_credentials",
]
