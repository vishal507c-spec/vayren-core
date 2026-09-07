"""Zerodha UBL adapter — broker identity, capability matrix, registration.

This package is the Unified Broker Layer face of Zerodha (design §5, M5).
It owns, as the single source of truth:

- ``BROKER_ID`` — the one stable registry id (``"zerodha"``; §10 — no
  ``"Zerodha"``/``"ZERODHA"``/``"KITE"`` aliases anywhere in product code);
- ``DISPLAY_NAME`` — UI label only, never a lookup key;
- ``HISTORICAL_CAPABILITIES`` — the exact advertised matrix: historical
  candles + symbols ONLY (no trading, no market-data stream, no funds —
  genuinely unimplemented surfaces are never claimed);
- :func:`zerodha_plugin_record` — builds the registry record from an
  injected provider factory (constructor injection: this package never
  imports the transport implementation, so there is no broker→data edge).

What lives where (one implementation, no duplication):

- Transport (KiteConnect SDK, auth, fetch, instruments, credentials,
  canonical interval mapping, error normalization): exactly once, in
  ``02_data/data/provider/zerodha/`` (already SDK-isolated; untouched).
- UBL contract surface (identity, capabilities, registration): here.
- The ``data.provider.factory`` shim passes its ``ZerodhaProvider``
  constructor into :func:`zerodha_plugin_record` — the resolved face
  remains a genuine ``ZerodhaProvider`` (``isinstance`` + sentinel
  identity preserved).
"""

from __future__ import annotations

from collections.abc import Callable

from broker.capabilities import CapabilitySet, Caps, Domain, capability_set
from broker.faces import FactoryPlugin
from broker.registry import BrokerRecord

BROKER_ID = "zerodha"
DISPLAY_NAME = "Zerodha"

HISTORICAL_CAPABILITIES: CapabilitySet = capability_set(
    {Domain.HISTORICAL_DATA: (Caps.HIST_CANDLES, Caps.HIST_SYMBOLS)}
)


def zerodha_plugin_record(provider_factory: Callable[[object], object]) -> BrokerRecord:
    """Build the registry record for Zerodha from an injected factory.

    ``provider_factory`` receives the caller's settings and must return a
    historical provider (duck-typed ``Provider``); faces are constructed
    fresh per call, exactly like the legacy provider dict.
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
    "zerodha_plugin_record",
]
