"""Live-safety tests: arming, read-only sessions, credential/env failures.

No test here can place a real order: every LIVE-mode session runs against
the deterministic sandbox venue, and arming is explicit per test.
"""

import time

import pytest
from broker.capabilities import CapabilitySet, Domain
from broker.faces import FactoryPlugin
from broker.registry import BrokerRecord, default_registry
from risk import RiskPolicy
from strategy import StrategyParameters
from strategy.strategies.sma import SmaCrossover

from execution.broker.adapter import BrokerError
from execution.broker.credentials import BrokerCredentials
from execution.broker.readonly import ReadOnlyBroker
from execution.broker.sandbox import SandboxBroker
from execution.engine import IllegalTransitionError
from execution.market_data.replay import ReplayProvider, bars_to_candles
from execution.modes import ExecutionMode, ModeGates
from execution.runtime.session import LiveSession, SessionConfig
from execution.tests.helpers import make_bars


def _logic():
    return SmaCrossover(StrategyParameters({"fast_period": 2, "slow_period": 3}))


class _LiveSma(SmaCrossover):
    """Test-only live declaration. Real strategy code is never modified."""

    SUPPORTS_LIVE = True


def _live_logic():
    return _LiveSma(StrategyParameters({"fast_period": 2, "slow_period": 3}))


def _live_session() -> tuple[LiveSession, ReplayProvider]:
    candles = bars_to_candles(make_bars("TEST", 60), "15m")
    provider = ReplayProvider(candles, chunk_size=1000)
    gates = ModeGates(True, True, True, True, True)
    session = LiveSession(
        SessionConfig(mode=ExecutionMode.LIVE, gates=gates, adapter_name="safety-sandbox"),
        provider,
        RiskPolicy(),
    )
    # M7: venue registered directly in the single UBL registry
    # (the ``register_adapter`` shim is retired).
    registry = default_registry()
    if "safety-sandbox" in registry:
        registry.unregister("safety-sandbox")
    registry.register(
        BrokerRecord(
            name="safety-sandbox",
            display_name="safety-sandbox",
            plugin=FactoryPlugin(
                name="safety-sandbox",
                display_name="safety-sandbox",
                factories={Domain.TRADING: lambda: SandboxBroker(account_id="safety")},
                capabilities=None,
            ),
            capabilities=CapabilitySet(),
            faces=(Domain.TRADING,),
        )
    )
    session.register_strategy("sma", "1.0", _live_logic, StrategyParameters({}))
    return session, provider


def test_arming_machine_defaults_disarmed() -> None:
    from execution.modes import LiveArm

    candles = bars_to_candles(make_bars("TEST", 10), "15m")
    provider = ReplayProvider(candles, chunk_size=1000)
    session = LiveSession(SessionConfig(), provider, RiskPolicy())
    assert session.armed == LiveArm.DISARMED
    assert session.arm("operator allows") == LiveArm.ARMED
    assert session.armed == LiveArm.ARMED
    assert session.disarm("done") == LiveArm.DISARMED
    assert session.disarm("idempotent") == LiveArm.DISARMED
    assert session.state()["armed"] == "DISARMED"
    assert session.journal.of_kind("LIVE_ARMED")
    assert session.journal.of_kind("LIVE_DISARMED")


def test_live_submit_blocked_until_armed() -> None:
    session, provider = _live_session()
    assert session.start(("TEST",), "15m", {"sma": make_bars("TEST", 25)}).ready
    while not provider.exhausted:
        session.step(time.time())
    blocked = session.journal.of_kind("ORDER_BLOCKED_UNARMED")
    assert blocked, "LIVE submissions without arming must be blocked and journaled"
    broker = session._broker
    assert broker is not None
    assert isinstance(broker, SandboxBroker)
    assert broker.fills == ()


def test_live_submit_flows_once_armed() -> None:
    session, provider = _live_session()
    assert session.start(("TEST",), "15m", _warmup_bars()).ready
    session.arm("test explicitly allows sandbox fills")
    while not provider.exhausted:
        session.step(time.time())
    assert session.journal.of_kind("LIVE_ARMED")
    broker = session._broker
    assert isinstance(broker, SandboxBroker)
    assert len(broker.fills) > 0


def _warmup_bars():
    return {"sma": make_bars("TEST", 25)}


def test_readonly_session_rejects_submission() -> None:
    candles = bars_to_candles(make_bars("TEST", 60), "15m")
    provider = ReplayProvider(candles, chunk_size=1000)
    session = LiveSession(SessionConfig(), provider, RiskPolicy())
    session.register_strategy("sma", "1.0", _logic, StrategyParameters({}))
    assert session.start(("TEST",), "15m", _warmup_bars()).ready
    venue = SandboxBroker(account_id="ro-venue")
    venue.connect()
    session._broker = ReadOnlyBroker(venue)
    while not provider.exhausted:
        session.step(time.time())
    rejected = session.journal.of_kind("ORDER_REJECTED")
    assert rejected, "read-only venue must reject every submission"
    assert venue.fills == ()
    assert venue.open_orders() == []


def test_sandbox_missing_credentials_blocks_place() -> None:
    venue = SandboxBroker(
        account_id="x",
        credentials=BrokerCredentials(account_id="", environment=""),
    )
    venue.connect()
    from execution.models.order import OrderPlan

    with pytest.raises(BrokerError) as exc_info:
        venue.place_order(OrderPlan(intent_id="i", symbol="T", side="BUY", quantity=1.0), "c1")
    assert exc_info.value.code == "CREDENTIALS"


def test_unknown_order_reconcile_raises() -> None:
    candles = bars_to_candles(make_bars("TEST", 10), "15m")
    provider = ReplayProvider(candles, chunk_size=1000)
    session = LiveSession(SessionConfig(), provider, RiskPolicy())
    session.register_strategy("sma", "1.0", _logic, StrategyParameters({}))
    assert session.start(("TEST",), "15m", _warmup_bars()).ready
    with pytest.raises(IllegalTransitionError):
        session.engine.reconcile("ghost", "FILLED")
    with pytest.raises(IllegalTransitionError):
        session.engine.reconcile("ghost", "BOGUS_STATE")


def test_cancel_unknown_order_is_safe_noop() -> None:
    venue = SandboxBroker(account_id="cx")
    venue.connect()
    assert venue.cancel_order("SANDBOX-000000") is False
    assert venue.modify_order("SANDBOX-000000", 1.0, None) is False
