"""Backtest orchestration tests (synthetic SQLite store, real kernels)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for entry in (
    "00_app",
    "01_core",
    "03_market",
    "05_strategy",
    "06_backtest",
):
    candidate = str(ROOT / entry)
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import pytest  # noqa: E402

from app.services.backtest_service import (  # noqa: E402
    BacktestError,
    describe_strategy,
    run_backtest,
)


def _write_trend_db(path: Path, days: int = 60) -> None:
    con = sqlite3.connect(str(path))
    con.execute(
        "CREATE TABLE ohlcv(candle_time TEXT PRIMARY KEY, open REAL NOT NULL,"
        " high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,"
        " volume INTEGER NOT NULL)"
    )
    from datetime import date, timedelta

    rows = []
    price = 100.0
    for day in range(days):
        day_label = (date(2026, 1, 1) + timedelta(days=day)).strftime("%Y-%m-%d")
        for slot in range(25):
            hour = 9 + (15 * (slot + 1) + 15) // 60
            minute = (15 * (slot + 1) + 15) % 60
            stamp = f"{day_label} {hour:02d}:{minute:02d}:00"
            # Sawtooth: trends up, then snaps back (guaranteed crossovers).
            wave = (day + slot) % 20
            price = 100.0 + (wave if wave < 10 else 20 - wave)
            rows.append((stamp, price - 0.5, price + 0.5, price - 1.0, price, 5000))
    con.executemany(
        "INSERT INTO ohlcv(candle_time, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    con.commit()
    con.close()


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    _write_trend_db(tmp_path / "TREND.db")
    return tmp_path


def test_builtin_strategy_runs_on_real_bars(store: Path, tmp_path: Path) -> None:
    results = run_backtest(
        "SMA Crossover",
        ["TREND"],
        "15m",
        None,
        None,
        1000000.0,
        "buy",
        store,
        tmp_path,
    )
    metrics = results["metrics"]
    assert metrics["total_trades"] > 0
    assert metrics["winning_trades"] + metrics["losing_trades"] == metrics["total_trades"]
    assert len(results["trades"]) == metrics["total_trades"]
    assert len(results["equity_curve"]) == metrics["total_trades"] + 1
    assert results["ranking"][0]["symbol"] == "TREND"
    first = results["trades"][0]
    assert first["side"] == "LONG"
    assert first["exit_time"] >= first["entry_time"]


def test_rsi_and_ema_builtins_run(store: Path, tmp_path: Path) -> None:
    for name in ("RSI Strategy", "EMA Crossover"):
        results = run_backtest(
            name, ["TREND"], "15m", None, None, 1000000.0, "buy", store, tmp_path
        )
        assert results["metrics"]["total_trades"] >= 0
        assert results["ranking"][0]["symbol"] == "TREND"


def test_sell_mode_shorts(store: Path, tmp_path: Path) -> None:
    results = run_backtest(
        "SMA Crossover", ["TREND"], "15m", None, None, 1000000.0, "sell", store, tmp_path
    )
    assert all(t["side"] == "SHORT" for t in results["trades"])


def test_insufficient_data_fails_closed(tmp_path: Path) -> None:
    con = sqlite3.connect(str(tmp_path / "TINY.db"))
    con.execute(
        "CREATE TABLE ohlcv(candle_time TEXT PRIMARY KEY, open REAL NOT NULL,"
        " high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,"
        " volume INTEGER NOT NULL)"
    )
    con.execute("INSERT INTO ohlcv VALUES ('2026-01-05 09:30:00', 1, 2, 0.5, 1.5, 10)")
    con.commit()
    con.close()
    with pytest.raises(BacktestError, match="Insufficient data"):
        run_backtest(
            "SMA Crossover",
            ["TINY"],
            "15m",
            None,
            None,
            1000000.0,
            "buy",
            tmp_path,
            tmp_path,
        )


def test_unknown_strategy_and_bad_inputs_fail_actionably(store: Path, tmp_path: Path) -> None:
    with pytest.raises(BacktestError, match="Unknown strategy"):
        run_backtest("Nope", ["TREND"], "15m", None, None, 1000000.0, "buy", store, tmp_path)
    with pytest.raises(BacktestError, match="No universe"):
        run_backtest("SMA Crossover", [], "15m", None, None, 1000000.0, "buy", store, tmp_path)
    with pytest.raises(BacktestError, match="capital must be positive"):
        run_backtest("SMA Crossover", ["TREND"], "15m", None, None, 0.0, "buy", store, tmp_path)
    with pytest.raises(BacktestError, match="after end date"):
        run_backtest(
            "SMA Crossover",
            ["TREND"],
            "15m",
            "2026-02-01",
            "2026-01-01",
            1000000.0,
            "buy",
            store,
            tmp_path,
        )


def test_describe_strategy_reports_params(tmp_path: Path) -> None:
    detail = describe_strategy("EMA Crossover", tmp_path)
    assert detail["kind"] == "built-in"
    assert {p["key"] for p in detail["params"]} == {
        "fast_period",
        "slow_period",
        "min_volume",
    }
    assert "EmaCrossover" in detail["code"]
