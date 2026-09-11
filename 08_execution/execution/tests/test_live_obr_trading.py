"""LIVE OBR trading proofs — same engine, same signals, safe orders.

Strategy Lab/backtest and LIVE share one strategy implementation: the
compiled ``StrategyRecord.code`` driven through ``logic.on_bar``. These
tests prove the claim with an OBR-shaped strategy (warmup 0,
buy/sell/close_position adapter, no SL/TP), seeded into a temp strategy
library exactly like production ``load_strategy_record``:

- backtest vs live signal parity on identical candles
- one candle cannot create duplicate orders (provider + ledger guards)
- per-symbol sessions keep independent state
- reconnect never re-emits (no duplicate orders)
- partial fills land correctly in the ledger
- STOP prevents new orders
- PAPER never touches a live venue; LIVE submits nothing while unarmed
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import datetime
import sqlite3
import tempfile
import time
from pathlib import Path

from backtest.models.config import BacktestConfig
from backtest.runner import BacktestRunner
from market.repository.symbol_repository import SymbolRepository
from risk import RiskPolicy
from strategy import StrategyDefinition, StrategyParameters
from strategy.language.compiler import compile_strategy
from strategy.language.storage import create_strategy

from execution import (
    ExecutionMode,
    LiveSession,
    SessionConfig,
    SqliteTailProvider,
    bars_to_candles,
)
from execution.broker.factory import resolve_broker
from execution.broker.sandbox import SandboxBroker
from execution.market_data.replay import ReplayProvider
from execution.modes import LiveArm, ModeGates

OBR_SHAPED_CODE = """from strategy.strategies.base import PythonStrategy


class Strategy(PythonStrategy):
    SUPPORTS_LIVE = True

    @staticmethod
    def param_specs():
        return ()

    def warmup(self):
        return 0

    def on_bar_logic(self, view):
        if view.index < 2:
            return
        bar = view.bar
        if bar.close > bar.open and view.state.flat:
            self.buy()
        elif bar.close < bar.open and not view.state.flat:
            self.close_position(view)
"""


def _seed_15m(tmp: Path, closes_by_symbol: dict[str, list[float]]) -> None:
    tmp.mkdir(parents=True, exist_ok=True)
    base = datetime.date(2026, 1, 5)  # a Monday
    times = ("09:15:00", "09:30:00", "09:45:00", "10:00:00", "10:15:00", "10:30:00")
    for symbol, closes in closes_by_symbol.items():
        conn = sqlite3.connect(tmp / f"{symbol}.db")
        conn.execute(
            "CREATE TABLE ohlcv (candle_time TEXT PRIMARY KEY, open REAL, "
            "high REAL, low REAL, close REAL, volume INTEGER);"
        )
        day = base
        i = 0
        while i < len(closes):
            if day.weekday() < 5:
                for slot, t in enumerate(times):
                    if i >= len(closes):
                        break
                    c = closes[i]
                    prev = closes[i - 1] if i else c
                    # alternate up/down deterministically: even idx up, odd idx down
                    if i % 2 == 0:
                        o, h, low = prev - 0.5, c + 1.0, prev - 1.0
                    else:
                        o, h, low = prev + 0.5, c + 1.0, c - 1.0
                    conn.execute(
                        "INSERT INTO ohlcv VALUES (?, ?, ?, ?, ?, ?)",
                        (f"{day.isoformat()} {t}", o, h, low, c, 1500 + slot),
                    )
                    i += 1
            day += datetime.timedelta(days=1)
        conn.commit()
        conn.close()


def _trend_closes(n: int) -> list[float]:
    # up/down alternation with drift so entries AND exits trigger
    out = []
    price = 100.0
    for i in range(n):
        price += 2.0 if i % 2 == 0 else -1.0
        out.append(round(price, 2))
    return out


def _setup(tmp: Path):
    _seed_15m(tmp, {"AAA": _trend_closes(48), "BBB": _trend_closes(48)})
    rec = create_strategy("LiveObrShaped", OBR_SHAPED_CODE, data_dir=tmp)
    return SymbolRepository(tmp), rec


def _register(session: LiveSession, rec) -> None:
    compiled = compile_strategy(rec.code)
    params = StrategyParameters(compiled.param_defaults)
    session.register_strategy(
        rec.id,
        rec.version,
        lambda: compiled.create_logic(params, owner_id=rec.name),
        params,
        StrategyDefinition(
            id=rec.id, name=rec.name, version=rec.version, kind="live", params=params
        ),
    )


def test_backtest_live_signal_parity():
    """Identical candles → identical BUY/SELL sequence, backtest == live."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        repo, rec = _setup(tmp)
        old = BacktestRunner(repo, data_dir=tmp).run(
            BacktestConfig(
                symbol="AAA",
                timeframe="15m",
                start_date="2026-01-01",
                end_date="2026-04-30",
                initial_capital=1_000_000,
            ),
            (rec.id,),
        )
        assert not old.has_error
        # TradeRecord sides are LONG/SHORT; live fills are BUY/SELL
        expected = [
            ("BUY" if t.side == "LONG" else "SELL", round(t.entry_price, 4), t.entry_time)
            for t in old.results[0].trades
        ]
        assert expected, "seed must produce entries"

        bars = tuple(repo.get_candles_timeframe("AAA", "15m", None))
        warmup, live = bars[:8], bars[8:]
        session = LiveSession(
            SessionConfig(mode=ExecutionMode.PAPER, paper_capital=1_000_000),
            ReplayProvider(bars_to_candles(live, "15m"), chunk_size=64),
            # small fixed size: full-capital sizing would exhaust paper cash
            # after the first fill and mask every later signal
            RiskPolicy(max_order_qty=10.0, max_position_qty=10.0),
        )
        _register(session, rec)
        report = session.start(("AAA",), "15m", {rec.id: warmup})
        assert report.ready, report.reasons
        now = time.time()
        provider = session._provider
        assert isinstance(provider, ReplayProvider)
        while not provider.exhausted:
            session.step(now)
        got = [
            (f.side, round(f.fill_price, 4), f.timestamp)
            for f in tuple(getattr(session._broker, "fills", ()) or ())
            if f.fill_qty > 0
        ]
        # the live tape starts after warmup: only backtest trades inside the
        # overlapping window can match (warmup bars feed indicators silently)
        live_start = live[0].timestamp
        expected = [e for e in expected if e[2] >= live_start]
        # entries pair with exits 1:1 in the single-position model
        entries = list(got[::2])
        assert len(got) == 2 * len(expected)
        assert [e[:2] for e in entries] == [e[:2] for e in expected]
        assert [e[2] for e in entries] == [e[2] for e in expected]


