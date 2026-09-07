"""Sandbox end-to-end: real pipeline through SandboxBroker + failure sims.

market data → strategy → signal → intent → risk → planner → sandbox
→ ack → fill → portfolio → journal → reconciliation. No mocks on the
core path; the venue itself is the deterministic simulated sandbox.
"""

import time

import pytest
from broker.capabilities import CapabilitySet, Domain
from broker.faces import FactoryPlugin
from broker.registry import BrokerRecord, default_registry
from risk import RiskPolicy
from strategy import StrategyParameters
from strategy.strategies.sma import SmaCrossover

from execution.broker.adapter import NotConfiguredError
from execution.broker.factory import resolve_broker
from execution.broker.paper import PaperBroker
from execution.broker.sandbox import SandboxBroker
from execution.engine import IllegalTransitionError
from execution.market_data.replay import ReplayProvider, bars_to_candles
from execution.models.order import BrokerOrder
from execution.modes import ExecutionMode, ModeGates
from execution.replay import replay_and_compare
from execution.runtime.session import LiveSession, SessionConfig
from execution.tests.helpers import make_bars


def _logic():
    return SmaCrossover(StrategyParameters({"fast_period": 2, "slow_period": 3}))


def _register_venue(name: str, factory: object) -> None:
    """M7: register a trading venue directly in the single UBL registry
    (the ``register_adapter`` shim is retired)."""
    registry = default_registry()
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


def _sandbox_session(candles, broker=None, **config_overrides):
    provider = ReplayProvider(candles, chunk_size=1000)
    values = {"mode": ExecutionMode.SANDBOX, "adapter_name": "e2e-sandbox"}
    values.update(config_overrides)
    config = SessionConfig(**values)
    venue = broker if broker is not None else SandboxBroker(account_id="e2e-acct")
    _register_venue("e2e-sandbox", lambda: venue)
    session = LiveSession(config, provider, RiskPolicy())
    session.register_strategy("sma", "1.0", _logic, StrategyParameters({}))
    return session, provider, venue


def _warmup():
    return {"sma": make_bars("TEST", 25)}


def test_sandbox_e2e_full_chain() -> None:
    candles = bars_to_candles(make_bars("TEST", 60), "15m")
    session, provider, venue = _sandbox_session(candles)
    report = session.start(("TEST",), "15m", _warmup())
    assert report.ready, report.reasons
    assert session.mode == ExecutionMode.SANDBOX
    now = time.time()
    while not provider.exhausted:
        session.step(now)
    kinds = {entry.kind for entry in session.journal.entries}
    for expected in (
        "SIGNAL_GENERATED",
        "RISK_APPROVED",
        "ORDER_PLANNED",
        "ORDER_SUBMITTED",
        "ORDER_ACK",
        "FILL",
        "POSITION_UPDATED",
        "LIVE_READY",
    ):
        assert expected in kinds, f"missing causal fact: {expected}"
    assert venue.fills, "sandbox must fill"
    ledger_qty = sum(p.quantity for p in session.ledger.all_positions())
    broker_qty = sum(f.fill_qty if f.side == "BUY" else -f.fill_qty for f in venue.fills)
    assert ledger_qty == broker_qty  # ledger tracks every venue fill
    state = session.state()
    assert state["broker"]["environment"] == "sandbox"
    assert state["broker"]["account_id"] == "e2e-acct"
    assert state["mode"] == "SANDBOX"
    reconcile = session.reconcile_now()
    assert not reconcile.blocks_live
    assert reconcile.positions.matched and reconcile.orders.matched


