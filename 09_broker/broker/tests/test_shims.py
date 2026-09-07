"""M3 delegation-shim tests: data provider factory + execution broker factory.

Behavior preservation is the point: same instances (fresh per call), same
exceptions, same messages, same sentinel identity. See
``90_brain/broker_layer_design.md`` §9 (M3) and §12 (tests 6, 7, 10, 12).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent.parent
for entry in (
    "02_data",
    "07_risk",
    "08_execution",
    "09_broker",
):
    sys.path.insert(0, str(ROOT / entry))

from data.provider import contract  # noqa: E402
from data.provider.factory import build_provider  # noqa: E402
from data.settings import DownloadSettings  # noqa: E402
from execution.broker.factory import NotConfiguredError, resolve_broker  # noqa: E402
from execution.broker.paper import PaperBroker  # noqa: E402
from execution.broker.sandbox import SandboxBroker  # noqa: E402
from execution.modes import ExecutionMode, ModeGates  # noqa: E402

from broker.capabilities import CapabilitySet, Caps, Domain, capability_set  # noqa: E402
from broker.faces import FactoryPlugin  # noqa: E402
from broker.registry import (  # noqa: E402
    BrokerRecord,
    BrokerRegistry,
    DuplicateBrokerError,
    default_registry,
)


def _settings(tmp_path, provider: str = "zerodha") -> DownloadSettings:
    return DownloadSettings(data_dir=str(tmp_path), provider=provider)


def _hist_record(name: str, factory: object) -> BrokerRecord:
    """Historical record in the retired shim's exact shape (M7: built
    directly in the single registry)."""
    caps = capability_set({Domain.HISTORICAL_DATA: (Caps.HIST_CANDLES, Caps.HIST_SYMBOLS)})
    return BrokerRecord(
        name=name,
        display_name=name,
        plugin=FactoryPlugin(
            name=name,
            display_name=name,
            factories={Domain.HISTORICAL_DATA: factory},
            capabilities=caps,
        ),
        capabilities=caps,
        faces=(Domain.HISTORICAL_DATA,),
    )


def _register_historical(registry: BrokerRegistry, name: str, factory: object) -> None:
    if name in registry:
        registry.unregister(name)
    registry.register(_hist_record(name, factory))


def _register_trading_venue(registry: BrokerRegistry, name: str, factory: object) -> None:
    """Trading record in the retired shim's exact shape (opaque factory,
    no declared capabilities)."""
    if name in registry:
        registry.unregister(name)
    registry.register(
        BrokerRecord(
            name=name,
            display_name=name,
            plugin=FactoryPlugin(
                name=name,
                display_name=name,
                factories={Domain.TRADING: factory},
                capabilities=None,
            ),
            capabilities=CapabilitySet(),
            faces=(Domain.TRADING,),
        )
    )


# ── historical delegation (design §12 test 6/12) ─────────────────────────


def test_build_provider_returns_zerodha_via_registry(tmp_path) -> None:  # type: ignore[no-untyped-def]
    provider = build_provider(_settings(tmp_path))
    assert type(provider).__name__ == "ZerodhaProvider"
    # Fresh instance per call (same as the legacy dict path).
    other = build_provider(_settings(tmp_path))
    assert other is not provider


def test_build_provider_unknown_name_message_preserved(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError) as excinfo:
        build_provider(_settings(tmp_path, provider="nope"))
    message = str(excinfo.value)
    assert "unknown provider: 'nope'" in message
    assert "zerodha" in message  # available list still helpful


def test_build_provider_capability_absent_fails_closed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # 'paper' is registered (trading face) but has NO historical face —
    # the unified layer must refuse, never silently fall back.
    with pytest.raises(ValueError) as excinfo:
        build_provider(_settings(tmp_path, provider="paper"))
    assert "does not provide historical-data capability" in str(excinfo.value)


def test_unified_registry_rejects_duplicate_names(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """M7: the single registry is fail-closed on duplicates — the retired
    shim's overwrite leniency is gone by design; explicit
    unregister-then-register re-registers cleanly."""
    registry = default_registry()

    class FakeProvider:
        def __init__(self, settings: object) -> None:  # historical factory shape
            self.settings = settings

        def available(self) -> tuple[bool, str]:
            return True, "ok"

        def symbols(self) -> set[str]:
            return set()

        def fetch_candles(self, symbol: str, interval: str, start, end):  # type: ignore[no-untyped-def]  # noqa: ARG002
            return []

        def new_session(self) -> None: ...

        def renew(self) -> None: ...

    try:
        _register_historical(registry, "mock-hist", FakeProvider)
        with pytest.raises(DuplicateBrokerError):
            registry.register(_hist_record("mock-hist", FakeProvider))
        registry.unregister("mock-hist")
        _register_historical(registry, "mock-hist", FakeProvider)
        provider = build_provider(_settings(tmp_path, provider="mock-hist"))
        assert isinstance(provider, FakeProvider)
        record = registry.get("mock-hist")
        assert Domain.HISTORICAL_DATA in record.faces
    finally:
        if "mock-hist" in registry:
            registry.unregister("mock-hist")


def test_historical_sentinel_identity_preserved(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The engine does ``is`` checks on sentinels — delegation must keep
    them the SAME objects (design §4.3 note)."""

    class SentinelProvider:
        def __init__(self, settings: object) -> None:  # legacy ProviderFactory shape
            self.settings = settings

        def available(self) -> tuple[bool, str]:
            return True, "ok"

        def symbols(self) -> set[str]:
            return set()

        def fetch_candles(self, symbol: str, interval: str, start, end):  # type: ignore[no-untyped-def]  # noqa: ARG002
            return contract.TOKEN_EXPIRED

        def new_session(self) -> None: ...

        def renew(self) -> None: ...

    registry = default_registry()
    _register_historical(registry, "sentinel-hist", SentinelProvider)
    try:
        provider = build_provider(_settings(tmp_path, provider="sentinel-hist"))
        result = provider.fetch_candles("X", "15m", None, None)  # type: ignore[arg-type]
        assert result is contract.TOKEN_EXPIRED
        assert result is not contract.RATE_LIMITED
    finally:
        if "sentinel-hist" in registry:
            registry.unregister("sentinel-hist")