def test_one_candle_cannot_duplicate_orders():
    """Provider emits once; a repeated candle yields no second order."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        repo, rec = _setup(tmp)
        bars = tuple(repo.get_candles_timeframe("AAA", "15m", None))[:12]
        up = next(b for b in bars if b.close > b.open)
        tape = bars_to_candles((up, up), "15m")  # same candle twice
        session = LiveSession(
            SessionConfig(mode=ExecutionMode.PAPER), ReplayProvider(tape), RiskPolicy()
        )
        _register(session, rec)
        assert session.start(("AAA",), "15m", {rec.id: bars[:2]}).ready
        session.step(time.time())
        session.step(time.time())
        submitted = session.journal.of_kind("ORDER_SUBMITTED")
        assert len(submitted) == 1


def test_tail_provider_emits_once_and_in_order():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _seed_15m(tmp, {"AAA": _trend_closes(12)})
        provider = SqliteTailProvider(tmp, "15m", stale_after_s=3600.0)
        provider.open(("AAA",), "15m")
        assert provider.poll() == ()  # watermarked, history not replayed
        assert provider.health()[0]

        conn = sqlite3.connect(tmp / "AAA.db")
        conn.execute("INSERT INTO ohlcv VALUES ('2026-01-09 10:45:00', 110, 112, 109, 111, 1500)")
        conn.execute("INSERT INTO ohlcv VALUES ('2026-01-09 11:00:00', 111, 113, 110, 112, 1500)")
        conn.commit()
        conn.close()
        first = provider.poll()
        assert [e.timestamp for e in first] == ["2026-01-09 10:45:00"]  # newest withheld
        assert provider.poll() == ()
        conn = sqlite3.connect(tmp / "AAA.db")
        conn.execute("INSERT INTO ohlcv VALUES ('2026-01-09 11:15:00', 112, 114, 111, 113, 1500)")
        conn.commit()
        conn.close()
        second = provider.poll()
        assert [e.timestamp for e in second] == ["2026-01-09 11:00:00"]
        assert provider.poll() == ()


def test_symbols_keep_independent_state():
    """One session per symbol: AAA trades, BBB stays flat, ledgers isolated."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        repo, rec = _setup(tmp)
        sessions = {}
        for symbol in ("AAA", "BBB"):
            bars = tuple(repo.get_candles_timeframe(symbol, "15m", None))
            session = LiveSession(
                SessionConfig(mode=ExecutionMode.PAPER),
                ReplayProvider(bars_to_candles(bars, "15m")),
                RiskPolicy(),
            )
            _register(session, rec)
            assert session.start((symbol,), "15m", {rec.id: bars[:8]}).ready
            sessions[symbol] = session
        now = time.time()
        for session in sessions.values():
            provider = session._provider
            while not provider.exhausted:
                session.step(now)
        ledgers = {s: sessions[s].ledger for s in sessions}
        assert ledgers["AAA"] is not ledgers["BBB"]
        fills_by_session = {
            s: list(getattr(sessions[s]._broker, "fills", ()) or ()) for s in sessions
        }
        assert fills_by_session["AAA"], "seed must produce AAA fills"
        assert fills_by_session["BBB"], "seed must produce BBB fills"
        # no fill ever leaks across symbols
        assert all(f.symbol == "AAA" for f in fills_by_session["AAA"])
        assert all(f.symbol == "BBB" for f in fills_by_session["BBB"])


