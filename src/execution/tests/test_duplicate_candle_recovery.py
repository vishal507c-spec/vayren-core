"""Regression tests for duplicate candle recovery bug and OBR C1C4 window integrity.

Validates:
1. Exact production sequence deduplication (08:45 premarket, 09:15, 09:45, 10:15 restored,
   overlapping 09:15, 09:45, 10:15 catchup).
2. Recovery idempotency across multiple repeated recoveries.
3. LiveStrategyDriver guards against duplicate and out-of-order candles.
4. OBR C1C4 window integrity: ccInvalidated remains False on recovery,
   candleCount increments cleanly (1, 2, 3, 4), and valid breakout fires.
5. Genuine double break in price action still sets ccInvalidated = True.
6. All 22 configured stocks recover independently without cross-contamination.
"""

from __future__ import annotations

from typing import Any, cast

from broker.capabilities import CapabilitySet, Caps, Domain
from broker.faces import FactoryPlugin
from broker.registry import BrokerRecord, default_registry
from execution.events import CandleEvent
from execution.models.contract import StrategyRuntimeContract
from execution.modes import ExecutionMode, ModeGates
from execution.runtime.lifecycle import LifecycleState, StrategyLifecycle
from execution.runtime.session import LiveSession, SessionConfig
from execution.runtime.strategy_runtime import LiveStrategyDriver, StrategyContext
from market import Bar
from risk import RiskPolicy
from strategy import StrategyDefinition, StrategyParameters
from strategy.language.compiler import compile_strategy
from strategy.obr_source import OBR_C1C4_SOURCE

ALL_22_SYMBOLS = [
    "NSE:DREDGECORP",
    "NSE:WAAREERTL",
    "NSE:KEC",
    "NSE:KAYNES",
    "NSE:AVANTIFEED",
    "NSE:ROUTE",
    "NSE:RAILTEL",
    "NSE:SCI",
    "NSE:MOIL",
    "NSE:CRAMC",
    "NSE:LXCHEM",
    "NSE:NOCIL",
    "NSE:DAMCAPITAL",
    "NSE:JSWCEMENT",
    "NSE:TEXRAIL",
    "NSE:ZEEL",
    "NSE:VIKRAN",
    "NSE:IFCI",
    "NSE:DCW",
    "NSE:SAGILITY",
    "NSE:JINDWORLD",
    "NSE:NETWEB",
]


def _make_bar(
    symbol: str,
    stamp: str,
    o: float = 100.0,
    h: float = 105.0,
    low: float = 95.0,
    c: float = 102.0,
    v: int = 1000,
    tf: str = "30m",
) -> Bar:
    return Bar(
        symbol=symbol,
        open=o,
        high=h,
        low=low,
        close=c,
        volume=v,
        timestamp=stamp,
        bar_size=tf,
        source="test",
    )


def _make_candle_event(bar: Bar, seq: int = 1) -> CandleEvent:
    return CandleEvent(
        symbol=bar.symbol,
        timestamp=bar.timestamp,
        seq=seq,
        source="test",
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        timeframe=bar.bar_size,
        is_closed=True,
    )


class _DummyProvider:
    capabilities = ("trades", "quotes", "candles", "candle-close")

    def __init__(self) -> None:
        self.subscribed: tuple[str, ...] = ()

    def open(self, symbols: tuple[str, ...], timeframe: str) -> None:  # noqa: ARG002
        self.subscribed = symbols

    def close(self) -> None:
        pass

    def poll(self, now_epoch: float) -> list[Any]:  # noqa: ARG002
        return []


class _FakeBroker:
    name = "test-fake"
    capabilities = ("orders.market", "account.funds")

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def health(self) -> tuple[bool, str]:
        return (True, "ok")

    def funds(self) -> dict[str, Any]:
        return {"available": 10_000.0, "used": 0.0, "equity": 10_000.0, "currency": "INR"}

    def account(self) -> dict[str, Any]:
        return {"equity": 10_000.0, "currency": "INR"}

    def place_order(self, plan: Any, client_order_id: str) -> str:  # noqa: ARG002
        return "BROKER-1"

    def cancel_order(self, broker_order_id: str) -> bool:  # noqa: ARG002
        return False

    def modify_order(self, broker_order_id: str, quantity=None, price=None) -> bool:  # noqa: ARG002
        return False

    def stream_events(self) -> tuple[()]:
        return ()

    def on_market_price(self, symbol: str, price: float, timestamp: str) -> None:  # noqa: ARG002
        pass

    def reference_spread(self, symbol: str) -> float | None:  # noqa: ARG002
        return 0.0

    def positions(self) -> list[Any]:
        return []

    def open_orders(self) -> list[Any]:
        return []


