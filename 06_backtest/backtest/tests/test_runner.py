"""Backtest runner end-to-end with a real repository."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3
import tempfile
from pathlib import Path

from market.repository.symbol_repository import SymbolRepository
from strategy.builtins import SMA_CROSSOVER_KIND, SMA_CROSSOVER_SPECS, install_builtins
from strategy.models.definition import StrategyDefinition
from strategy.models.parameters import StrategyParameters
from strategy.registry import StrategyRegistry

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


def test_runner_produces_metrics():
    with tempfile.TemporaryDirectory() as d:
        closes = [100.0 + (i % 10) for i in range(60)]
        closes[10:15] = [120.0, 122.0, 125.0, 123.0, 121.0]
        repo = _seed_repo(Path(d), "TEST", closes)
        registry = StrategyRegistry()
        install_builtins(registry)
        definition = StrategyDefinition(
            id="sma-crossover",
            name="SMA Crossover",
            version="1.0",
            kind=SMA_CROSSOVER_KIND,
            params=StrategyParameters.from_specs(SMA_CROSSOVER_SPECS),
        )
        registry.register_definition(definition)
        runner = BacktestRunner(repo, registry)
        config = BacktestConfig(
            symbol="TEST",
            timeframe="15m",
            start_date="2026-01-01",
            end_date="2026-01-30",
            initial_capital=1_000_000,
        )
        result = runner.run(config, ("sma-crossover",))
        assert not result.has_error
        assert len(result.results) == 1
        strategy_result = result.results[0]
        assert strategy_result.bars_used > 0
        assert strategy_result.equity_curve
        assert strategy_result.metrics.starting_capital == 1_000_000


def test_runner_empty_range():
    with tempfile.TemporaryDirectory() as d:
        repo = _seed_repo(Path(d), "TEST", [100.0, 101.0, 102.0])
        registry = StrategyRegistry()
        install_builtins(registry)
        registry.register_definition(
            StrategyDefinition(
                id="s",
                name="S",
                version="1.0",
                kind=SMA_CROSSOVER_KIND,
                params=StrategyParameters.from_specs(SMA_CROSSOVER_SPECS),
            )
        )
        runner = BacktestRunner(repo, registry)
        config = BacktestConfig(
            symbol="TEST",
            timeframe="15m",
            start_date="2025-01-01",
            end_date="2025-01-02",
            initial_capital=1_000_000,
        )
        result = runner.run(config, ("s",))
        assert result.has_error