def test_strategy_parity_paper_sandbox() -> None:
    """Same strategy + same tape → same signals; same fill economics."""

    candles = bars_to_candles(make_bars("TEST", 60), "15m")

    provider = ReplayProvider(candles, chunk_size=1000)
    paper_session = LiveSession(SessionConfig(), provider, RiskPolicy())
    paper_session.register_strategy("sma", "1.0", _logic, StrategyParameters({}))
    assert paper_session.start(("TEST",), "15m", _warmup()).ready
    now = time.time()
    while not provider.exhausted:
        paper_session.step(now)

    provider = ReplayProvider(candles, chunk_size=1000)
    sandbox_session = LiveSession(
        SessionConfig(mode=ExecutionMode.SANDBOX, adapter_name="parity-sandbox"),
        provider,
        RiskPolicy(),
    )
    _register_venue("parity-sandbox", lambda: SandboxBroker(account_id="parity"))
    sandbox_session.register_strategy("sma", "1.0", _logic, StrategyParameters({}))
    assert sandbox_session.start(("TEST",), "15m", _warmup()).ready
    now = time.time()
    while not provider.exhausted:
        sandbox_session.step(now)

    def signals_of(session):
        return tuple(
            (e.payload["signal_id"], e.payload["event_seq"])
            for e in session.journal.of_kind("SIGNAL_GENERATED")
        )

    assert signals_of(paper_session) == signals_of(sandbox_session)
    paper_broker = paper_session._broker
    sandbox_broker = sandbox_session._broker
    assert isinstance(paper_broker, PaperBroker)
    assert isinstance(sandbox_broker, SandboxBroker)
    paper_prices = tuple(round(f.fill_price, 6) for f in paper_broker.fills)
    sandbox_prices = tuple(round(f.fill_price, 6) for f in sandbox_broker.fills)
    assert paper_prices == sandbox_prices


def test_replay_parity_sandbox_session() -> None:
    candles = bars_to_candles(make_bars("TEST", 40), "15m")

    def run_once(tape):
        provider = ReplayProvider(tape, chunk_size=1000)
        session = LiveSession(
            SessionConfig(mode=ExecutionMode.SANDBOX, adapter_name="replay-sandbox"),
            provider,
            RiskPolicy(),
        )
        _register_venue("replay-sandbox", lambda: SandboxBroker(account_id="replay"))
        session.register_strategy("sma", "1.0", _logic, StrategyParameters({}))
        assert session.start(("TEST",), "15m", _warmup()).ready
        now = time.time()
        while not provider.exhausted:
            session.step(now)
        return tuple(
            e.payload.get("signal_id", e.payload.get("client_order_id", e.kind))
            for e in session.journal.entries
            if e.kind in ("SIGNAL_GENERATED", "ORDER_SUBMITTED", "FILL")
        )

    report = replay_and_compare(candles, run_once)
    assert report.matched, report.divergences
    assert report.compared > 0


def test_failure_broker_disconnect_mid_run() -> None:
    candles = bars_to_candles(make_bars("TEST", 60), "15m")
    venue = SandboxBroker(account_id="disc")
    session, provider, _ = _sandbox_session(candles, broker=venue)
    assert session.start(("TEST",), "15m", _warmup()).ready
    session.step(time.time())
    venue.simulate_disconnect()
    assert venue.health()[0] is False
    # disconnected stream errors journal safely every step
    session.step(time.time())
    assert session.journal.of_kind("BROKER_STREAM_ERROR")
    # ...and risk denies any new signal while the broker is unhealthy
    from risk import RiskRequest

    denied = session._risk.evaluate(
        RiskRequest(
            intent_id="disc-1",
            strategy_id="sma",
            symbol="TEST",
            side="BUY",
            quantity=1.0,
            price=100.0,
            timestamp="2026-01-06T10:00:00+00:00",
            broker_healthy=False,
        )
    )
    assert not denied.approved


def test_failure_order_rejection_and_partial() -> None:
    candles = bars_to_candles(make_bars("TEST", 60), "15m")
    venue = SandboxBroker(account_id="pol")
    venue.set_default_policy("reject:risk desk halt")
    session, provider, _ = _sandbox_session(candles, broker=venue)
    assert session.start(("TEST",), "15m", _warmup()).ready
    while not provider.exhausted:
        session.step(time.time())
    assert session.journal.of_kind("ORDER_REJECTED")
    assert not venue.fills
    assert session.ledger.all_positions() == ()


def test_failure_duplicate_intent_blocked() -> None:
    candles = bars_to_candles(make_bars("TEST", 40), "15m")
    session, provider, _ = _sandbox_session(candles)
    assert session.start(("TEST",), "15m", _warmup()).ready
    while not provider.exhausted:
        session.step(time.time())
    intents = [e.payload["intent_id"] for e in session.journal.of_kind("RISK_APPROVED")]
    assert len(intents) == len(set(intents))
    # replaying an approved intent into the engine raises (no double order)
    first = session.journal.of_kind("RISK_APPROVED")[0]
    with pytest.raises(IllegalTransitionError):
        session.engine.create(
            BrokerOrder(
                client_order_id="other-id",
                intent_id=first.payload["intent_id"],
                symbol="TEST",
                side="BUY",
                quantity=1.0,
            )
        )


