"""Replay determinism, regime detection and adaptive layer tests."""

from pathlib import Path

from execution.adaptive.attention import AttentionConfig, AttentionFilter
from execution.adaptive.confidence import (
    ConfidenceInput,
    ConfidencePolicy,
    ExecutionPosture,
    ExpectationWindow,
    SurpriseMonitor,
)
from execution.adaptive.memory import Incident, LongTermMemory, WorkingMemory
from execution.events import CandleEvent, OrderBookEvent
from execution.regime import MarketRegime, StatisticalRegimeDetector
from execution.replay import LiveEventRecorder, replay_and_compare
from execution.tests.helpers import make_candles


def _candle(seq: int, close: float = 100.0) -> CandleEvent:
    return CandleEvent(
        symbol="T",
        timestamp="2026-01-06T09:30:00+00:00",
        seq=seq,
        close=close,
        open=close - 1,
        high=close + 1,
        low=close - 2,
        volume=100,
        timeframe="15m",
    )


def test_recorder_roundtrip_and_replay_determinism(tmp_path: Path) -> None:
    events = make_candles(count=10)
    recorder = LiveEventRecorder(tmp_path / "tape.jsonl")
    for event in events:
        recorder.record(event)
    loaded = LiveEventRecorder.load(tmp_path / "tape.jsonl")
    assert len(loaded) == 10
    assert [e.seq for e in loaded] == list(range(1, 11))
    assert isinstance(loaded[0], CandleEvent)

    def run_once(tape) -> tuple[str, ...]:
        return tuple(f"s-{e.seq}:{e.close}" for e in tape)

    report = replay_and_compare(loaded, run_once)
    assert report.matched and report.compared == 10 and report.divergences == ()


def test_replay_reports_divergence() -> None:
    calls = {"n": 0}

    def flaky(tape) -> tuple[str, ...]:
        calls["n"] += 1
        return tuple(f"s-{e.seq}" for e in tape) + (
            (f"extra-{calls['n']}",) if calls["n"] == 1 else ()
        )

    report = replay_and_compare(make_candles(count=3), flaky)
    assert not report.matched
    assert any("length" in d for d in report.divergences)


def test_orderbook_roundtrip(tmp_path: Path) -> None:
    event = OrderBookEvent(
        symbol="T", timestamp="t", seq=1, bids=((100.0, 5.0),), asks=((101.0, 3.0),)
    )
    recorder = LiveEventRecorder(tmp_path / "tape.jsonl")
    recorder.record(event)
    (loaded,) = LiveEventRecorder.load(tmp_path / "tape.jsonl")
    assert isinstance(loaded, OrderBookEvent)
    assert loaded.bids == ((100.0, 5.0),)


def test_regime_detector_progression() -> None:
    detector = StatisticalRegimeDetector(lookback=10)
    assert detector.update(100.0, 1000.0) == MarketRegime.UNKNOWN  # warming
    for i in range(9):
        detector.update(100.0 + i * 0.01, 1000.0)
    calm = detector.update(100.1, 1000.0)
    assert calm in (MarketRegime.RANGE, MarketRegime.LOW_VOLATILITY, MarketRegime.TREND)
    wild = StatisticalRegimeDetector(lookback=10)
    for i in range(11):
        wild.update(100.0 + (20.0 if i % 2 else -20.0), 1000.0)
    assert wild.update(100.0, 1000.0) == MarketRegime.HIGH_VOLATILITY
    dry = StatisticalRegimeDetector(lookback=10)
    for _ in range(11):
        dry.update(100.0, 0.0)
    assert dry.update(100.0, 0.0) == MarketRegime.ILLIQUID


def test_attention_scores_and_sheds_with_counts() -> None:
    filt = AttentionFilter(AttentionConfig(active_symbols=("A",)))
    filt.observe(_candle(1))
    assert filt.processed == 1  # candles always process
    from execution.events import TradeEvent

    tick = TradeEvent(symbol="B", timestamp="t", seq=2, price=100.0, quantity=1.0)
    first = filt.observe(tick, under_load=True)
    assert not first.process  # inactive symbol, no move yet: 0.2 < 0.5 under load
    tick2 = TradeEvent(symbol="B", timestamp="t", seq=3, price=100.0, quantity=1.0)
    shed = filt.observe(tick2, under_load=True)
    assert not shed.process
    assert filt.skipped == 2
    assert any("shed" in reason for reason in shed.reasons)
    active = filt.observe(
        TradeEvent(symbol="A", timestamp="t", seq=4, price=100.0, quantity=1.0),
        under_load=True,
    )
    assert active.process  # active instrument scores above the shed line


def test_working_memory_bounds() -> None:
    memory = WorkingMemory(max_events=5, max_signals=3)
    for i in range(20):
        memory.note_event("CandleEvent", "T", i)
    for i in range(10):
        memory.note_signal(f"s-{i}", "BUY")
    assert len(memory.recent_events) == 5
    assert len(memory.recent_signals) == 3
    assert memory.recent_events[-1]["seq"] == 19
    snapshot = memory.snapshot()
    assert snapshot["recent_events"] == 5


def test_long_term_memory_persists_and_advises(tmp_path: Path) -> None:
    path = tmp_path / "ltm.json"
    memory = LongTermMemory(path)
    memory.save_checkpoint("warmup", {"bars": 20})
    memory.record_incident(Incident(kind="disconnect", detail="hb timeout", timestamp="t"))
    memory.record_broker("paper", {"fills": 3})
    reopened = LongTermMemory(path)
    assert reopened.load_checkpoint("warmup") == {"bars": 20}
    assert len(reopened.incidents) == 1
    advice = reopened.advise()
    assert advice["incident_count"] == 1 and advice["brokers_observed"] == ["paper"]
    assert reopened.load_checkpoint("missing") is None


def test_surprise_and_confidence_postures() -> None:
    monitor = SurpriseMonitor(threshold=1.0)
    assert monitor.observe("spread", 0.02) is None  # warming: blind, no signal
    for _ in range(5):
        monitor.observe("spread", 0.02)
    assert monitor.is_surprising("spread", 0.02) is False
    assert monitor.is_surprising("spread", 0.10) is True
    window = ExpectationWindow()
    assert window.expected is None and window.surprise(1.0) is None

    policy = ConfidencePolicy()
    assert policy.evaluate(ConfidenceInput()) == ExecutionPosture.NORMAL
    assert policy.evaluate(ConfidenceInput(data_stale=True)) == ExecutionPosture.HALT
    assert policy.evaluate(ConfidenceInput(broker_unstable=True)) == ExecutionPosture.HALT
    assert policy.evaluate(ConfidenceInput(spread_surprise=1.5)) == ExecutionPosture.REDUCED
    assert policy.evaluate(ConfidenceInput(spread_surprise=5.0)) == ExecutionPosture.HALT
    normal = policy.preferences(ExecutionPosture.NORMAL)
    assert normal == {"halt": False, "prefer_limit": False, "size_multiplier": 1.0}
    reduced = policy.preferences(ExecutionPosture.REDUCED)
    assert reduced["size_multiplier"] == 0.5 and reduced["prefer_limit"] is True
    halted = policy.preferences(ExecutionPosture.HALT)
    assert halted["halt"] is True and halted["size_multiplier"] == 0.0