def _register_fake_broker() -> None:
    registry = default_registry()
    if "test-fake" in registry:
        registry.unregister("test-fake")
    registry.register(
        BrokerRecord(
            name="test-fake",
            display_name="test-fake",
            plugin=FactoryPlugin(
                name="test-fake",
                display_name="test-fake",
                factories={Domain.TRADING: lambda: _FakeBroker()},
                capabilities=CapabilitySet(
                    domains=(Domain.TRADING,), items=frozenset([Caps.ORDERS_MARKET])
                ),
            ),
            capabilities=CapabilitySet(
                domains=(Domain.TRADING,), items=frozenset([Caps.ORDERS_MARKET])
            ),
            faces=(Domain.TRADING,),
        )
    )


def _create_obr_logic() -> Any:
    compiled = compile_strategy(OBR_C1C4_SOURCE)
    params = StrategyParameters(compiled.param_defaults)
    logic = cast(Any, compiled.create_logic(params))
    logic.SUPPORTS_LIVE = True
    return logic


def _create_obr_driver(symbol: str = "NSE:DAMCAPITAL") -> LiveStrategyDriver:
    _ = symbol
    logic = _create_obr_logic()
    ctx = StrategyContext(
        strategy_id="obr-c1c4",
        strategy_version="1.0",
        logic=logic,
        params=logic.params,
        contract=StrategyRuntimeContract(strategy_id="obr-c1c4", strategy_version="1.0"),
        lifecycle=StrategyLifecycle(state=LifecycleState.RUNNING),
    )
    return LiveStrategyDriver(ctx)


def test_driver_warmup_and_on_candle_deduplication() -> None:
    """Driver rejects duplicate and out-of-order candles safely."""
    driver = _create_obr_driver()
    ctx = driver.context

    # 1. Warmup initial bars
    b1 = _make_bar("TEST", "2026-11-05 09:15:00", 100, 105, 95, 102)
    b2 = _make_bar("TEST", "2026-11-05 09:45:00", 102, 108, 101, 106)
    fed = driver.warmup((b1, b2))
    assert fed == 2
    assert len(ctx.bars) == 2

    # 2. Warmup with duplicate b2 and older b1 -> skipped!
    fed2 = driver.warmup((b1, b2))
    assert fed2 == 0
    assert len(ctx.bars) == 2

    # 3. on_candle with duplicate 09:45:00 -> rejected
    c2 = _make_candle_event(b2, seq=1)
    sig2 = driver.on_candle(c2)
    assert sig2 is None
    assert len(ctx.bars) == 2

    # 4. on_candle with out-of-order 09:30:00 -> rejected
    b_old = _make_bar("TEST", "2026-11-05 09:30:00")
    sig_old = driver.on_candle(_make_candle_event(b_old, seq=2))
    assert sig_old is None
    assert len(ctx.bars) == 2

    # 5. on_candle with new 10:15:00 -> accepted
    b3 = _make_bar("TEST", "2026-11-05 10:15:00", 106, 110, 104, 108)
    driver.on_candle(_make_candle_event(b3, seq=3))
    assert len(ctx.bars) == 3
    assert ctx.bars[-1].timestamp == "2026-11-05 10:15:00"


def test_driver_restore_window_cleans_corrupted_checkpoint() -> None:
    """restore_window cleans, deduplicates, and sorts corrupted historical bars."""
    driver = _create_obr_driver()
    ctx = driver.context

    # Simulate the exact corrupted sequence found on EC2
    corrupted_bars = (
        _make_bar("TEST", "2026-11-05 08:45:00"),
        _make_bar("TEST", "2026-11-05 09:15:00"),
        _make_bar("TEST", "2026-11-05 09:15:00"),  # duplicate
        _make_bar("TEST", "2026-11-05 09:45:00"),
        _make_bar("TEST", "2026-11-05 10:15:00"),
        _make_bar("TEST", "2026-11-05 09:15:00"),  # out of order duplicate
        _make_bar("TEST", "2026-11-05 09:45:00"),  # out of order duplicate
        _make_bar("TEST", "2026-11-05 10:15:00"),  # out of order duplicate
        _make_bar("TEST", "2026-11-05 10:45:00"),
        _make_bar("TEST", "2026-11-05 11:15:00"),
    )

    fed = driver.restore_window(corrupted_bars)
    # Unique timestamps: 08:45, 09:15, 09:45, 10:15, 10:45, 11:15 -> exactly 6 bars
    assert fed == 6
    assert len(ctx.bars) == 6
    timestamps = [b.timestamp for b in ctx.bars]
    assert timestamps == [
        "2026-11-05 08:45:00",
        "2026-11-05 09:15:00",
        "2026-11-05 09:45:00",
        "2026-11-05 10:15:00",
        "2026-11-05 10:45:00",
        "2026-11-05 11:15:00",
    ]


