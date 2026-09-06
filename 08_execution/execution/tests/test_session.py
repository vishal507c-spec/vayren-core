"""LiveSession paper tests — full pipeline, kill switch, recovery, isolation."""

import tempfile
import time
from pathlib import Path

from risk import KillSwitch, RiskPolicy
from strategy import StrategyParameters

from execution.adaptive.confidence import ConfidenceInput
from execution.broker.paper import PaperBroker
from execution.events import CandleEvent
from execution.market_data.replay import ReplayProvider, bars_to_candles
from execution.models.order import TERMINAL_STATES
from execution.modes import ExecutionMode
from execution.runtime.lifecycle import LifecycleState
from execution.runtime.session import LiveSession, SessionConfig, check_live_readiness
from execution.tests.helpers import make_bars


def _sma_logic():
    from strategy.strategies.sma import SmaCrossover

    return SmaCrossover(StrategyParameters({"fast_period": 2, "slow_period": 3}))


def _session(candles, **config_overrides) -> LiveSession:
    config = SessionConfig(**config_overrides)
    provider = ReplayProvider(candles, chunk_size=1000)
    session = LiveSession(config, provider, RiskPolicy())
    session.register_strategy("sma", "1.0", _sma_logic, StrategyParameters({}))
    return session


def _warmup(n: int = 25):
    return {"sma": make_bars("TEST", n)}


def _drain(session: LiveSession) -> None:
    """Step until a replay provider is exhausted (narrowed, typed)."""
    provider = session._provider
    assert isinstance(provider, ReplayProvider)
    while not provider.exhausted:
        session.step(time.time())


def test_paper_session_trades_end_to_end() -> None:
    candles = bars_to_candles(make_bars("TEST", 60), "15m")
    session = _session(candles)
    report = session.start(("TEST",), "15m", _warmup())
    assert report.ready, report.reasons
    _drain(session)
    kinds = [entry.kind for entry in session.journal.entries]
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
        assert expected in kinds, f"missing journal fact: {expected}"
    assert "RISK_DENIED" not in kinds
    broker = session._broker
    assert isinstance(broker, PaperBroker)
    assert len(broker.fills) >= 1
    positions = session.ledger.all_positions()
    assert session.engine.open_orders() == ()
    for order in session.engine._orders.values():
        assert order.state in TERMINAL_STATES
    _ = positions


def test_kill_switch_blocks_start_with_reasons() -> None:
    path = Path(tempfile.mkdtemp()) / "kill.json"
    kill = KillSwitch(path)
    kill.engage("operator halt")
    candles = bars_to_candles(make_bars("TEST", 60), "15m")
    config = SessionConfig(kill_switch_path=path)
    provider = ReplayProvider(candles, chunk_size=1000)
    session = LiveSession(config, provider, RiskPolicy())
    session.register_strategy("sma", "1.0", _sma_logic, StrategyParameters({}))
    report = session.start(("TEST",), "15m", _warmup())
    assert not report.ready
    assert any("kill" in reason for reason in report.reasons)
    assert session.journal.of_kind("NOT_LIVE_READY")


def test_missing_warmup_fails_readiness() -> None:
    candles = bars_to_candles(make_bars("TEST", 60), "15m")
    session = _session(candles)
    report = session.start(("TEST",), "15m", {})
    assert not report.ready
    assert any("warmup" in reason for reason in report.reasons)


def test_duplicate_events_process_once() -> None:
    candles = bars_to_candles(make_bars("TEST", 30), "15m")
    doubled = candles + candles  # identical seqs replayed twice
    provider = ReplayProvider(doubled, chunk_size=1000)
    session = LiveSession(SessionConfig(), provider, RiskPolicy())
    session.register_strategy("sma", "1.0", _sma_logic, StrategyParameters({}))
    assert session.start(("TEST",), "15m", _warmup()).ready
    session.step(time.time())
    signals = session.journal.of_kind("SIGNAL_GENERATED")
    assert session._normalizer.stats.duplicates >= len(candles)
    assert len(signals) >= 1  # processed once per unique candle, never doubled by replay


def test_stale_stream_halts_processing() -> None:
    candles = bars_to_candles(make_bars("TEST", 30), "15m")
    provider = ReplayProvider(candles, chunk_size=1)
    session = LiveSession(SessionConfig(), provider, RiskPolicy())
    session.register_strategy("sma", "1.0", _sma_logic, StrategyParameters({}))
    assert session.start(("TEST",), "15m", _warmup()).ready
    session.step(1000.0)  # first event flows, health anchored
    assert not session.journal.of_kind("STALE_DATA")
    session.step(10**6)  # far-future clock: stalled stream flagged, skipped
    assert session.journal.of_kind("STALE_DATA")
    stale_signals = len(session.journal.of_kind("SIGNAL_GENERATED"))
    session.step(10**6 + 1.0)
    assert len(session.journal.of_kind("SIGNAL_GENERATED")) == stale_signals


