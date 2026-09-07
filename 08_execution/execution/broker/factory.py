"""Broker factory — mode-gated adapter resolution, delegated to the UBL
registry (Phase 20 M3).

PAPER always resolves (PaperBroker, no credentials). SANDBOX and LIVE raise
:class:`NotConfiguredError` unless an adapter is explicitly registered AND (for
LIVE) every safety gate passes. There is no default live path.

M3 delegation (behavior-preserving, per ``90_brain/broker_layer_design.md``
§9): the venue map lives in the unified ``broker.registry`` (the ONLY
registry). M7 retired the ``register_adapter`` shim — registration happens
directly in the registry; ``resolve_broker`` keeps its signature,
downgrade semantics and exception. Venue faces are constructed FRESH per
resolve — the same per-call instances the legacy local dict produced.
Capability discovery is registry-authoritative for declared records.
"""

from __future__ import annotations

from broker.capabilities import CapabilitySet, Domain
from broker.registry import BrokerRecord, default_registry
from broker.vocab import BrokerNotRegisteredError, UnsupportedCapabilityError

from execution.broker.adapter import BrokerAdapter, BrokerCapabilities, NotConfiguredError
from execution.broker.paper import PaperBroker
from execution.broker.sandbox import SandboxBroker
from execution.modes import ExecutionMode, ModeGates, resolve_mode

# M6 funds capability id. NOT a second vocabulary: this string is
# byte-identical to UBL ``Caps.ACCOUNT_FUNDS`` ("account.funds"); no new
# ``BrokerCapabilities`` constant is introduced by design.
_ACCOUNT_FUNDS = "account.funds"

_PAPER_CAPS = (
    BrokerCapabilities.MARKET_ORDERS,
    BrokerCapabilities.LIMIT_ORDERS,
    BrokerCapabilities.CANCEL,
    BrokerCapabilities.POSITIONS,
    BrokerCapabilities.OPEN_ORDERS,
    _ACCOUNT_FUNDS,
)
_SANDBOX_CAPS = (*_PAPER_CAPS, BrokerCapabilities.MODIFY, BrokerCapabilities.STREAMING)

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


def _default_sandbox() -> BrokerAdapter:
    return SandboxBroker()


def _seed_builtins() -> None:
    """Expose Paper/Sandbox through the unified registry (M2, discovery)."""
    from broker.faces import FactoryPlugin

    registry = default_registry()
    for name, factory, caps in (
        ("paper", lambda: PaperBroker(capital=1_000_000.0), _PAPER_CAPS),
        ("sandbox", _default_sandbox, _SANDBOX_CAPS),
    ):
        caps_set = _trading_caps(caps)
        record = BrokerRecord(
            name=name,
            display_name=name.capitalize(),
            plugin=FactoryPlugin(
                name=name,
                display_name=name.capitalize(),
                factories={Domain.TRADING: factory},
                capabilities=caps_set,
            ),
            capabilities=caps_set,
            faces=(Domain.TRADING,),
        )
        if name in registry:
            registry.unregister(name)
        registry.register(record)


_seed_builtins()


def resolve_broker(
    mode: ExecutionMode,
    gates: ModeGates,
    adapter_name: str = "",
    paper_capital: float = 1_000_000.0,
) -> tuple[BrokerAdapter, ExecutionMode, tuple[str, ...]]:
    """Resolve (adapter, effective_mode, notes) fail-closed.

    PAPER ignores gates and adapters. SANDBOX needs a registered adapter.
    LIVE needs a registered adapter AND all five gates, else PAPER.
    Delegation: venues resolve through the unified registry; faces are
    constructed fresh per call (same instances the legacy dict produced).
    """
    effective, notes = resolve_mode(mode, gates)
    if effective == ExecutionMode.PAPER:
        broker: BrokerAdapter = PaperBroker(capital=paper_capital)
        broker.connect()
        return broker, effective, notes
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
    face.connect()
    return face, effective, notes
