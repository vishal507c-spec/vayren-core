"""Worker batch wiring — enqueue → stock-level progress → done, off UI thread.

These tests drive the worker with ``max_workers=1`` (inline execution in
the worker thread, no child processes): they prove the QThread queue,
stock-level progress/done/failed signals and cancellation. The process
pool itself is covered by ``test_batch_parity.py`` (spawn without Qt),
which is how production combines them.

Slots use direct connections and a ``threading.Event`` gate — no nested
Qt event loop, so the test stays hermetic inside the full suite.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3
import tempfile
import threading
from pathlib import Path

from market.repository.symbol_repository import SymbolRepository
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from strategy.language.storage import create_strategy

from backtest.runner import BacktestRunner
from backtest.worker import BacktestWorker, BatchEnqueued


def _qt_app() -> QApplication:
    inst = QApplication.instance()
    if isinstance(inst, QApplication):
        return inst
    return QApplication([])


SMA_CODE = """from strategy.strategies.base import PythonStrategy
from strategy.strategies.indicators import calc_sma
class Strategy(PythonStrategy):
    @staticmethod
    def param_specs():
        from strategy.models.parameters import ParameterSpec
        return (
            ParameterSpec(key="fast_period", label="Fast period", default=10, minimum=2, maximum=50, decimals=0),
            ParameterSpec(key="slow_period", label="Slow period", default=30, minimum=5, maximum=100, decimals=0),
        )
    def __init__(self, params=None):
        super().__init__(params)
        self.prev_fast = None
        self.prev_slow = None
    def on_bar_logic(self, view):
        fast_period = int(self.params.get("fast_period", 10))
        slow_period = int(self.params.get("slow_period", 30))
        fast = calc_sma(self.closes, fast_period)
        slow = calc_sma(self.closes, slow_period)
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


def _seed(tmp: Path) -> SymbolRepository:
    import datetime

    tmp.mkdir(parents=True, exist_ok=True)
    base = datetime.date(2026, 1, 5)
    for symbol, seed in (("WAAA", 1.0), ("WBBB", 5.0)):
        db = tmp / f"{symbol}.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE ohlcv (candle_time TEXT PRIMARY KEY, open REAL, "
            "high REAL, low REAL, close REAL, volume INTEGER);"
        )
        price = 100.0 + seed
        day = base
        for _ in range(60):
            while day.weekday() >= 5:
                day += datetime.timedelta(days=1)
            price += 1.0
            conn.execute(
                "INSERT INTO ohlcv VALUES (?, ?, ?, ?, ?, ?)",
                (f"{day.isoformat()} 09:15:00", price - 1, price + 1, price - 2, price, 1000),
            )
            day += datetime.timedelta(days=1)
        conn.commit()
        conn.close()
    return SymbolRepository(tmp)


def test_worker_batch_progress_and_done():
    _qt_app()
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        repo = _seed(tmp)
        rec = create_strategy("WorkerBatchSMA", SMA_CODE, data_dir=tmp)
        worker = BacktestWorker(BacktestRunner(repo, data_dir=tmp))
        try:
            finished = threading.Event()
            progress: list = []
            done: list = []
            failed: list = []
            worker.batch_progress.connect(progress.append, Qt.ConnectionType.DirectConnection)
            worker.batch_done.connect(
                lambda p: (done.append(p), finished.set()), Qt.ConnectionType.DirectConnection
            )
            worker.batch_failed.connect(
                lambda p: (failed.append(p), finished.set()),
                Qt.ConnectionType.DirectConnection,
            )
            worker.enqueue_batch(
                BatchEnqueued(
                    request_id="wb1",
                    strategy_id=rec.id,
                    symbols=("WAAA", "WBBB"),
                    timeframe="15m",
                    start_date="2026-01-01",
                    end_date="2026-04-30",
                    initial_capital=1_000_000.0,
                    slippage_pct=0.02,
                    commission_pct=0.03,
                    max_workers=1,
                )
            )
            assert finished.wait(timeout=90), "batch did not finish"
            assert not failed, failed
            assert len(done) == 1
            request_id, outcomes = done[0]
            assert request_id == "wb1"
            assert [o.symbol for o in outcomes] == ["WAAA", "WBBB"]
            assert all(o.error is None for o in outcomes)
            # stock-level progress only: (done, total) pairs ending at 2/2
            assert progress
            assert progress[-1] == ("wb1", 2, 2)
            assert all(p[0] == "wb1" and p[2] == 2 for p in progress)
        finally:
            worker.shutdown()


