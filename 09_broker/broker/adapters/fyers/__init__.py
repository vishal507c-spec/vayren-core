"""FYERS UBL adapter — broker identity, capability matrix, registration.

This package is the Unified Broker Layer face of FYERS. It owns, as the
single source of truth:

- ``BROKER_ID`` — the one stable registry id (``"fyers"``; no aliases
  anywhere in product code);
- ``DISPLAY_NAME`` — UI label only, never a lookup key;
- ``HISTORICAL_CAPABILITIES`` — honestly EMPTY in the authentication phase:
  FYERS historical download is not implemented yet, so the venue claims
  no history capabilities and every history call fails closed with an
  explicit reason (never a silent fallback to another broker);
- :func:`fyers_plugin_record` — builds the registry record from an
  injected provider factory (constructor injection: this package never
  imports the transport implementation, so there is no broker→data edge).

What lives where (one implementation, no duplication):

- Transport (FYERS API v3 auth, session, credentials, future interval
  mapping, error normalization): exactly once, in
  ``02_data/data/provider/fyers/`` (SDK-isolated; stdlib only).
- UBL contract surface (identity, capabilities, registration): here.
- The ``data.provider.factory`` shim passes its ``FyersProvider``
  constructor into :func:`fyers_plugin_record` — the resolved face
  remains a genuine ``FyersProvider`` (``isinstance`` + protocol checks
  preserved).
"""

from __future__ import annotations

from collections.abc import Callable

from broker.capabilities import CapabilitySet, Domain, capability_set
from broker.faces import FactoryPlugin
from broker.registry import BrokerRecord

BROKER_ID = "fyers"
DISPLAY_NAME = "Fyers"
# The venue's own product label. Lives here (with DISPLAY_NAME) so UI surfaces
# read it from the snapshot instead of pinning a broker literal themselves.
VENUE_SUBTITLE = "FYERS API v3"

# Authentication phase: no history capabilities claimed. The record still
# serves the HISTORICAL_DATA domain (registry records must serve at least
# one) through a fail-closed face — selection + management work, downloads
# refuse loudly with the recorded reason.
HISTORICAL_CAPABILITIES: CapabilitySet = capability_set({})


def fyers_plugin_record(provider_factory: Callable[[object], object]) -> BrokerRecord:
    """Build the registry record for FYERS from an injected factory.

    ``provider_factory`` receives the caller's settings and must return a
    historical provider (duck-typed ``Provider``); faces are constructed
    fresh per call, exactly like the Zerodha record.
    """
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
    "fyers_plugin_record",
]