# ── trading delegation (design §12 tests 7/8/9/10) ───────────────────────


def test_resolve_paper_unchanged() -> None:  # type: ignore[no-untyped-def]
    broker, mode, notes = resolve_broker(ExecutionMode.PAPER, ModeGates())
    assert isinstance(broker, PaperBroker)
    assert mode is ExecutionMode.PAPER
    assert notes == ()
    broker.disconnect()


def test_resolve_paper_capital_parameter_preserved() -> None:  # type: ignore[no-untyped-def]
    broker, _, _ = resolve_broker(ExecutionMode.PAPER, ModeGates(), paper_capital=500.0)
    assert isinstance(broker, PaperBroker)
    assert broker.capital == 500.0  # per-call construction, not a shared instance
    broker.disconnect()


def test_resolve_sandbox_via_registry_fresh_per_call() -> None:  # type: ignore[no-untyped-def]
    first, mode, _ = resolve_broker(ExecutionMode.SANDBOX, ModeGates(), adapter_name="sandbox")
    assert isinstance(first, SandboxBroker)
    assert mode is ExecutionMode.SANDBOX
    second, _, _ = resolve_broker(ExecutionMode.SANDBOX, ModeGates(), adapter_name="sandbox")
    assert second is not first  # same per-call freshness as the legacy dict
    first.disconnect()
    second.disconnect()


def test_resolve_unknown_adapter_same_exception_and_message() -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(NotConfiguredError) as excinfo:
        resolve_broker(ExecutionMode.SANDBOX, ModeGates(), adapter_name="nope")
    assert "no broker adapter registered under 'nope'" in str(excinfo.value)


