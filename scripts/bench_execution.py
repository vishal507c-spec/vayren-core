"""Execution-layer micro-benchmarks — stdlib only, baselines first.

Measures (no claims, no optimization): market-event dispatch throughput,
strategy evaluation, risk evaluation, order planning, paper settle and
reconciliation time. Prints a small table; record the numbers, don't
chase them.
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path

from execution.broker.paper import PaperBroker
from execution.market_data.normalizer import StreamNormalizer
from execution.market_data.replay import ReplayProvider, bars_to_candles
from execution.planner import OrderPlanner
from execution.runtime.session import LiveSession, SessionConfig
from execution.tests.helpers import make_bars
from risk import RiskEngine, RiskPolicy, RiskRequest
from strategy import StrategyParameters
from strategy.strategies.sma import SmaCrossover


def _timed(fn, repeats: int = 5) -> dict[str, float]:
    import math

    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - start)
    samples.sort()
    rank = max(0, min(repeats - 1, math.ceil(0.95 * repeats) - 1))
    return {
        "n": repeats,
        "median_ms": statistics.median(samples) * 1000.0,
        "p95_ms": samples[rank] * 1000.0,
        "min_ms": samples[0] * 1000.0,
        "max_ms": samples[-1] * 1000.0,
    }


def bench_event_dispatch() -> dict[str, float]:
    candles = bars_to_candles(make_bars("TEST", 200), "15m")

    def run_once() -> None:
        provider = ReplayProvider(candles, chunk_size=1000)
        normalizer = StreamNormalizer()
        provider.open(("TEST",), "15m")
        now = time.time()
        while True:
            batch = provider.poll()
            if not batch:
                break
            for event in batch:
                normalizer.observe(event, now)

    stats = _timed(run_once)
    stats["events_per_second"] = 200.0 / (stats["median_ms"] / 1000.0)
    return stats


def bench_strategy_eval() -> dict[str, float]:

    from strategy import BarView, StrategyState

    bars = make_bars("TEST", 60)
    logic = SmaCrossover(StrategyParameters({"fast_period": 2, "slow_period": 3}))
    params = StrategyParameters({"fast_period": 2, "slow_period": 3})
    view = BarView(bars=bars, index=59, params=params, state=StrategyState())

    def run_once() -> None:
        logic.on_bar(view)

    out = _timed(run_once, repeats=200)
    return out


def bench_risk_eval() -> dict[str, float]:
    engine = RiskEngine(RiskPolicy())
    request = RiskRequest(
        intent_id="bench",
        strategy_id="s",
        symbol="T",
        side="BUY",
        quantity=10.0,
        price=100.0,
        timestamp="2026-01-06T10:00:00+00:00",
        equity=1_000_000.0,
        available_capital=500_000.0,
        data_age_seconds=1.0,
        now_epoch=time.time(),
    )

    def run_once() -> None:
        engine.evaluate(request)

    return _timed(run_once, repeats=200)


def bench_plan_and_settle() -> dict[str, float]:
    planner = OrderPlanner()
    broker = PaperBroker()
    broker.connect()
    from execution.models.intent import ExecutionIntent

    intent = ExecutionIntent(
        intent_id="i",
        strategy_id="s",
        strategy_version="1",
        signal_id="g",
        timestamp="t",
        event_seq=1,
        symbol="T",
        side="BUY",
        target_position_qty=10.0,
        quantity=10.0,
    )

    def run_once() -> None:
        cid = f"c-{time.perf_counter_ns()}"
        plan = planner.plan(intent, 100.0)
        broker.place_order(plan, cid)
        broker.settle(cid, 100.0, "t")

    return _timed(run_once, repeats=100)


def bench_paper_session() -> dict[str, float]:
    bars = make_bars("TEST", 60)
    candles = bars_to_candles(bars, "15m")

    def run_once() -> None:
        provider = ReplayProvider(candles, chunk_size=1000)
        session = LiveSession(SessionConfig(), provider, RiskPolicy())
        session.register_strategy(
            "sma",
            "1.0",
            lambda: SmaCrossover(StrategyParameters({"fast_period": 2, "slow_period": 3})),
            StrategyParameters({}),
        )
        session.start(("TEST",), "15m", {"sma": bars[:20]})
        now = time.time()
        while not provider.exhausted:
            session.step(now)

    stats = _timed(run_once, repeats=3)
    stats["events_per_second"] = 60.0 / (stats["median_ms"] / 1000.0)
    return stats


def bench_reconcile() -> dict[str, float]:
    session_bars = make_bars("TEST", 60)
    candles = bars_to_candles(session_bars, "15m")
    provider = ReplayProvider(candles, chunk_size=1000)
    session = LiveSession(SessionConfig(), provider, RiskPolicy())
    session.register_strategy(
        "sma",
        "1.0",
        lambda: SmaCrossover(StrategyParameters({"fast_period": 2, "slow_period": 3})),
        StrategyParameters({}),
    )
    session.start(("TEST",), "15m", {"sma": session_bars[:20]})
    now = time.time()
    while not provider.exhausted:
        session.step(now)

    def run_once() -> None:
        session.reconcile_now()

    return _timed(run_once, repeats=20)


def bench_paper_bootstrap() -> dict[str, float]:
    """PAPER_BOOTSTRAP_E2E baseline: startup/recovery/reconcile/warmup/execution/shutdown."""
    import shutil
    import sqlite3
    import tempfile

    from app.services.paper_service import PaperRunConfig, PaperService

    tmp = Path(tempfile.mkdtemp(prefix="bench-paper-"))
    data_dir = tmp / "data"
    data_dir.mkdir()
    conn = sqlite3.connect(data_dir / "TEST.db")
    conn.executescript(
        "CREATE TABLE ohlcv (candle_time TEXT PRIMARY KEY,"
        " open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,"
        " close REAL NOT NULL, volume INTEGER NOT NULL);"
    )
    for i in range(60):
        close = 100.0 + (i % 10) - 3.0 + i * 0.05
        conn.execute(
            "INSERT INTO ohlcv VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"2026-01-{(i % 28) + 1:02d} 09:{15 + (i % 45):02d}:00",
                close - 0.5,
                close + 0.5,
                close - 1.0,
                close,
                1000 + i,
            ),
        )
    conn.commit()
    conn.close()
    strategy_dir = tmp / "strategies"
    strategy_dir.mkdir()
    from strategy.language.storage import save_strategy

    save_strategy(_SMA_CODE, "sma-paper", strategy_dir)

    stages: dict[str, list[float]] = {
        "startup": [],
        "execution": [],
        "shutdown": [],
        "recovery": [],
    }
    try:
        for _ in range(3):
            checkpoint = data_dir / "paper" / "TEST" / "checkpoint.json"
            if checkpoint.is_file():
                checkpoint.unlink()
            service = PaperService(PaperRunConfig(data_dir=data_dir, strategy_dir=strategy_dir))
            start = time.perf_counter()
            service.prepare()
            stages["startup"].append(time.perf_counter() - start)
            start = time.perf_counter()
            report = service.run()
            stages["execution"].append(time.perf_counter() - start)
            assert report.fills > 0
            start = time.perf_counter()
            service.shutdown()
            stages["shutdown"].append(time.perf_counter() - start)
        # recovery pass: checkpoint exists from the last run
        service = PaperService(PaperRunConfig(data_dir=data_dir, strategy_dir=strategy_dir))
        service.prepare()
        start = time.perf_counter()
        report = service.run()
        stages["recovery"].append(time.perf_counter() - start)
        assert report.fills > 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out = {name: round(statistics.median(samples) * 1000.0, 3) for name, samples in stages.items()}
    out["n"] = 3
    total = out["startup"] + out["execution"] + out["shutdown"]
    print(
        f"  paper_bootstrap stages (median ms): startup={out['startup']:.2f} "
        f"execution={out['execution']:.2f} shutdown={out['shutdown']:.2f} "
        f"recovery_run={out['recovery']:.2f}"
    )
    return {"median_ms": total, "p95_ms": total, "n": 3}


_SMA_CODE = """from strategy.strategies.base import PythonStrategy
from strategy.strategies.indicators import calc_sma
class Strategy(PythonStrategy):
    def __init__(self, params=None):
        super().__init__(params)
        self.prev_fast = None
        self.prev_slow = None
    def on_bar_logic(self, view):
        fast = calc_sma(self.closes, 2)
        slow = calc_sma(self.closes, 3)
        if self.prev_fast is None:
            self.prev_fast = fast
            self.prev_slow = slow
            return
        if fast > slow and self.prev_fast <= self.prev_slow:
            self.buy()
        elif fast < slow and self.prev_fast >= self.prev_slow:
            self.sell()
        self.prev_fast = fast
        self.prev_slow = slow
