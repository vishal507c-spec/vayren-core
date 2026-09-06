"""Broker factory — mode-gated adapter resolution.

PAPER always resolves (PaperBroker, no credentials). SANDBOX and LIVE raise
:class:`NotConfiguredError` unless an adapter is explicitly registered AND (for
LIVE) every safety gate passes. There is no default live path.
"""

from __future__ import annotations

from collections.abc import Callable

from execution.broker.adapter import BrokerAdapter, NotConfiguredError
from execution.broker.paper import PaperBroker
from execution.broker.sandbox import SandboxBroker
from execution.modes import ExecutionMode, ModeGates, resolve_mode

_AdapterFactory = Callable[[], BrokerAdapter]
_registry: dict[str, _AdapterFactory] = {}


def register_adapter(name: str, factory: _AdapterFactory) -> None:
    """Register a named broker adapter factory (sandbox/live venues)."""
    _registry[name] = factory


def _default_sandbox() -> BrokerAdapter:
    return SandboxBroker()


register_adapter("sandbox", _default_sandbox)


def resolve_broker(
    mode: ExecutionMode,
    gates: ModeGates,
    adapter_name: str = "",
    paper_capital: float = 1_000_000.0,
) -> tuple[BrokerAdapter, ExecutionMode, tuple[str, ...]]:
    """Resolve (adapter, effective_mode, notes) fail-closed.

    PAPER ignores gates and adapters. SANDBOX needs a registered adapter.
    LIVE needs a registered adapter AND all five gates, else PAPER.
    """
    effective, notes = resolve_mode(mode, gates)
    if effective == ExecutionMode.PAPER:
        broker: BrokerAdapter = PaperBroker(capital=paper_capital)
        broker.connect()
        return broker, effective, notes
    factory = _registry.get(adapter_name)
    if factory is None:
        raise NotConfiguredError(
            f"no broker adapter registered under {adapter_name!r} "
            "(LIVE_BROKER_INTEGRATION = NOT_CONFIGURED)"
        )
    broker = factory()
    broker.connect()
    return broker, effective, notes