def test_resolve_live_downgrade_unchanged() -> None:  # type: ignore[no-untyped-def]
    broker, mode, notes = resolve_broker(ExecutionMode.LIVE, ModeGates())
    assert isinstance(broker, PaperBroker)  # fail-closed downgrade to PAPER
    assert mode is ExecutionMode.PAPER
    assert len(notes) == 5  # every missing gate reported, never silent
    broker.disconnect()


def test_direct_registry_registration_resolves_trading_venue() -> None:  # type: ignore[no-untyped-def]
    registry = default_registry()
    from execution.broker.adapter import BrokerCapabilities

    class Venue:
        name = "shim-venue"
        environment = "sandbox"

        def capabilities(self) -> tuple[str, ...]:
            return (BrokerCapabilities.MARKET_ORDERS,)

        def connect(self) -> None: ...

        def disconnect(self) -> None: ...

        def health(self) -> tuple[bool, str]:
            return True, "ok"

        def account(self) -> dict[str, object]:
            return {}

        def positions(self) -> list[dict[str, object]]:
            return []

        def open_orders(self) -> list[dict[str, object]]:
            return []

        def place_order(self, plan: object, client_order_id: str) -> str:  # noqa: ARG002
            return "V-1"

        def cancel_order(self, broker_order_id: str) -> bool:  # noqa: ARG002
            return False

        def modify_order(
            self,
            broker_order_id: str,  # noqa: ARG002
            quantity: float | None,  # noqa: ARG002
            price: float | None,  # noqa: ARG002
        ) -> bool:
            return False

        def stream_events(self) -> tuple[dict[str, object], ...]:
            return ()

        def on_market_price(self, symbol: str, price: float, timestamp: str) -> None: ...

        def reference_spread(self, symbol: str) -> float | None:  # noqa: ARG002
            return None

    venue = Venue()
    try:
        _register_trading_venue(registry, "shim-venue", lambda: venue)
        assert "shim-venue" in registry
        broker, mode, _ = resolve_broker(
            ExecutionMode.SANDBOX, ModeGates(), adapter_name="shim-venue"
        )
        assert broker is venue  # the factory's object, unchanged
        assert mode is ExecutionMode.SANDBOX
        broker.disconnect()
    finally:
        if "shim-venue" in registry:
            registry.unregister("shim-venue")


def test_direct_registry_record_rejects_non_adapter_face() -> None:  # type: ignore[no-untyped-def]
    """Fail-closed: a registered factory whose object is not a BrokerAdapter
    is refused at resolve time (never a mis-shaped venue reaching orders)."""
    registry = default_registry()

    class NotAnAdapter:
        name = "broken"

    try:
        _register_trading_venue(registry, "broken-venue", NotAnAdapter)
        with pytest.raises(NotConfiguredError) as excinfo:
            resolve_broker(ExecutionMode.SANDBOX, ModeGates(), adapter_name="broken-venue")
        assert "does not provide a trading face" in str(excinfo.value)
    finally:
        if "broken-venue" in registry:
            registry.unregister("broken-venue")


def test_builtin_records_advertise_legacy_equivalent_capabilities() -> None:  # type: ignore[no-untyped-def]
    registry = default_registry()
    paper = registry.get("paper")
    sandbox = registry.get("sandbox")
    assert paper.capabilities.supports("orders.market")
    assert not paper.capabilities.supports("orders.modify")  # PaperBroker has no MODIFY
    assert sandbox.capabilities.supports("orders.modify")
    assert sandbox.capabilities.supports("stream.fills")
    assert sandbox.capabilities.supports("account.positions")
    # M6 funds surface: Paper/Sandbox advertise ACCOUNT_FUNDS (§6 rule 3 —
    # capability present because the surface is genuinely implemented).
    assert paper.capabilities.supports("account.funds")
    assert sandbox.capabilities.supports("account.funds")