def test_worker_batch_unknown_strategy_fails():
    _qt_app()
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        repo = _seed(tmp)
        worker = BacktestWorker(BacktestRunner(repo, data_dir=tmp))
        try:
            finished = threading.Event()
            failed: list = []
            worker.batch_failed.connect(
                lambda p: (failed.append(p), finished.set()),
                Qt.ConnectionType.DirectConnection,
            )
            worker.batch_done.connect(lambda _p: finished.set(), Qt.ConnectionType.DirectConnection)
            worker.enqueue_batch(
                BatchEnqueued(
                    request_id="wb2",
                    strategy_id="no-such-strategy",
                    symbols=("WAAA",),
                    timeframe="15m",
                    start_date="2026-01-01",
                    end_date="2026-04-30",
                    initial_capital=1_000_000.0,
                    slippage_pct=0.02,
                    commission_pct=0.03,
                    max_workers=1,
                )
            )
            assert finished.wait(timeout=90), "batch did not finish"
            assert failed and failed[0][0] == "wb2"
        finally:
            worker.shutdown()


def _make_job(rec_id: str, symbols: tuple[str, ...]) -> BatchEnqueued:
    return BatchEnqueued(
        request_id="wb-path",
        strategy_id=rec_id,
        symbols=symbols,
        timeframe="15m",
        start_date="2026-01-01",
        end_date="2026-04-30",
        initial_capital=1_000_000.0,
        slippage_pct=0.02,
        commission_pct=0.03,
        max_workers=1,
    )


def test_batch_uses_repository_dir_for_market_data():
    """Regression: market DBs resolve under the repository directory.

    Production wires ``BacktestRunner(repository=<market dir>,
    data_dir=<strategy library>)``. The batch must read stock databases
    from the repository dir — never ``<strategy library>/<SYMBOL>.db``.
    Drives ``_execute_batch`` synchronously (no threads/loop needed).
    """
    _qt_app()
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        market = tmp / "market"
        strats = tmp / "strats"
        strats.mkdir(parents=True, exist_ok=True)
        _seed(market)
        rec = create_strategy("PathCheckSMA", SMA_CODE, data_dir=strats)
        runner = BacktestRunner(SymbolRepository(market), data_dir=strats)
        # Sanity: the two roots really differ (the production shape).
        assert str(runner._data_dir) == str(strats)
        assert str(runner._repository.directory) == str(market)
        worker = BacktestWorker(runner)
        try:
            done: list = []
            failed: list = []
            worker.batch_done.connect(done.append, Qt.ConnectionType.DirectConnection)
            worker.batch_failed.connect(failed.append, Qt.ConnectionType.DirectConnection)
            worker._execute_batch(_make_job(rec.id, ("WAAA", "WBBB")))
            assert not failed, failed
            assert len(done) == 1
            request_id, outcomes = done[0]
            assert request_id == "wb-path"
            assert [o.symbol for o in outcomes] == ["WAAA", "WBBB"]
            assert all(o.error is None for o in outcomes), [o.error for o in outcomes]
            assert all(o.result is not None and o.result.bars_used > 0 for o in outcomes)
        finally:
            worker.shutdown()


def test_batch_missing_symbol_error_names_market_dir():
    """A genuinely missing database errors honestly under the market root."""
    _qt_app()
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        market = tmp / "market"
        strats = tmp / "strats"
        strats.mkdir(parents=True, exist_ok=True)
        _seed(market)
        rec = create_strategy("PathCheckSMA", SMA_CODE, data_dir=strats)
        runner = BacktestRunner(SymbolRepository(market), data_dir=strats)
        worker = BacktestWorker(runner)
        try:
            done: list = []
            worker.batch_done.connect(done.append, Qt.ConnectionType.DirectConnection)
            worker._execute_batch(_make_job(rec.id, ("NOSUCHDB",)))
            assert len(done) == 1
            (_, outcomes) = done[0]
            assert len(outcomes) == 1
            assert outcomes[0].result is None
            assert outcomes[0].error is not None
            # Honest: names the market root, never the strategy library.
            assert str(market) in outcomes[0].error
            assert str(strats) not in outcomes[0].error
        finally:
            worker.shutdown()
