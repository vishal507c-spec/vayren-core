"""Broker factory — mode-gated adapter resolution, delegated to the UBL
registry (Phase 20 M3).

There is no simulated venue any more. LIVE resolves a registered adapter's
trading face and connects it; anything else raises
:class:`NotConfiguredError` with the remediation. There is no default live
path and no paper/sandbox fallback — a LIVE request that cannot be satisfied
is refused, never swapped for a simulated one.

M3 delegation (behavior-preserving, per ``90_brain/broker_layer_design.md``
§9): the venue map lives in the unified ``broker.registry`` (the ONLY
registry). Registration happens directly in the registry; ``resolve_broker``
keeps its signature and exception. Venue faces are constructed FRESH per
resolve. Capability discovery is registry-authoritative for declared records.
"""

from __future__ import annotations

from broker.capabilities import CapabilitySet, Domain
from broker.registry import BrokerRecord, default_registry
from broker.vocab import BrokerNotRegisteredError, UnsupportedCapabilityError
from execution.broker.adapter import BrokerAdapter, BrokerCapabilities, NotConfiguredError
from execution.modes import ExecutionMode, ModeGates, resolve_mode

# M6 funds capability id. NOT a second vocabulary: this string is
# byte-identical to UBL ``Caps.ACCOUNT_FUNDS`` ("account.funds").
_ACCOUNT_FUNDS = "account.funds"

# Legacy trading-capability tuples map onto UBL ids (stream.events →
# stream.fills; the rest are byte-identical).
_LEGACY_TO_UBL = {
    BrokerCapabilities.MARKET_ORDERS: "orders.market",
    BrokerCapabilities.LIMIT_ORDERS: "orders.limit",
    BrokerCapabilities.CANCEL: "orders.cancel",
    BrokerCapabilities.MODIFY: "orders.modify",
    BrokerCapabilities.POSITIONS: "account.positions",
    BrokerCapabilities.OPEN_ORDERS: "account.open_orders",
    BrokerCapabilities.STREAMING: "stream.fills",
    _ACCOUNT_FUNDS: "account.funds",
}


def _trading_caps(legacy: tuple[str, ...]) -> CapabilitySet:
    items = tuple(_LEGACY_TO_UBL[cap] for cap in legacy)
    return CapabilitySet(domains=(Domain.TRADING,), items=frozenset(items))


def resolve_broker(
    mode: ExecutionMode,
    gates: ModeGates,
    adapter_name: str = "",
    *,
    connect: bool = True,
) -> tuple[BrokerAdapter, ExecutionMode, tuple[str, ...]]:
    """Resolve (adapter, effective_mode, notes) fail-closed.

    BACKTEST has no venue at all — historical replay runs through the backtest
    engine, never through this factory; asking for it here is an error, not a
    silent no-op. LIVE needs a registered trading adapter AND all five gates
    (:func:`~execution.modes.resolve_mode` raises with the missing gates
    otherwise). There is no fallback venue: an unavailable LIVE broker is an
    explicit refusal.

    ``connect=False`` returns the registered face WITHOUT the verification
    probe. The reconciliation path re-resolves on every step and must not
    issue a profile REST call (nor drop the shared account cache) per session
    per step: the venue is verified once at registration, and usability of
    the returned face fails closed on the caller's next read.
    """
    if mode is ExecutionMode.BACKTEST:
        raise NotConfiguredError(
            "BACKTEST runs through the backtest engine (historical data, no venue); "
            "it has no broker face to resolve"
        )
    effective, notes = resolve_mode(mode, gates)
    try:
        record = default_registry().get(adapter_name)
        face = record.plugin.face(Domain.TRADING)
    except (BrokerNotRegisteredError, UnsupportedCapabilityError):
        raise NotConfiguredError(
            f"no broker adapter registered under {adapter_name!r} "
            "(LIVE_BROKER_INTEGRATION = NOT_CONFIGURED)"
        ) from None
    if not isinstance(face, BrokerAdapter):
        raise NotConfiguredError(
            f"registered venue {adapter_name!r} does not provide a trading face"
        )
    if connect:
        face.connect()
    return face, effective, notes


__all__ = ["resolve_broker", "BrokerRecord", "_trading_caps", "_ACCOUNT_FUNDS"]