def test_reconnect_never_reemits():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _seed_15m(tmp, {"AAA": _trend_closes(12)})
        provider = SqliteTailProvider(tmp, "15m", stale_after_s=3600.0)
        provider.open(("AAA",), "15m")
        provider.disconnect()
        assert provider.poll() == ()
        assert not provider.health()[0]
        provider.reconnect()
        assert provider.health()[0]
        assert provider.poll() == ()  # nothing new, nothing re-emitted


def test_session_recover_reconciles_clean():
    """Checkpoint → fresh session → recover → start: no dup, no block (paper)."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        repo, rec = _setup(tmp)
        bars = tuple(repo.get_candles_timeframe("AAA", "15m", None))
        first, rest = bars[:16], bars[16:]
        session = LiveSession(
            SessionConfig(mode=ExecutionMode.PAPER),
            ReplayProvider(bars_to_candles(first[8:], "15m")),
            RiskPolicy(),
        )
        _register(session, rec)
        assert session.start(("AAA",), "15m", {rec.id: first[:8]}).ready
        provider = session._provider
        assert isinstance(provider, ReplayProvider)
        while not provider.exhausted:
            session.step(time.time())
        checkpoint = session.checkpoint()

        resumed = LiveSession(
            SessionConfig(mode=ExecutionMode.PAPER),
            ReplayProvider(bars_to_candles(rest, "15m")),
            RiskPolicy(),
        )
        _register(resumed, rec)
        resumed.recover(checkpoint)
        report = resumed.start(("AAA",), "15m", {rec.id: first})
        assert report.ready, report.reasons
        assert resumed.reconciliation.blocks_live is False
        delivered = 0
        provider2 = resumed._provider
        assert isinstance(provider2, ReplayProvider)
        while not provider2.exhausted:
            delivered += resumed.step(time.time())
        assert delivered > 0, "resumed session must process the remaining tape"


def test_partial_fill_lands_in_ledger():
    """Sandbox partial policy → ledger holds partial qty, order stays open."""
    from execution.models.order import OrderPlan
    from execution.portfolio.ledger import PositionLedger

    broker = SandboxBroker()
    broker.connect()
    broker.set_default_policy("partial:10")
    plan = OrderPlan(intent_id="i1", symbol="AAA", side="BUY", quantity=100.0)
    broker.place_order(plan, "c1")
    fill = broker.settle("c1", 100.0, "2026-01-05 09:30:00")
    assert fill is not None and fill.partial
    ledger = PositionLedger(1_000_000.0)
    pos = ledger.apply_fill(fill)
    assert pos.quantity == 10.0
    assert [o for o in broker.open_orders() if o["client_order_id"] == "c1"]


def test_stop_prevents_new_orders():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        repo, rec = _setup(tmp)
        bars = tuple(repo.get_candles_timeframe("AAA", "15m", None))
        session = LiveSession(
            SessionConfig(mode=ExecutionMode.PAPER),
            ReplayProvider(bars_to_candles(bars, "15m"), chunk_size=4),
            RiskPolicy(),
        )
        _register(session, rec)
        assert session.start(("AAA",), "15m", {rec.id: bars[:8]}).ready
        session.step(time.time())
        session.stop()
        before = len(session.journal.of_kind("ORDER_SUBMITTED"))
        for _ in range(20):
            session.step(time.time())
        after = len(session.journal.of_kind("ORDER_SUBMITTED"))
        assert after == before


def test_paper_never_touches_live_venue():
    broker, effective, _ = resolve_broker(ExecutionMode.PAPER, ModeGates(), adapter_name="nope")
    assert effective == ExecutionMode.PAPER
    assert broker.name == "paper"


def test_live_submits_nothing_while_unarmed():
    """LIVE + sandbox venue: signal without ARM is blocked, with ARM fills."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        repo, rec = _setup(tmp)
        bars = tuple(repo.get_candles_timeframe("AAA", "15m", None))
        # tape must be NEWER than warmup: the driver rejects out-of-order bars
        ups = [b for b in bars[8:12] if b.close > b.open][:2]
        assert len(ups) == 2
        tape = bars_to_candles(tuple(ups), "15m")
        gates = ModeGates(True, True, True, True, True)
        session = LiveSession(
            SessionConfig(
                mode=ExecutionMode.LIVE, paper_capital=1_000_000.0, adapter_name="sandbox"
            ),
            ReplayProvider(tape),
            RiskPolicy(),
            request_id="live-arm-test",
        )
        _register(session, rec)
        session._config.gates = gates
        report = session.start(("AAA",), "15m", {rec.id: bars[:8]})
        assert report.ready, report.reasons
        assert session.mode == ExecutionMode.LIVE
        session.step(time.time())
        assert session.armed == LiveArm.DISARMED
        assert session.journal.of_kind("ORDER_BLOCKED_UNARMED")
        assert len(session.journal.of_kind("ORDER_SUBMITTED")) == 0
        session.arm("test consent")
        session._provider = ReplayProvider(tape)
        session._provider.open(("AAA",), "15m")  # protocol: open before poll
        session.step(time.time())  # seq=1 already seen → normalizer drops the replay
        session.step(time.time())  # seq=2 is new → submits now that armed
        assert len(session.journal.of_kind("ORDER_SUBMITTED")) == 1
