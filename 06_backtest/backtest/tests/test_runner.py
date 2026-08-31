"""Backtest runner end-to-end with VM-only path (.vstrat → IR → VM)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3
import tempfile
from pathlib import Path

from market.repository.symbol_repository import SymbolRepository
from strategy.language.storage import create_strategy

from backtest.models.config import BacktestConfig
from backtest.runner import BacktestRunner


def _seed_repo(tmp: Path, symbol: str, closes: list[float]) -> SymbolRepository:
    import datetime

    tmp.mkdir(parents=True, exist_ok=True)
    db = tmp / f"{symbol}.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE ohlcv (candle_time TEXT PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume INTEGER);"  # noqa: E501
    )
    base = datetime.date(2026, 1, 1)
    for i, c in enumerate(closes):
        day = base + datetime.timedelta(days=i)
        conn.execute(
            "INSERT INTO ohlcv VALUES (?, ?, ?, ?, ?, ?)",
            (f"{day.isoformat()} 09:15:00", c, c + 1, c - 1, c, 1000),
        )
    conn.commit()
    conn.close()
    return SymbolRepository(tmp)


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


def test_runner_produces_metrics():
    with tempfile.TemporaryDirectory() as d:
        closes = [100.0 + (i % 10) for i in range(60)]
        closes[10:15] = [120.0, 122.0, 125.0, 123.0, 121.0]
        tmp = Path(d)
        repo = _seed_repo(tmp, "TEST", closes)
        # Create .vstrat strategy (VM-only, no builtin factory)
        rec = create_strategy("SMA Crossover", SMA_CODE, data_dir=tmp)
        runner = BacktestRunner(repo, data_dir=tmp)
        config = BacktestConfig(
            symbol="TEST",
            timeframe="15m",
            start_date="2026-01-01",
            end_date="2026-01-30",
            initial_capital=1_000_000,
        )
        result = runner.run(config, (rec.id,))
        assert not result.has_error
        assert len(result.results) == 1
        strategy_result = result.results[0]
        assert strategy_result.bars_used > 0
        assert strategy_result.equity_curve
        assert strategy_result.metrics.starting_capital == 1_000_000
        # Also test by name lookup (VM path supports both)
        result2 = runner.run(config, (rec.name,))
        assert not result2.has_error
        assert len(result2.results) == 1


def test_runner_empty_range():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        repo = _seed_repo(tmp, "TEST", [100.0, 101.0, 102.0])
        rec = create_strategy("EmptyTest", SMA_CODE, data_dir=tmp)
        runner = BacktestRunner(repo, data_dir=tmp)
        config = BacktestConfig(
            symbol="TEST",
            timeframe="15m",
            start_date="2025-01-01",
            end_date="2025-01-02",
            initial_capital=1_000_000,
        )
        result = runner.run(config, (rec.id,))
        assert result.has_error
        assert result.results == ()


def test_runner_vm_is_only_path():
    # Prove no builtin import and VM is used
    import pathlib

    runner_text = pathlib.Path("06_backtest/backtest/runner.py").read_text(encoding="utf-8")
    # Should not import builtins
    assert "builtins" not in runner_text
    assert "install_builtins" not in runner_text
    assert "sma_crossover" not in runner_text.lower()
    # Should use VM path
    assert "vm_from_ir" in runner_text or "compile_strategy" in runner_text
    # Compiler must be Python-native (no IR / VM / DSL)
    comp_text = pathlib.Path("05_strategy/strategy/language/compiler.py").read_text(
        encoding="utf-8"
    )
    assert "StrategyIR" not in comp_text
    assert "vm_from_ir" not in comp_text
    assert "_CompiledLogic" not in comp_text
