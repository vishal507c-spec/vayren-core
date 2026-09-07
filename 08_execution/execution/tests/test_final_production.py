"""FINAL production tests: idempotency survival, verdict reconciliation,
policies, funds mapping, activation ceremony, journal chain (FINAL §H/§K–§M,
§T/§V — no future phases)."""

from __future__ import annotations

import time

import pytest
from broker.funds import FundsSnapshot
from risk import RiskPolicy
from strategy import StrategyParameters

from execution.broker.activation import evaluate_activation, record_activation
from execution.broker.gates import (
    LiveGatesReport,
    funds_snapshot_from_face,
    funds_valid_for_live,
    risk_capital_from_funds,
)
from execution.broker.paper import PaperBroker
from execution.broker.resilience import BackoffPolicy, ReconnectPolicy, TimeoutPolicy
from execution.engine import ExecutionEngine, IllegalTransitionError
from execution.journal import ExecutionJournal
from execution.market_data.normalizer import StreamNormalizer
from execution.market_data.provider import MarketDataError
from execution.market_data.replay import ReplayProvider, bars_to_candles
from execution.models.order import BrokerOrder, OrderPlan, OrderState
from execution.models.position import AccountSnapshot, Position
from execution.modes import LiveArm
from execution.portfolio.reconcile import (
    ReconcileStatus,
    ReconciliationState,
    reconcile_funds,
    reconcile_orders,
    reconcile_positions,
    record_verdict,
    verdict_of,
)
from execution.runtime.session import LiveSession, SessionConfig
from execution.tests.helpers import make_bars


def _sma_logic():
    from strategy.strategies.sma import SmaCrossover

    return SmaCrossover(StrategyParameters({"fast_period": 2, "slow_period": 3}))


def _session(candles, **overrides) -> LiveSession:
    config = SessionConfig(**overrides)
    provider = ReplayProvider(candles, chunk_size=1000)
    session = LiveSession(config, provider, RiskPolicy())
    session.register_strategy("sma", "1.0", _sma_logic, StrategyParameters({}))
    return session


def _warmup(n: int = 25):
    return {"sma": make_bars("TEST", n)}


def _drain(session: LiveSession) -> None:
    provider = session._provider
    assert isinstance(provider, ReplayProvider)
    while not provider.exhausted:
        session.step(time.time())


# ── §K: engine snapshot / restore ────────────────────────────────────────


def test_engine_snapshot_restore_roundtrip() -> None:
    engine = ExecutionEngine()
    order = BrokerOrder(client_order_id="k1", intent_id="ki1", symbol="X", side="BUY", quantity=2.0)
    engine.create(order)
    engine.transition("k1", OrderState.VALIDATED)
    engine.transition("k1", OrderState.SUBMITTED)
    snap = engine.snapshot()
    assert snap["orders"][0]["state"] == "SUBMITTED"
    fresh = ExecutionEngine()
    fresh.restore(snap)
    assert fresh.get("k1") is not None
    assert fresh.by_intent("ki1") is not None
    with pytest.raises(IllegalTransitionError):
        fresh.create(order)  # duplicates still denied after restore


def test_engine_restore_rejects_corrupt_snapshot() -> None:
    engine = ExecutionEngine()
    with pytest.raises(IllegalTransitionError):
        engine.restore({"orders": "nope"})
    with pytest.raises(IllegalTransitionError):
        engine.restore({"orders": [{"client_order_id": "x"}]})
    with pytest.raises(IllegalTransitionError):
        engine.restore(
            {
                "orders": [
                    {
                        "client_order_id": "a",
                        "intent_id": "i",
                        "symbol": "X",
                        "side": "BUY",
                        "quantity": 1.0,
                        "state": "BOGUS",
                    },
                    {
                        "client_order_id": "a",
                        "intent_id": "i",
                        "symbol": "X",
                        "side": "BUY",
                        "quantity": 1.0,
                        "state": "CREATED",
                    },
                ]
            }
        )