def test_failure_stale_stream_skips_processing() -> None:
    candles = bars_to_candles(make_bars("TEST", 30), "15m")
    provider = ReplayProvider(candles, chunk_size=5)
    session = LiveSession(SessionConfig(), provider, RiskPolicy())
    session.register_strategy("sma", "1.0", _logic, StrategyParameters({}))
    assert session.start(("TEST",), "15m", _warmup()).ready
    session.step(1000.0)
    assert not session.journal.of_kind("STALE_DATA")
    session.step(10**6)  # clock jumps far past the last observed event
    assert session.journal.of_kind("STALE_DATA")


def test_failure_kill_switch_blocks_and_releases() -> None:
    candles = bars_to_candles(make_bars("TEST", 30), "15m")
    provider = ReplayProvider(candles, chunk_size=5)
    session = LiveSession(SessionConfig(), provider, RiskPolicy())
    session.register_strategy("sma", "1.0", _logic, StrategyParameters({}))
    assert session.start(("TEST",), "15m", _warmup()).ready
    session.step(time.time())
    session.step(time.time())
    session._risk.kill_switch.engage("operator halt")
    assert session._risk.kill_switch.is_halted() is True
    session.step(time.time())
    denied = session.journal.of_kind("RISK_DENIED")
    assert denied
    session._risk.kill_switch.disengage()
    assert not session._risk.kill_switch.is_halted()


def test_failure_reconciliation_mismatch_blocks_live() -> None:
    candles = bars_to_candles(make_bars("TEST", 60), "15m")
    session, provider, venue = _sandbox_session(candles)
    assert session.start(("TEST",), "15m", _warmup()).ready
    while not provider.exhausted:
        session.step(time.time())
    # tamper broker-side: phantom position the ledger never saw
    from execution.models.order import Fill

    venue._fills.append(
        Fill(
            client_order_id="ghost",
            broker_order_id="GHOST-1",
            symbol="TEST",
            side="BUY",
            fill_qty=999.0,
            fill_price=1.0,
            commission=0.0,
            timestamp="t",
        )
    )
    report = session.reconcile_now()
    assert not report.positions.matched
    assert report.blocks_live


def test_failure_restart_recovery_sandbox() -> None:
    candles = bars_to_candles(make_bars("TEST", 40), "15m")
    session, provider, venue = _sandbox_session(candles)
    assert session.start(("TEST",), "15m", _warmup()).ready
    session.step(time.time())
    checkpoint = session.checkpoint()
    session2, _, _ = _sandbox_session(candles)
    session2.recover(checkpoint)
    assert len(session2._contexts["sma:1.0"].bars) == len(checkpoint["windows"]["sma:1.0"])


def test_failure_cancel_and_modify() -> None:
    venue = SandboxBroker(account_id="cm")
    venue.connect()
    from execution.models.order import OrderPlan

    plan = OrderPlan(intent_id="i", symbol="T", side="BUY", quantity=10.0)
    broker_id = venue.place_order(plan, "c1")
    assert venue.modify_order(broker_id, quantity=5.0, price=None) is True
    assert venue.cancel_order(broker_id) is True
    assert venue.open_orders() == []
    assert venue.stream_events()  # ack + cancel drained


def test_mode_separation_no_silent_fallthrough() -> None:
    broker, mode, notes = resolve_broker(ExecutionMode.SANDBOX, ModeGates(), adapter_name="sandbox")
    assert mode == ExecutionMode.SANDBOX and broker.name == "sandbox"
    paper, paper_mode, _ = resolve_broker(ExecutionMode.PAPER, ModeGates())
    assert paper_mode == ExecutionMode.PAPER and paper.name == "paper"
    try:
        resolve_broker(ExecutionMode.SANDBOX, ModeGates(), adapter_name="nope")
        raise AssertionError("expected NotConfiguredError")
    except NotConfiguredError:
        pass
    live_broker, live_mode, live_notes = resolve_broker(ExecutionMode.LIVE, ModeGates())
    assert live_mode == ExecutionMode.PAPER and live_notes  # downgrade reported, never silent