"""


def bench_sandbox_bootstrap() -> dict[str, float]:
    """SANDBOX_BOOTSTRAP_E2E baseline: startup/recovery/warmup/event
    processing/order submission/fill processing/reconciliation/shutdown."""
    import time as _time

    from execution.broker.factory import register_adapter
    from execution.broker.sandbox import SandboxBroker
    from execution.modes import ExecutionMode
    from execution.runtime.session import LiveSession, SessionConfig

    bars = make_bars("TEST", 60)
    candles = bars_to_candles(bars, "15m")
    stages: dict[str, list[float]] = {
        "startup": [],
        "event_processing": [],  # includes order submission + fill processing per event
        "reconciliation": [],
        "shutdown": [],
    }
    for _ in range(3):
        start = time.perf_counter()
        register_adapter("bench-sandbox", lambda: SandboxBroker(account_id="bench"))
        provider = ReplayProvider(candles, chunk_size=1000)
        session = LiveSession(
            SessionConfig(mode=ExecutionMode.SANDBOX, adapter_name="bench-sandbox"),
            provider,
            RiskPolicy(),
        )
        session.register_strategy(
            "sma",
            "1.0",
            lambda: SmaCrossover(StrategyParameters({"fast_period": 2, "slow_period": 3})),
            StrategyParameters({}),
        )
        report = session.start(("TEST",), "15m", {"sma": bars[:20]})
        assert report.ready, report.reasons
        stages["startup"].append(time.perf_counter() - start)
        now = _time.time()
        start = time.perf_counter()
        delivered = 0
        while not provider.exhausted:
            delivered += session.step(now)
        stages["event_processing"].append(time.perf_counter() - start)
        broker = session._broker
        fills = len(broker.fills) if isinstance(broker, SandboxBroker) else 0
        assert fills > 0 and delivered > 0
        start = time.perf_counter()
        session.reconcile_now()
        stages["reconciliation"].append(time.perf_counter() - start)
        start = time.perf_counter()
        session.stop()
        stages["shutdown"].append(time.perf_counter() - start)
    medians = {k: round(statistics.median(v) * 1000.0, 3) for k, v in stages.items()}
    print(
        "  sandbox_bootstrap stages (median ms): "
        + " ".join(f"{k}={v:.2f}" for k, v in medians.items())
    )
    total = sum(medians.values())
    return {"median_ms": total, "p95_ms": total, "n": 3}


def main() -> int:
    results = {
        "event_dispatch_200candles": bench_event_dispatch(),
        "strategy_eval_single_bar": bench_strategy_eval(),
        "risk_eval_single_request": bench_risk_eval(),
        "plan_and_paper_settle": bench_plan_and_settle(),
        "paper_session_60candles": bench_paper_session(),
        "reconcile": bench_reconcile(),
        "paper_bootstrap_e2e": bench_paper_bootstrap(),
        "sandbox_bootstrap_e2e": bench_sandbox_bootstrap(),
    }
    print(f"{'benchmark':32s} {'median':>10s} {'p95':>10s} {'extra':>16s}")
    for name, stats in results.items():
        extra = ""
        if "events_per_second" in stats:
            extra = f"{stats['events_per_second']:,.0f} ev/s"
        median = stats.get("median_ms", 0.0)
        p95 = stats.get("p95_ms", median)
        print(f"{name:32s} {median:9.3f}m {p95:9.3f}m {extra:>16s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