def test_restored_unknown_still_needs_reconcile() -> None:
    engine = ExecutionEngine()
    engine.create(
        BrokerOrder(client_order_id="u1", intent_id="ui1", symbol="X", side="BUY", quantity=1.0)
    )
    engine.transition("u1", OrderState.VALIDATED)
    engine.transition("u1", OrderState.SUBMITTED)
    engine.transition("u1", OrderState.UNKNOWN, reason="timeout")
    fresh = ExecutionEngine()
    fresh.restore(engine.snapshot())
    with pytest.raises(IllegalTransitionError):
        fresh.transition("u1", OrderState.FILLED)
    resolved = fresh.reconcile("u1", "ACKNOWLEDGED")
    assert resolved.state is OrderState.ACKNOWLEDGED


def test_session_checkpoint_recovers_idempotency() -> None:
    candles = bars_to_candles(make_bars("TEST", 60), "15m")
    session = _session(candles)
    assert session.start(("TEST",), "15m", _warmup()).ready
    _drain(session)
    checkpoint = session.checkpoint()
    assert checkpoint["engine"]["orders"], "drained session must track orders"
    session2 = _session(candles)
    session2.recover(checkpoint)
    restored = session2._engine.snapshot()["orders"]
    assert len(restored) == len(checkpoint["engine"]["orders"])
    kinds = [entry.kind for entry in session2.journal.entries]
    assert "IDEMPOTENCY_RESTORED" in kinds
    # Recovered session reconciles through RECONCILING, then runs.
    report = session2.start(("TEST",), "15m", _warmup())
    assert report.ready
    assert session2.journal.of_kind("RECONCILED")


def test_legacy_checkpoint_without_engine_still_recovers() -> None:
    candles = bars_to_candles(make_bars("TEST", 40), "15m")
    session = _session(candles)
    legacy = {"windows": {}, "orders_today": 0, "last_order_epoch": None}
    session.recover(legacy)  # no "engine" key: backward compatible
    assert session._engine.snapshot() == {"orders": []}
    assert session.start(("TEST",), "15m", _warmup()).ready


# ── §L: verdict reconciliation ───────────────────────────────────────────


def test_verdict_safe_warning_blocked() -> None:
    positions = reconcile_positions((), [])
    orders = reconcile_orders((), ())
    calm = verdict_of((positions, orders))
    assert calm.status is ReconcileStatus.SAFE and not calm.blocks_live
    assert verdict_of((), evaluated=False).status is ReconcileStatus.WARNING
    assert verdict_of((), evaluated=False).blocks_live
    bad_positions = reconcile_positions((Position(symbol="X", quantity=5.0),), [])
    blocked = verdict_of((bad_positions, orders))
    assert blocked.status is ReconcileStatus.BLOCKED and blocked.blocks_live
    assert "position X" in blocked.reasons[0]


def test_reconcile_funds_match_mismatch_unknown() -> None:
    assert reconcile_funds(100.0, {"equity": 100.0}).matched
    mismatch = reconcile_funds(100.0, {"equity": 90.0})
    assert not mismatch.matched and mismatch.mismatches[0].kind == "funds"
    unknown = reconcile_funds(100.0, {})
    assert not unknown.matched and unknown.mismatches[0].broker == "unknown"


def test_state_verdict_and_journaling() -> None:
    state = ReconciliationState()
    assert state.verdict().status is ReconcileStatus.SAFE
    assert state.verdict(evaluated=False).status is ReconcileStatus.WARNING
    journal = ExecutionJournal()
    record_verdict(journal, state.verdict())
    entries = journal.of_kind("RECONCILED")
    assert len(entries) == 1 and entries[0].payload["status"] == "SAFE"


# ── §M: policies ─────────────────────────────────────────────────────────


def test_timeout_policy_validation() -> None:
    policy = TimeoutPolicy()
    assert policy.submit_seconds == 10.0
    with pytest.raises(ValueError):
        TimeoutPolicy(read_seconds=0.0)


def test_backoff_bounded_and_deterministic() -> None:
    policy = BackoffPolicy(base_seconds=1.0, factor=2.0, max_seconds=5.0, max_attempts=3)
    assert policy.delay(1) == 1.0
    assert policy.delay(2) == 2.0
    assert policy.delay(9) == 5.0  # capped, never a storm
    assert policy.delay(1) == policy.delay(1)  # deterministic, no jitter
    assert policy.exhausted(2) is False and policy.exhausted(3) is True
    with pytest.raises(ValueError):
        BackoffPolicy(max_attempts=0)


def test_reconnect_policy_bounded() -> None:
    policy = ReconnectPolicy(max_attempts=3)
    assert policy.exhausted(2) is False and policy.exhausted(3) is True
    with pytest.raises(ValueError):
        ReconnectPolicy(max_attempts=0)