def test_session_recovery_seeds_seen_timestamps_and_skips_duplicates() -> None:
    """LiveSession.recover seeds seen timestamps from restored window and skips duplicates."""
    _register_fake_broker()
    session = LiveSession(
        SessionConfig(
            mode=ExecutionMode.LIVE,
            gates=ModeGates(True, True, True, True, True),
            adapter_name="test-fake",
            session_start="09:15",
            session_end="15:30",
        ),
        cast(Any, _DummyProvider()),
        RiskPolicy(),
    )

    compiled = compile_strategy(OBR_C1C4_SOURCE)
    params = StrategyParameters(compiled.param_defaults)
    definition = StrategyDefinition(
        id="obr-c1c4", name="obr-c1c4", version="1.0", kind="live", params=params
    )
    session.register_strategy("obr-c1c4", "1.0", _create_obr_logic, params, definition)

    # Checkpoint containing yesterday's warmup and today's 09:15, 09:45, 10:15
    checkpoint = {
        "mode": "live",
        "session_date": "2026-11-05",
        "windows": {
            "obr-c1c4:1.0": [
                {
                    "symbol": "NSE:DAMCAPITAL",
                    "open": 90.0,
                    "high": 95.0,
                    "low": 85.0,
                    "close": 92.0,
                    "volume": 1000,
                    "timestamp": "2026-11-04 15:00:00",
                    "bar_size": "30m",
                },
                {
                    "symbol": "NSE:DAMCAPITAL",
                    "open": 100.0,
                    "high": 105.0,
                    "low": 95.0,
                    "close": 102.0,
                    "volume": 1000,
                    "timestamp": "2026-11-05 09:15:00",
                    "bar_size": "30m",
                },
                {
                    "symbol": "NSE:DAMCAPITAL",
                    "open": 102.0,
                    "high": 106.0,
                    "low": 101.0,
                    "close": 104.0,
                    "volume": 1500,
                    "timestamp": "2026-11-05 09:45:00",
                    "bar_size": "30m",
                },
                {
                    "symbol": "NSE:DAMCAPITAL",
                    "open": 104.0,
                    "high": 108.0,
                    "low": 103.0,
                    "close": 107.0,
                    "volume": 2000,
                    "timestamp": "2026-11-05 10:15:00",
                    "bar_size": "30m",
                },
            ]
        },
    }

    # 1. Recover checkpoint
    session.recover(checkpoint)

    # Verify seen timestamp was seeded
    assert session._seen_candle_stamps.get("NSE:DAMCAPITAL") == "2026-11-05 10:15:00"

    # Start session
    report = session.start(("NSE:DAMCAPITAL",), "30m")
    assert report.ready

    # 2. Feed catchup session bars: overlapping [09:15, 09:45, 10:15] + new [10:45]
    catchup_bars = [
        _make_bar("NSE:DAMCAPITAL", "2026-11-05 09:15:00", 100, 105, 95, 102),
        _make_bar("NSE:DAMCAPITAL", "2026-11-05 09:45:00", 102, 106, 101, 104),
        _make_bar("NSE:DAMCAPITAL", "2026-11-05 10:15:00", 104, 108, 103, 107),
        _make_bar("NSE:DAMCAPITAL", "2026-11-05 10:45:00", 107, 115, 105, 112),  # breakout candle
    ]
    session.catchup_session_candles(catchup_bars)

    # 3. Verify duplicate skip entries in journal
    skipped = session.journal.of_kind("CANDLE_DUPLICATE_SKIPPED")
    assert len(skipped) == 3
    skipped_stamps = [e.timestamp for e in skipped]
    assert skipped_stamps == [
        "2026-11-05 09:15:00",
        "2026-11-05 09:45:00",
        "2026-11-05 10:15:00",
    ]

    # Verify context bars contain exactly 5 bars (1 yesterday + 4 today)
    ctx = list(session._contexts.values())[0]
    assert len(ctx.bars) == 5
    stamps = [b.timestamp for b in ctx.bars]
    assert stamps == [
        "2026-11-04 15:00:00",
        "2026-11-05 09:15:00",
        "2026-11-05 09:45:00",
        "2026-11-05 10:15:00",
        "2026-11-05 10:45:00",
    ]

    # Verify OBR C1C4 engine state
    engine = cast(Any, ctx.logic).engine
    assert engine.candleCount == 3  # C4 is index 3 of today
    assert engine.refSet is True
    assert engine.refHigh == 108.0
    assert engine.refLow == 103.0
    assert engine.ccInvalidated is False  # NOT invalidated!
    assert engine.firstBreakDone is True  # C4 broke refHigh (115 > 108)