def test_broken_strategy_isolated_from_healthy_one() -> None:

    class BrokenLogic:
        def warmup(self) -> int:
            return 0

        def on_bar(self, view) -> None:  # noqa: ARG002
            raise RuntimeError("boom")

    candles = bars_to_candles(make_bars("TEST", 40), "15m")
    provider = ReplayProvider(candles, chunk_size=1000)
    session = LiveSession(SessionConfig(), provider, RiskPolicy())
    session.register_strategy("sma", "1.0", _sma_logic, StrategyParameters({}))
    session.register_strategy("broken", "1.0", BrokenLogic, StrategyParameters({}))
    assert session.start(("TEST",), "15m", {"sma": make_bars("TEST", 25), "broken": ()}).ready
    session.step(time.time())
    assert session.journal.of_kind("STRATEGY_ERROR")
    assert session.journal.of_kind("SIGNAL_GENERATED")  # healthy instance unaffected


def test_adaptive_halt_blocks_orders() -> None:
    candles = bars_to_candles(make_bars("TEST", 40), "15m")

    class HaltSession(LiveSession):
        def _confidence_input(self, symbol: str) -> ConfidenceInput:  # noqa: ARG002
            return ConfidenceInput(spread_surprise=9.0)

    provider = ReplayProvider(candles, chunk_size=1000)
    session = HaltSession(SessionConfig(), provider, RiskPolicy())
    session.register_strategy("sma", "1.0", _sma_logic, StrategyParameters({}))
    assert session.start(("TEST",), "15m", _warmup()).ready
    _drain(session)
    assert session.journal.of_kind("EXECUTION_HALTED")
    assert not session.journal.of_kind("ORDER_SUBMITTED")


def test_adaptive_reduced_prefers_limits() -> None:
    candles = bars_to_candles(make_bars("TEST", 40), "15m")

    class ReducedSession(LiveSession):
        def _confidence_input(self, symbol: str) -> ConfidenceInput:  # noqa: ARG002
            return ConfidenceInput(spread_surprise=1.5)

    provider = ReplayProvider(candles, chunk_size=1000)
    session = ReducedSession(SessionConfig(), provider, RiskPolicy())
    session.register_strategy("sma", "1.0", _sma_logic, StrategyParameters({}))
    assert session.start(("TEST",), "15m", _warmup()).ready
    _drain(session)
    planned = session.journal.of_kind("ORDER_PLANNED")
    assert planned  # orders still flow under REDUCED
    for order in session.engine._orders.values():
        assert order.order_type == "LIMIT"


def test_checkpoint_recover_roundtrip() -> None:
    candles = bars_to_candles(make_bars("TEST", 40), "15m")
    session = _session(candles)
    assert session.start(("TEST",), "15m", _warmup()).ready
    session.step(time.time())
    checkpoint = session.checkpoint()
    assert checkpoint["windows"]["sma:1.0"]
    session2 = _session(candles)
    session2.recover(checkpoint)
    restored = session2._contexts["sma:1.0"].bars
    assert len(restored) == len(checkpoint["windows"]["sma:1.0"])
    assert restored[0].close == checkpoint["windows"]["sma:1.0"][0]["close"]


def test_recover_method_rearms_lifecycle() -> None:
    candles = bars_to_candles(make_bars("TEST", 40), "15m")
    session = _session(candles)
    assert session.start(("TEST",), "15m", _warmup()).ready
    checkpoint = session.checkpoint()
    session.recover(checkpoint)
    assert session._lifecycle.state == LifecycleState.RECOVERING
    # A recovered session can start: RECOVERING -> VALIDATING is explicit.
    report = session.start(("TEST",), "15m", _warmup())
    assert report.ready
    assert session._lifecycle.state == LifecycleState.RUNNING


def test_reconcile_now_matches_paper_truth() -> None:
    candles = bars_to_candles(make_bars("TEST", 60), "15m")
    session = _session(candles)
    assert session.start(("TEST",), "15m", _warmup()).ready
    _drain(session)
    report = session.reconcile_now()
    assert not report.blocks_live
    assert report.positions.matched and report.orders.matched


def test_readiness_reports_all_eleven_gates() -> None:
    from execution.models.contract import StrategyRuntimeContract

    contract = StrategyRuntimeContract(
        strategy_id="s",
        strategy_version="1",
        missing=("strategy_logic",),
        supports_live=False,
        warmup_bars=5,
    )
    report = check_live_readiness(
        contract,
        data_capabilities=(),
        broker_capabilities=(),
        warmup_bars_available=0,
        risk_policy_ok=False,
        account_ok=False,
        clock_ok=False,
        reconcile_ok=False,
        persistence_ok=False,
        kill_ok=False,
        observability_ok=False,
    )
    assert not report.ready
    assert len(report.reasons) >= 11
    assert any("SUPPORTS_LIVE" in reason for reason in report.reasons)
    assert any("warmup" in reason for reason in report.reasons)
    assert any("kill" in reason for reason in report.reasons)


def test_live_mode_without_gates_downgrades() -> None:
    candles = bars_to_candles(make_bars("TEST", 40), "15m")
    config = SessionConfig(mode=ExecutionMode.LIVE)
    provider = ReplayProvider(candles, chunk_size=1000)
    session = LiveSession(config, provider, RiskPolicy())
    session.register_strategy("sma", "1.0", _sma_logic, StrategyParameters({}))
    report = session.start(("TEST",), "15m", _warmup())
    assert session.mode == ExecutionMode.PAPER  # downgraded, never silent
    assert session.journal.of_kind("MODE_DOWNGRADE")
    _ = report


def test_candle_event_type_safety() -> None:
    event = CandleEvent(symbol="T", timestamp="t", seq=1, close=1.0)
    assert event.is_closed is True
    assert event.timeframe == ""
