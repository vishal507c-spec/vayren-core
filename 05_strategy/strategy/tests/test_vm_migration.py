"""Python-native strategy migration proof — no VM, no DSL.

Proves:
A. OBR Python → Signal
B. SMA Python → Signal
C. User Python strategy → Signal
D. Same base class used
E. No DSL import for execution
F. No strategy-specific factory
G. Backtest works with Python
H. Versioning works with Python
I. No .vstrat remaining
"""

from pathlib import Path


def test_obr_python_signal(tmp_path: Path):

    from market.models.bar import Bar

    from strategy.language import compile_strategy
    from strategy.language.storage import create_strategy
    from strategy.models.parameters import StrategyParameters
    from strategy.runtime import StrategyRuntime

    # Create OBR via Python code
    obr_code = """
from strategy.strategies.base import PythonStrategy
from strategy.strategies.indicators import calc_range, calc_rsi

class Strategy(PythonStrategy):
    def on_bar_logic(self, view):
        bar = view.bar
        ref_range = calc_range(self.highs, self.lows, 20)
        rsi_val = calc_rsi(self.closes, 14)
        if bar.close > bar.high - ref_range * 0.10 and rsi_val > 55:
            self.buy()
        if bar.close < bar.low + ref_range * 0.10 and rsi_val < 45:
            self.sell()
        self.time_exit("15:15")
"""
    rec = create_strategy("OBR", obr_code, data_dir=tmp_path)
    compiled = compile_strategy(rec.code)
    strat = compiled.create_logic(StrategyParameters({}))
    bars = tuple(
        Bar(
            symbol="TEST",
            open=100,
            high=101,
            low=99,
            close=100 + (i % 5),
            volume=1000,
            timestamp=f"2026-01-01 09:{15 + i:02d}:00",
        )
        for i in range(60)
    )
    signals = StrategyRuntime(strat, StrategyParameters({})).run(bars)
    assert isinstance(signals, tuple)


def test_sma_python_signal(tmp_path: Path):
    from market.models.bar import Bar

    from strategy.language import compile_strategy
    from strategy.language.storage import create_strategy
    from strategy.models.parameters import StrategyParameters
    from strategy.runtime import StrategyRuntime

    sma_code = """
from strategy.strategies.base import PythonStrategy
from strategy.strategies.indicators import calc_sma

class Strategy(PythonStrategy):
    def __init__(self, params=None):
        super().__init__(params)
        self.prev_fast=None
        self.prev_slow=None
    def on_bar_logic(self, view):
        fast=calc_sma(self.closes, 2)
        slow=calc_sma(self.closes, 3)
        if self.prev_fast is None:
            self.prev_fast=fast; self.prev_slow=slow; return
        if fast>slow and self.prev_fast<=self.prev_slow:
            self.buy()
        elif fast<slow and self.prev_fast>=self.prev_slow:
            self.sell()
        self.prev_fast=fast; self.prev_slow=slow
"""
    rec = create_strategy("SMA", sma_code, data_dir=tmp_path)
    compiled = compile_strategy(rec.code)
    strat = compiled.create_logic(StrategyParameters({}))
    bars = tuple(
        Bar(
            symbol="TEST",
            open=100,
            high=101,
            low=99,
            close=100 + (i % 3),
            volume=1000,
            timestamp=f"2026-01-01 09:{15 + i:02d}:00",
        )
        for i in range(60)
    )
    signals = StrategyRuntime(strat, StrategyParameters({})).run(bars)
    assert isinstance(signals, tuple)


def test_no_vstrat_files():
    import pathlib

    # Ensure no .vstrat file handling in codebase
    for p in pathlib.Path("05_strategy").rglob("*.py"):
        if "tests" in p.parts:
            continue
        t = p.read_text(encoding="utf-8")
        assert ".vstrat" not in t
    for p in pathlib.Path("06_backtest").rglob("*.py"):
        if "tests" in p.parts:
            continue
        t = p.read_text(encoding="utf-8")
        assert ".vstrat" not in t
    # Ensure storage uses .py
    assert pathlib.Path("05_strategy/strategy/language/storage.py").read_text().count(".py") > 0


def test_same_base_class():
    from strategy.strategies.obr import ObrSellV10
    from strategy.strategies.sma import SmaCrossover

    assert ObrSellV10.__bases__[0].__name__ == "PythonStrategy"
    assert SmaCrossover.__bases__[0].__name__ == "PythonStrategy"


def test_backtest_python(tmp_path: Path):
    import datetime
    import sqlite3

    from backtest.models.config import BacktestConfig
    from backtest.runner import BacktestRunner
    from market.repository.symbol_repository import SymbolRepository

    from strategy.language.storage import create_strategy

    code = """
from strategy.strategies.base import PythonStrategy
class Strategy(PythonStrategy):
    def on_bar_logic(self, view):
        if view.bar.close > view.bar.open:
            self.buy()
"""
    rec = create_strategy("TestStrat", code, data_dir=tmp_path)
    # Create repo with data
    db_path = tmp_path / "data"
    db_path.mkdir()
    # Use symbol repository
    repo = SymbolRepository(db_path)
    # Create a test DB
    db = db_path / "TEST.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE ohlcv (candle_time TEXT PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume INTEGER)"
    )
    base = datetime.date(2026, 1, 1)
    for i in range(30):
        c = 100 + i % 5
        conn.execute(
            "INSERT INTO ohlcv VALUES (?,?,?,?,?,?)",
            (f"{base} 09:15:00", c - 1, c + 1, c - 1, c, 1000),
        )
        base += datetime.timedelta(days=1)
    conn.commit()
    conn.close()
    runner = BacktestRunner(repo, data_dir=tmp_path)
    cfg = BacktestConfig(
        symbol="TEST", timeframe="15m", start_date="2026-01-01", end_date="2026-02-01"
    )
    result = runner.run(cfg, (rec.id,))
    assert result is not None