def test_recovery_idempotence_multiple_runs() -> None:
    """Running recover() multiple times is completely idempotent."""
    _register_fake_broker()
    session = LiveSession(
        SessionConfig(
            mode=ExecutionMode.LIVE,
            gates=ModeGates(True, True, True, True, True),
            adapter_name="test-fake",
            session_start="09:15",
            session_end="15:30",
        ),
        cast(Any, _DummyProvider()),
        RiskPolicy(),
    )
    compiled = compile_strategy(OBR_C1C4_SOURCE)
    params = StrategyParameters(compiled.param_defaults)
    session.register_strategy(
        "obr-c1c4",
        "1.0",
        _create_obr_logic,
        params,
        StrategyDefinition(
            id="obr-c1c4", name="obr-c1c4", version="1.0", kind="live", params=params
        ),
    )

    checkpoint = {
        "mode": "live",
        "session_date": "2026-11-05",
        "windows": {
            "obr-c1c4:1.0": [
                {
                    "symbol": "NSE:VIKRAN",
                    "open": 50.0,
                    "high": 52.0,
                    "low": 48.0,
                    "close": 51.0,
                    "volume": 500,
                    "timestamp": "2026-11-05 09:15:00",
                    "bar_size": "30m",
                },
                {
                    "symbol": "NSE:VIKRAN",
                    "open": 51.0,
                    "high": 53.0,
                    "low": 49.0,
                    "close": 52.0,
                    "volume": 600,
                    "timestamp": "2026-11-05 09:45:00",
                    "bar_size": "30m",
                },
            ]
        },
    }

    # Recover 5 times in a row
    for _ in range(5):
        session.recover(checkpoint)

    ctx = list(session._contexts.values())[0]
    assert len(ctx.bars) == 2
    assert [b.timestamp for b in ctx.bars] == [
        "2026-11-05 09:15:00",
        "2026-11-05 09:45:00",
    ]


def test_obrengine_c1c4_breakout_and_confirmation_signal() -> None:
    """OBR C1C4 generates valid BUY signal when breakout and confirmation occur cleanly."""
    driver = _create_obr_driver("NSE:DAMCAPITAL")
    ctx = driver.context

    # Warmup bar from yesterday + C1, C2, C3
    c0 = _make_bar("NSE:DAMCAPITAL", "2026-11-04 15:00:00", 90, 95, 85, 92)
    c1 = _make_bar("NSE:DAMCAPITAL", "2026-11-05 09:15:00", 100, 105, 95, 102)
    c2 = _make_bar("NSE:DAMCAPITAL", "2026-11-05 09:45:00", 102, 106, 101, 104)
    c3 = _make_bar("NSE:DAMCAPITAL", "2026-11-05 10:15:00", 104, 108, 103, 107)  # Ref: H=108, L=103

    driver.restore_window((c0, c1, c2, c3))
    assert len(ctx.bars) == 4
    eng = cast(Any, ctx.logic).engine
    assert eng.refSet is True
    assert eng.refHigh == 108.0
    assert eng.refLow == 103.0
    assert eng.ccInvalidated is False

    # Now C4: Breakout candle (High breaks 108 -> 110, Low stays above refLow: 104)
    c4 = _make_bar("NSE:DAMCAPITAL", "2026-11-05 10:45:00", 107, 110, 104, 109)
    sig4 = driver.on_candle(_make_candle_event(c4, seq=4))
    assert sig4 is None  # Breakout detected, waiting for confirmation on C5
    assert eng.firstBreakDone is True
    assert eng.ccBuySet is True
    assert eng.ccBuyHigh == 110.0
    assert eng.ccInvalidated is False

    # Now C5: Confirmation candle (High breaks 110 -> 112)
    c5 = _make_bar("NSE:DAMCAPITAL", "2026-11-05 11:15:00", 109, 112, 107, 111)
    sig5 = driver.on_candle(_make_candle_event(c5, seq=5))

    # Signal MUST be generated!
    assert sig5 is not None
    assert sig5.symbol == "NSE:DAMCAPITAL"
    assert sig5.side == "BUY"
    assert eng.ccInvalidated is False