# ── §H: funds mapping + account identity ─────────────────────────────────


def test_funds_to_risk_mapping_and_validation() -> None:
    broker = PaperBroker()
    broker.connect()
    snapshot = funds_snapshot_from_face(broker)
    available, equity = risk_capital_from_funds(snapshot)
    assert available == snapshot.available and equity == snapshot.equity
    ok, _ = funds_valid_for_live(snapshot)
    assert ok is True
    broke = FundsSnapshot(available=0.0, used=0.0, equity=0.0)
    ok, reasons = funds_valid_for_live(broke)
    assert ok is False and len(reasons) == 2  # zero can never authorize LIVE


def test_account_snapshot_carries_identity() -> None:
    snap = AccountSnapshot(equity=1.0, available_capital=1.0)
    assert snap.account_id == "" and snap.environment == ""  # ledger default
    identified = AccountSnapshot(
        equity=1.0, available_capital=1.0, account_id="a1", environment="sandbox"
    )
    assert identified.account_id == "a1"


# ── §T: activation ceremony ──────────────────────────────────────────────


def test_ceremony_all_false_blocks_with_reasons() -> None:
    report = evaluate_activation()
    assert report.ready is False
    assert len(report.steps) == 12
    assert [step.index for step in report.steps] == list(range(1, 13))
    assert report.steps[-1].ok is False  # START never auto-ok
    assert report.blockers, "every failure must name its blocker"


def test_ceremony_green_requires_explicit_arm() -> None:
    base: dict = {
        "broker_name": "sandbox",
        "environment": "sandbox",
        "credentials_ok": True,
        "account_ok": True,
        "market_healthy": True,
        "funds_ok": True,
        "risk_ok": True,
        "verdict": verdict_of((reconcile_positions((), []), reconcile_orders((), ()))),
        "kill_halted": False,
        "gates": LiveGatesReport(ready=True, gates=()),
    }
    disarmed = evaluate_activation(**base)
    assert disarmed.ready is False  # sandbox env + DISARMED block
    live = evaluate_activation(**{**base, "environment": "live", "armed": LiveArm.ARMED})
    assert live.ready is True
    assert live.steps[-1].ok is False  # start stays an operator act
    journal = ExecutionJournal()
    record_activation(journal, live)
    entries = journal.of_kind("ACTIVATION_EVALUATED")
    assert len(entries) == 1 and entries[0].payload["ready"] is True


# ── §F/§V: malformed guard + journal causal chain ────────────────────────


def test_normalizer_rejects_malformed_events() -> None:
    normalizer = StreamNormalizer()
    with pytest.raises(MarketDataError):
        normalizer.observe(object(), 1000.0)  # type: ignore[arg-type]


def test_journal_causal_chain_carries_ids() -> None:
    candles = bars_to_candles(make_bars("TEST", 60), "15m")
    session = _session(candles)
    assert session.start(("TEST",), "15m", _warmup()).ready
    _drain(session)
    submitted = session.journal.of_kind("ORDER_SUBMITTED")
    assert submitted, "expected submissions"
    for entry in submitted:
        assert entry.payload["broker_order_id"]
        assert entry.payload["broker"] == "paper"
        assert entry.payload["environment"] == "paper"
    fills = session.journal.of_kind("FILL")
    assert fills
    for entry in fills:
        assert entry.payload["intent_id"] and entry.payload["broker_order_id"]
    acks = session.journal.of_kind("ORDER_ACK")
    assert acks and all(e.payload["broker_order_id"] for e in acks)


def test_replay_full_lifecycle_methods() -> None:
    provider = ReplayProvider(bars_to_candles(make_bars("T", 3), "15m"))
    provider.connect()
    provider.open(("T",), "15m")
    provider.subscribe(("T",))
    assert provider.poll() != ()
    provider.disconnect()
    assert provider.poll() == ()
    provider.reconnect()
    assert provider.health()[0] is True
    provider.unsubscribe(("T",))
    assert provider._subscribed == ()
    provider.close()
    assert provider.poll() == ()


def test_plan_model_unchanged() -> None:
    plan = OrderPlan(intent_id="i", symbol="X", side="BUY", quantity=1.0)
    assert plan.order_type == "MARKET" and plan.time_in_force == "DAY"
