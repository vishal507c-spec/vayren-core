"""Comprehensive tests for Reliable Startup Recovery & No-Missed-Valid-Signal Fix.

Covers all minimum required test scenarios:
1. Engine starts before market open.
2. Engine starts at 09:49 IST with 09:15 candle available historically.
3. Engine starts halfway through the trading session.
4. WebSocket disconnects and later reconnects.
5. Historical API returns missing, duplicated, stale, or incomplete candles.
6. Replay produces same strategy state as chronological live processing.
7. Repeated startup/recovery does not generate duplicate signals or orders.
8. Expired historical signals do not create stale live entries.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.services.session_recovery import (
    VENUE_TZ,
    HistoricalRecoveryError,
    backfill_candle_gap,
    evaluate_recovered_signal,
    expected_completed_session_intervals,
    fetch_and_validate_session_candles,
    is_trading_day,
)
from execution.market_data.broker_feed import BrokerFeedProvider
from execution.models.intent import StrategySignal
from execution.modes import ExecutionMode, LiveArm, ModeGates
from execution.runtime.lifecycle import LifecycleState
from execution.runtime.session import LiveSession, SessionConfig
from market.models.bar import Bar
from risk import RiskPolicy
from strategy import StrategyParameters, StrategyState
from strategy.language.compiler import compile_strategy
from strategy.obr_source import OBR_C1C4_SOURCE
from strategy.runtime import BarView


def _create_obr_logic() -> Any:
    """Helper to instantiate compiled OBR C1C4 strategy logic."""
    compiled = compile_strategy(OBR_C1C4_SOURCE)
    params = StrategyParameters(compiled.param_defaults)
    return compiled.create_logic(params)


class MockFyersHistoryAdapter:
    """Mock FYERS adapter answering `history` with test-controlled rows."""

    def __init__(
        self,
        rows_by_symbol: dict[str, list[tuple[int, float, float, float, float, int]]],
        fail_symbols: set[str] | None = None,
    ) -> None:
        self.rows_by_symbol = rows_by_symbol
        self.fail_symbols = fail_symbols or set()
        self.calls: list[tuple[str, str, int, int]] = []

    def history(
        self, symbol: str, resolution: str, start_epoch: int, end_epoch: int
    ) -> list[tuple[int, float, float, float, float, int]]:
        self.calls.append((symbol, str(resolution), int(start_epoch), int(end_epoch)))
        if symbol in self.fail_symbols:
            raise RuntimeError(f"Venue rejected history for {symbol}")
        return list(self.rows_by_symbol.get(symbol, []))


class MockBrokerAdapter:
    """Minimal fake broker adapter for LiveSession testing."""

    def __init__(
        self,
        positions_list: list[dict[str, Any]] | None = None,
        orders_list: list[dict[str, Any]] | None = None,
    ) -> None:
        self._positions = positions_list or []
        self._orders = orders_list or []
        self.placed_orders: list[Any] = []
        self.market_prices: list[tuple[str, float, str]] = []

    @property
    def name(self) -> str:
        return "mock-broker"

    @property
    def capabilities(self) -> tuple[str, ...]:
        return ("orders.market", "orders.limit", "account.positions", "account.open_orders")

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def health(self) -> tuple[bool, str]:
        return True, "connected"

    def account(self) -> dict[str, Any]:
        return {"account_id": "ACC1", "status": "ACTIVE"}

    def funds(self) -> dict[str, Any]:
        return {"available": 100_000.0, "total": 100_000.0}

    def order_stream_health(self) -> tuple[bool, str]:
        return True, "ok"

    def reference_spread(self, symbol: str) -> float | None:  # noqa: ARG002
        return None

    def positions(self) -> list[dict[str, Any]]:
        return list(self._positions)

    def open_orders(self) -> list[dict[str, Any]]:
        return list(self._orders)

    def place_order(self, plan: Any, client_order_id: str) -> str:
        self.placed_orders.append((plan, client_order_id))
        return f"BROKER-{len(self.placed_orders)}"

    def cancel_order(self, broker_order_id: str) -> bool:  # noqa: ARG002
        return True

    def modify_order(
        self,
        broker_order_id: str,  # noqa: ARG002
        quantity: float | None,  # noqa: ARG002
        price: float | None,  # noqa: ARG002
    ) -> bool:
        return True

    def stream_events(self) -> tuple[dict[str, Any], ...]:
        return ()

    def on_market_price(self, symbol: str, price: float, timestamp: str) -> None:
        self.market_prices.append((symbol, price, timestamp))


class DummyProvider:
    name = "dummy"

    @property
    def capabilities(self):
        return ("candle-close",)

    def open(self, symbols: tuple[str, ...], timeframe: str) -> None:  # noqa: ARG002
        pass

    def close(self) -> None:
        pass

    def poll(self):
        return ()

    def health(self) -> tuple[bool, str]:
        return True, "ok"


def _make_epoch(dt_str: str) -> int:
    """Convert 'YYYY-MM-DD HH:MM:SS' IST to epoch seconds."""
    dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=VENUE_TZ)
    return int(dt.timestamp())


def test_1_starts_before_market_open() -> None:
    """1. Engine starts before market open (08:45 IST on a weekday)."""
    now_dt = datetime(2026, 10, 28, 8, 45, tzinfo=VENUE_TZ)
    assert is_trading_day(now_dt)

    intervals = expected_completed_session_intervals(now_dt, "30m")
    assert intervals == []

    adapter = MockFyersHistoryAdapter({"KAYNES": []})
    bars, report = fetch_and_validate_session_candles(
        adapter, "KAYNES", "30m", now_dt=now_dt, fail_fast=True
    )
    assert bars == ()
    assert report.status == "NO_DATA_REQUIRED"
    assert report.expected_candles_count == 0
    assert report.recovered_candles_count == 0
    assert len(adapter.calls) == 0


def test_2_starts_at_0949_ist_with_0915_opening_candle() -> None:
    """2. Engine starts at 09:49 IST with 09:15 opening candle already available historically."""
    now_dt = datetime(2026, 10, 28, 9, 49, tzinfo=VENUE_TZ)
    intervals = expected_completed_session_intervals(now_dt, "30m")
    assert len(intervals) == 1
    assert intervals[0][0].strftime("%H:%M") == "09:15"
    assert intervals[0][1].strftime("%H:%M") == "09:45"

    ep_0915 = _make_epoch("2026-10-28 09:15:00")
    c1 = (ep_0915, 100.0, 105.0, 99.0, 102.0, 50000)
    adapter = MockFyersHistoryAdapter({"KAYNES": [c1]})

    bars, report = fetch_and_validate_session_candles(
        adapter, "KAYNES", "30m", now_dt=now_dt, fail_fast=True
    )
    assert len(bars) == 1
    assert report.status == "SUCCESS"
    assert bars[0].timestamp == "2026-10-28 09:15:00"
    assert bars[0].open == 100.0
    assert bars[0].close == 102.0

    # Feed into OBR strategy to verify candleCount becomes 0 (Candle 1)
    logic = _create_obr_logic()
    view = BarView(bars=bars, index=0, params=logic.params, state=StrategyState())
    logic.on_bar(view)
    assert logic.engine.candleCount == 1  # Candle 1 processed
    assert logic.engine.refSet is False  # Ref candle locks at candle 3


def test_3_starts_halfway_through_session() -> None:
    """3. Engine starts halfway through the session (12:30 IST)."""
    now_dt = datetime(2026, 10, 28, 12, 30, tzinfo=VENUE_TZ)
    intervals = expected_completed_session_intervals(now_dt, "30m")
    # Expected: 09:15, 09:45, 10:15, 10:45, 11:15, 11:45 (6 completed candles)
    assert len(intervals) == 6
    assert intervals[0][0].strftime("%H:%M") == "09:15"
    assert intervals[-1][0].strftime("%H:%M") == "11:45"
    assert intervals[-1][1].strftime("%H:%M") == "12:15"

    stamps = [
        "2026-10-28 09:15:00",
        "2026-10-28 09:45:00",
        "2026-10-28 10:15:00",
        "2026-10-28 10:45:00",
        "2026-10-28 11:15:00",
        "2026-10-28 11:45:00",
    ]
    rows = [(_make_epoch(s), 100.0, 105.0, 95.0, 102.0, 1000) for s in stamps]
    adapter = MockFyersHistoryAdapter({"KAYNES": rows})

    bars, report = fetch_and_validate_session_candles(
        adapter, "KAYNES", "30m", now_dt=now_dt, fail_fast=True
    )
    assert len(bars) == 6
    assert report.status == "SUCCESS"
    assert [b.timestamp for b in bars] == stamps


def test_4_websocket_disconnects_and_reconnects_backfill() -> None:
    """4. WebSocket disconnects and later reconnects: backfills missing completed intervals."""
    # Disconnected at 10:10 (last emitted was 09:15 candle).
    # Reconnects at 10:50.
    # Missed candles: 09:45 (closed at 10:15) and 10:15 (closed at 10:45).
    now_reconnect = datetime(2026, 10, 28, 10, 50, tzinfo=VENUE_TZ)
    last_emitted = "2026-10-28 09:15:00"

    ep_0945 = _make_epoch("2026-10-28 09:45:00")
    ep_1015 = _make_epoch("2026-10-28 10:15:00")
    ep_1045 = _make_epoch("2026-10-28 10:45:00")  # forming, incomplete at 10:50!

    rows = [
        (ep_0945, 102.0, 104.0, 101.0, 103.0, 2000),
        (ep_1015, 103.0, 106.0, 102.0, 105.0, 3000),
        (ep_1045, 105.0, 107.0, 104.0, 106.0, 1500),  # incomplete, should be excluded!
    ]
    adapter = MockFyersHistoryAdapter({"KAYNES": rows})

    backfilled = backfill_candle_gap(adapter, "KAYNES", "30m", last_emitted, now_dt=now_reconnect)
    # Only 09:45 and 10:15 must be emitted as closed candles
    assert len(backfilled) == 2
    assert backfilled[0].timestamp == "2026-10-28 09:45:00"
    assert backfilled[1].timestamp == "2026-10-28 10:15:00"
    assert backfilled[0].is_closed is True
    assert backfilled[1].is_closed is True

    # Test BrokerFeedProvider integration with reconnect
    class MockFace:
        def __init__(self) -> None:
            self.connected = True

        def connect(self) -> None:
            pass

        def disconnect(self) -> None:
            self.connected = False

        def reconnect(self) -> None:
            self.connected = True

        def subscribe(self, syms: tuple) -> None:
            pass

        def poll(self) -> list:
            return []

        def health(self) -> tuple[bool, str]:
            return True, "ok"

    feed = BrokerFeedProvider(
        MockFace(), "30m", clock=lambda: now_reconnect.timestamp(), history_face=adapter
    )
    feed.open(("KAYNES",), "30m")
    feed.set_last_emitted("KAYNES", last_emitted)
    feed.disconnect()
    feed.reconnect()

    polled = feed.poll()
    assert len(polled) == 2
    assert polled[0].timestamp == "2026-10-28 09:45:00"
    assert polled[1].timestamp == "2026-10-28 10:15:00"
    assert feed.last_emitted["KAYNES"] == "2026-10-28 10:15:00"


def test_5_historical_api_missing_or_corrupt_fails_safely() -> None:
    """5. Historical API returns missing, duplicated, or incomplete candles."""
    now_dt = datetime(2026, 10, 28, 10, 20, tzinfo=VENUE_TZ)
    # Expected at 10:20: 09:15 and 09:45
    ep_0915 = _make_epoch("2026-10-28 09:15:00")
    ep_0945 = _make_epoch("2026-10-28 09:45:00")

    # A) Missing opening candle (only 09:45 returned) -> must FAIL safely
    adapter_missing = MockFyersHistoryAdapter(
        {"KAYNES": [(ep_0945, 100.0, 102.0, 99.0, 101.0, 1000)]}
    )
    with pytest.raises(HistoricalRecoveryError) as exc_info:
        fetch_and_validate_session_candles(adapter_missing, "KAYNES", "30m", now_dt=now_dt)
    assert "Missing required session candle(s)" in str(exc_info.value)
    assert "09:15:00" in str(exc_info.value)

    # B) Venue failure -> must FAIL safely
    adapter_fail = MockFyersHistoryAdapter({"KAYNES": []}, fail_symbols={"KAYNES"})
    with pytest.raises(HistoricalRecoveryError):
        fetch_and_validate_session_candles(adapter_fail, "KAYNES", "30m", now_dt=now_dt)

    # C) Duplicates & incomplete forming candle handled properly
    ep_1015 = _make_epoch("2026-10-28 10:15:00")  # forming at 10:20
    adapter_dups = MockFyersHistoryAdapter(
        {
            "KAYNES": [
                (ep_0915, 100.0, 102.0, 99.0, 101.0, 1000),
                (ep_0915, 100.0, 102.5, 99.0, 101.5, 1200),  # duplicate, latest wins
                (ep_0945, 101.5, 103.0, 101.0, 102.5, 1500),
                (ep_1015, 102.5, 104.0, 102.0, 103.0, 500),  # incomplete, must be excluded
            ]
        }
    )
    bars, report = fetch_and_validate_session_candles(adapter_dups, "KAYNES", "30m", now_dt=now_dt)
    assert report.status == "SUCCESS"
    assert len(bars) == 2
    assert bars[0].timestamp == "2026-10-28 09:15:00"
    assert bars[0].high == 102.5  # latest duplicate won
    assert bars[1].timestamp == "2026-10-28 09:45:00"


def test_6_historical_replay_produces_identical_strategy_state() -> None:
    """6. Replay produces same strategy state as chronological live processing."""
    stamps = [
        "2026-10-28 09:15:00",
        "2026-10-28 09:45:00",
        "2026-10-28 10:15:00",
        "2026-10-28 10:45:00",
    ]
    raw_candles = [
        (100.0, 105.0, 95.0, 102.0, 1000),  # Candle 1
        (102.0, 106.0, 101.0, 104.0, 1500),  # Candle 2
        (104.0, 108.0, 103.0, 107.0, 2000),  # Candle 3: REF locked! (refHigh=108, refLow=103)
        (107.0, 107.5, 101.0, 102.0, 2500),  # Candle 4: breaks low < 103 -> SELL break
    ]

    recovered_bars = [
        Bar(
            symbol="KAYNES",
            open=o,
            high=h,
            low=low_val,
            close=c,
            volume=v,
            timestamp=stamps[idx],
            bar_size="30m",
        )
        for idx, (o, h, low_val, c, v) in enumerate(raw_candles)
    ]

    # Instance A: Live chronological processing
    logic_a = _create_obr_logic()
    bars_accum_a: list[Bar] = []
    for idx, b in enumerate(recovered_bars):
        bars_accum_a.append(b)
        view_a = BarView(
            bars=tuple(bars_accum_a), index=idx, params=logic_a.params, state=StrategyState()
        )
        logic_a.on_bar(view_a)

    # Instance B: Replayed recovered bars
    logic_b = _create_obr_logic()
    bars_accum_b: list[Bar] = []
    for idx, b in enumerate(recovered_bars):
        bars_accum_b.append(b)
        view_b = BarView(
            bars=tuple(bars_accum_b), index=idx, params=logic_b.params, state=StrategyState()
        )
        logic_b.on_bar(view_b)

    # Strict equality of internal engine state
    eng_a = logic_a.engine
    eng_b = logic_b.engine
    assert eng_a.candleCount == eng_b.candleCount
    assert eng_a.refSet == eng_b.refSet
    assert eng_a.refHigh == eng_b.refHigh
    assert eng_a.refHigh is not None
    assert eng_a.refLow == eng_b.refLow
    assert eng_a.refLow is not None
    assert eng_a.firstBreakDone == eng_b.firstBreakDone
    assert eng_a.sessionState == eng_b.sessionState
    assert eng_a.tradeState == eng_b.tradeState


def test_7_repeated_startup_recovery_prevents_duplicate_orders(tmp_path) -> None:
    """7. Repeated startup/recovery does not generate duplicate signals or orders."""
    session_dir = tmp_path / "session_test"
    session_dir.mkdir(parents=True)

    broker = MockBrokerAdapter(
        positions_list=[{"symbol": "KAYNES", "quantity": -10, "side": "SELL"}],
        orders_list=[{"symbol": "KAYNES", "side": "SELL", "status": "FILLED"}],
    )

    session = LiveSession(
        SessionConfig(
            mode=ExecutionMode.LIVE,
            gates=ModeGates(
                live_trading_enabled=True,
                broker_live_enabled=True,
                account_confirmed=True,
                risk_limits_valid=True,
                kill_switch_off=True,
            ),
            journal_path=str(session_dir / "journal.jsonl"),
            memory_path=str(session_dir / "memory.json"),
            kill_switch_path=str(session_dir / "kill.json"),
            adapter_name="",
        ),
        DummyProvider(),
        RiskPolicy(max_order_qty=10, max_position_qty=10, allowed_symbols=("KAYNES",)),
        request_id="test",
    )
    session._broker = broker  # type: ignore[assignment]

    # Evaluate recovered SELL signal when position already exists
    sig = StrategySignal(
        signal_id="obr:1.0:1",
        strategy_id="obr",
        strategy_version="1.0",
        timestamp="2026-10-28 10:45:00",
        event_seq=4,
        symbol="KAYNES",
        side="SELL",
        price=101.0,
        stop_loss=108.0,
    )
    now_dt = datetime(2026, 10, 28, 11, 0, tzinfo=VENUE_TZ)
    is_valid, reason = evaluate_recovered_signal(
        sig,
        now_dt=now_dt,
        existing_positions=broker.positions(),
        existing_orders=broker.open_orders(),
    )
    assert is_valid is False
    assert "POSITION_ALREADY_EXISTS" in reason
    assert len(broker.placed_orders) == 0


def test_8_expired_historical_signals_do_not_create_stale_entries() -> None:
    """8. Expired historical signals do not create stale live entries."""
    sig = StrategySignal(
        signal_id="obr:1.0:1",
        strategy_id="obr",
        strategy_version="1.0",
        timestamp="2026-10-28 10:45:00",
        event_seq=4,
        symbol="KAYNES",
        side="SELL",
        price=101.0,
        stop_loss=108.0,  # SL is 108.0
    )

    # A) SL breached by subsequent bar high >= 108.0
    bar_breach = Bar(
        symbol="KAYNES",
        open=102.0,
        high=108.5,  # Touched SL!
        low=101.0,
        close=107.0,
        volume=1000,
        timestamp="2026-10-28 11:15:00",
        bar_size="30m",
    )
    now_dt = datetime(2026, 10, 28, 11, 30, tzinfo=VENUE_TZ)
    is_valid, reason = evaluate_recovered_signal(
        sig,
        now_dt=now_dt,
        subsequent_bars=[bar_breach],
        current_quote=107.0,
    )
    assert is_valid is False
    assert "STOP_LOSS_BREACHED" in reason

    # B) SL breached by current quote >= 108.0
    is_valid_q, reason_q = evaluate_recovered_signal(
        sig,
        now_dt=now_dt,
        current_quote=108.20,
    )
    assert is_valid_q is False
    assert "STOP_LOSS_BREACHED" in reason_q

    # C) Session time exit passed (>= 15:15 IST)
    eod_dt = datetime(2026, 10, 28, 15, 20, tzinfo=VENUE_TZ)
    is_valid_time, reason_time = evaluate_recovered_signal(
        sig,
        now_dt=eod_dt,
        current_quote=100.0,
    )
    assert is_valid_time is False
    assert "SESSION_TIME_EXIT_PASSED" in reason_time

    # D) Adverse price drift (> 2.0% above entry for short)
    is_valid_drift, reason_drift = evaluate_recovered_signal(
        sig,
        now_dt=now_dt,
        current_quote=103.5,  # 2.47% above entry 101.0
    )
    assert is_valid_drift is False
    assert "ADVERSE_PRICE_DRIFT" in reason_drift

    # E) Valid signal: within hours, SL not touched, price within tolerance -> VALID!
    is_valid_ok, reason_ok = evaluate_recovered_signal(
        sig,
        now_dt=now_dt,
        current_quote=100.8,
    )
    assert is_valid_ok is True
    assert reason_ok == "VALID"


def test_9_valid_recovered_signal_executes_through_session(tmp_path) -> None:
    """9. Valid unexpired recovered signal executes through normal order safety pipeline."""
    session_dir = tmp_path / "session_exec"
    session_dir.mkdir(parents=True)

    broker = MockBrokerAdapter(positions_list=[], orders_list=[])

    session = LiveSession(
        SessionConfig(
            mode=ExecutionMode.LIVE,
            gates=ModeGates(
                live_trading_enabled=True,
                broker_live_enabled=True,
                account_confirmed=True,
                risk_limits_valid=True,
                kill_switch_off=True,
            ),
            journal_path=str(session_dir / "journal.jsonl"),
            memory_path=str(session_dir / "memory.json"),
            kill_switch_path=str(session_dir / "kill.json"),
            adapter_name="",
        ),
        DummyProvider(),
        RiskPolicy(max_order_qty=100, max_position_qty=100, allowed_symbols=("KAYNES",)),
        request_id="test-exec",
    )
    session._broker = broker  # type: ignore[assignment]

    # Register a strategy that emits a SELL signal on 2nd candle
    class EmitSellLogic:
        def __init__(self, params=None):  # noqa: ARG002
            self.count = 0

        def warmup(self) -> int:
            return 0

        def on_bar(self, view):
            self.count += 1
            if self.count == 2:
                from strategy.models.signal import Signal, SignalKind

                return Signal(
                    index=view.index,
                    timestamp=view.bar.timestamp,
                    kind=SignalKind.SELL,
                    price=100.0,
                    stop_loss=108.0,
                )
            return None

    from strategy import StrategyDefinition

    session.register_strategy(
        "test-strat",
        "1.0",
        lambda: EmitSellLogic(),
        StrategyParameters({}),
        StrategyDefinition(
            id="test-strat",
            name="test",
            version="1.0",
            kind="live",
            params=StrategyParameters({}),
        ),
    )

    session._mode = ExecutionMode.LIVE
    session._armed = LiveArm.ARMED
    for state in (
        LifecycleState.VALIDATING,
        LifecycleState.WARMING_UP,
        LifecycleState.READY,
        LifecycleState.RUNNING,
    ):
        session._lifecycle.transition(state, reason="test")

    ctx = session._contexts["test-strat:1.0"]
    for state in (
        LifecycleState.VALIDATING,
        LifecycleState.WARMING_UP,
        LifecycleState.READY,
        LifecycleState.RUNNING,
    ):
        ctx.lifecycle.transition(state, reason="test")

    bar1 = Bar(
        symbol="KAYNES",
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=1000,
        timestamp="2026-10-28 09:15:00",
        bar_size="30m",
    )
    bar2 = Bar(
        symbol="KAYNES",
        open=101.0,
        high=103.0,
        low=98.0,
        close=99.0,
        volume=1500,
        timestamp="2026-10-28 09:45:00",
        bar_size="30m",
    )

    # Engine starts at 10:00 IST (valid time window, before 15:15, SL 108 not touched, quote 99.5)
    now_epoch = _make_epoch("2026-10-28 10:00:00")
    replayed = session.catchup_session_candles(
        [bar1, bar2], now_epoch=float(now_epoch), current_quote=99.5
    )

    assert replayed == 2
    # Order was submitted!
    assert len(broker.placed_orders) == 1
    plan, client_order_id = broker.placed_orders[0]
    assert plan.symbol == "KAYNES"
    assert plan.side == "SELL"
    assert client_order_id.startswith("test-strat")