def test_obrengine_c1c4_genuine_double_break_still_invalidates() -> None:
    """OBR C1C4 sets ccInvalidated = True only on genuine market double-break."""
    driver = _create_obr_driver("NSE:DAMCAPITAL")
    ctx = driver.context

    c0 = _make_bar("NSE:DAMCAPITAL", "2026-11-04 15:00:00", 90, 95, 85, 92)
    c1 = _make_bar("NSE:DAMCAPITAL", "2026-11-05 09:15:00", 100, 105, 95, 102)
    c2 = _make_bar("NSE:DAMCAPITAL", "2026-11-05 09:45:00", 102, 106, 101, 104)
    c3 = _make_bar("NSE:DAMCAPITAL", "2026-11-05 10:15:00", 104, 108, 103, 107)  # Ref: H=108, L=103

    driver.restore_window((c0, c1, c2, c3))

    # C4: Breaks Ref High (110 > 108), refLow not broken
    c4 = _make_bar("NSE:DAMCAPITAL", "2026-11-05 10:45:00", 107, 110, 104, 109)
    driver.on_candle(_make_candle_event(c4, seq=4))
    ctx_logic: Any = ctx.logic
    assert ctx_logic.engine.firstBreakDone is True
    assert ctx_logic.engine.ccBuySet is True
    assert ctx_logic.engine.ccInvalidated is False

    # C5: Crashes and breaches Ref Low (101 <= 103) without touching entry (high 107 < 110)
    c5 = _make_bar("NSE:DAMCAPITAL", "2026-11-05 11:15:00", 105, 107, 101, 102)
    sig5 = driver.on_candle(_make_candle_event(c5, seq=5))

    assert sig5 is None
    # Genuine double-break rule MUST invalidate!
    assert ctx_logic.engine.ccInvalidated is True
    assert ctx_logic.engine.ccBuySet is False


def test_all_22_stocks_isolated_recovery() -> None:
    """All 22 configured stocks recover independently without cross-contamination."""
    _register_fake_broker()
    for sym in ALL_22_SYMBOLS:
        session = LiveSession(
            SessionConfig(
                mode=ExecutionMode.LIVE,
                gates=ModeGates(True, True, True, True, True),
                adapter_name="test-fake",
                session_start="09:15",
                session_end="15:30",
            ),
            cast(Any, _DummyProvider()),
            RiskPolicy(),
        )

        compiled = compile_strategy(OBR_C1C4_SOURCE)
        params = StrategyParameters(compiled.param_defaults)
        session.register_strategy(
            "obr-c1c4",
            "1.0",
            _create_obr_logic,
            params,
            StrategyDefinition(
                id="obr-c1c4", name="obr-c1c4", version="1.0", kind="live", params=params
            ),
        )

        cp = {
            "mode": "live",
            "session_date": "2026-11-05",
            "windows": {
                "obr-c1c4:1.0": [
                    {
                        "symbol": sym,
                        "open": 90.0,
                        "high": 95.0,
                        "low": 85.0,
                        "close": 92.0,
                        "volume": 1000,
                        "timestamp": "2026-11-04 15:00:00",
                        "bar_size": "30m",
                    },
                    {
                        "symbol": sym,
                        "open": 100.0,
                        "high": 105.0,
                        "low": 95.0,
                        "close": 102.0,
                        "volume": 1000,
                        "timestamp": "2026-11-05 09:15:00",
                        "bar_size": "30m",
                    },
                    {
                        "symbol": sym,
                        "open": 102.0,
                        "high": 108.0,
                        "low": 100.0,
                        "close": 106.0,
                        "volume": 1200,
                        "timestamp": "2026-11-05 09:45:00",
                        "bar_size": "30m",
                    },
                ]
            },
        }

        session.recover(cp)
        assert session._seen_candle_stamps.get(sym) == "2026-11-05 09:45:00"
        report = session.start((sym,), "30m")
        assert report.ready

        # Catchup with duplicate 09:15, 09:45, and new 10:15
        catchup = [
            _make_bar(sym, "2026-11-05 09:15:00"),
            _make_bar(sym, "2026-11-05 09:45:00"),
            _make_bar(sym, "2026-11-05 10:15:00", 106, 110, 104, 108),
        ]
        session.catchup_session_candles(catchup)

        ctx = list(session._contexts.values())[0]
        assert len(ctx.bars) == 4
        assert [b.timestamp for b in ctx.bars] == [
            "2026-11-04 15:00:00",
            "2026-11-05 09:15:00",
            "2026-11-05 09:45:00",
            "2026-11-05 10:15:00",
        ]
        ctx_logic: Any = ctx.logic
        assert ctx_logic.engine.ccInvalidated is False
        assert ctx_logic.engine.refSet is True
