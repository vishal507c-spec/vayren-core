"""Batch parity — the bounded parallel batch must match sequential runs exactly.

OLD (BacktestRunner.run, one symbol at a time) vs NEW (run_symbol_batch,
inline and pooled): same candles, same bars, same trades, same equity,
same metrics, same visuals. Only scheduling may differ.
"""



import sqlite3
import tempfile
from pathlib import Path

from market.repository.symbol_repository import SymbolRepository
from strategy.language.storage import create_strategy

from backtest.models.config import BacktestConfig
from backtest.runner import BacktestRunner, BatchSpec, run_symbol_batch

CTA_CODE = """from strategy.strategies.base import PythonStrategy
from strategy.strategies.indicators import calc_range, calc_rsi

class Strategy(PythonStrategy):
    @staticmethod
    def param_specs():
        from strategy.models.parameters import ParameterSpec
        return (
            ParameterSpec(key="c1_thresh", label="C1 Range", default=1.25,
                          minimum=0.1, maximum=5.0, decimals=2),
        )
    def on_bar_logic(self, view):
        bar = view.bar
        c1 = float(self.params.get("c1_thresh", 1.25))
        rsi = calc_rsi(self.closes, 14)
        ch_range = calc_range(self.highs, self.lows, 20)
        if bar.close >= (bar.high - ch_range * 0.15 * c1) and rsi >= 60 and bar.volume >= 100:
            self.sell()
            self.stop_loss(bar.close + c1 * (bar.high - bar.low))
            self.take_profit(bar.close - 2 * c1 * (bar.high - bar.low))
            return
        if bar.close < (bar.high + bar.low) / 2 and rsi < 50:
            self.close_position(view)
            return
        self.time_exit("15:15")
"""


def _seed_repo(tmp: Path, closes_by_symbol: dict[str, list[float]]) -> SymbolRepository:
    import datetime

    tmp.mkdir(parents=True, exist_ok=True)
    base = datetime.date(2026, 1, 5)  # a Monday — weekdays only
    for symbol, closes in closes_by_symbol.items():
        db = tmp / f"{symbol}.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE ohlcv (candle_time TEXT PRIMARY KEY, open REAL, "
            "high REAL, low REAL, close REAL, volume INTEGER);"
        )
        day = base
        i = 0
        while i < len(closes):
            if day.weekday() < 5:
                c = closes[i]
                # two intraday bars per day so 30m aggregation has work to do
                conn.execute(
                    "INSERT INTO ohlcv VALUES (?, ?, ?, ?, ?, ?)",
                    (f"{day.isoformat()} 09:15:00", c - 1, c + 2, c - 2, c, 1500),
                )
                conn.execute(
                    "INSERT INTO ohlcv VALUES (?, ?, ?, ?, ?, ?)",
                    (f"{day.isoformat()} 09:30:00", c, c + 3, c - 1, c + 0.5, 1800),
                )
                i += 1
            day += datetime.timedelta(days=1)
        conn.commit()
        conn.close()
    return SymbolRepository(tmp)


def _closes(seed: int, n: int) -> list[float]:
    out = []
    price = 100.0 + seed
    for i in range(n):
        price += ((i * 37 + seed * 11) % 9) - 4.0
        out.append(round(price, 2))
    # inject a spike so SL/TP paths trigger
    out[20] = out[19] + 12.0
    out[40] = out[39] - 12.0
    return out


def _setup(tmp: Path):
    repo = _seed_repo(
        tmp,
        {
            "AAA": _closes(1, 60),
            "BBB": _closes(2, 60),
            "CCC": _closes(3, 60),
        },
    )
    rec = create_strategy("BatchParityCTA", CTA_CODE, data_dir=tmp)
    return repo, rec


def _old_results(repo, rec, tmp: Path, timeframe: str):
    runner = BacktestRunner(repo, data_dir=tmp)
    out = {}
    for symbol in ("AAA", "BBB", "CCC"):
        config = BacktestConfig(
            symbol=symbol,
            timeframe=timeframe,
            start_date="2026-01-01",
            end_date="2026-04-30",
            initial_capital=1_000_000,
        )
        result = runner.run(config, (rec.id,))
        assert not result.has_error, symbol
        out[symbol] = result.results[0]
    return out


def _spec(rec, timeframe: str, **kwargs):
    return BatchSpec(
        strategy_id=rec.id,
        strategy_name=rec.name,
        strategy_version=rec.version,
        strategy_code=rec.code,
        symbols=("AAA", "BBB", "CCC"),
        timeframe=timeframe,
        start_date="2026-01-01",
        end_date="2026-04-30",
        initial_capital=1_000_000.0,
        **kwargs,
    )


def _assert_same(old, outcome, *, visuals: bool):
    assert outcome.error is None, (outcome.symbol, outcome.error)
    new = outcome.result
    assert new is not None
    assert new.strategy_id == old.strategy_id
    assert new.name == old.name
    assert new.config == old.config
    assert new.bars_used == old.bars_used
    assert new.period_start == old.period_start
    assert new.period_end == old.period_end
    assert [t.to_dict() for t in new.trades] == [t.to_dict() for t in old.trades]
    assert tuple(new.equity_curve) == tuple(old.equity_curve)
    assert new.metrics == old.metrics
    if visuals:
        assert [dict(s.values) if hasattr(s, "values") else s for s in new.chart_series] == [
            dict(s.values) if hasattr(s, "values") else s for s in old.chart_series
        ]
        assert [str(s) for s in new.chart_series] == [str(s) for s in old.chart_series]
        assert [str(p) for p in new.chart_plots] == [str(p) for p in old.chart_plots]
        assert tuple(new.muted_bars) == tuple(old.muted_bars)
    else:
        assert new.chart_series == ()
        assert new.chart_plots == ()
        assert new.muted_bars == ()


def test_batch_inline_matches_sequential():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        repo, rec = _setup(tmp)
        for timeframe in ("15m", "30m"):
            old = _old_results(repo, rec, tmp, timeframe)
            outcomes = run_symbol_batch(
                str(tmp), _spec(rec, timeframe, include_plots=True, max_workers=1)
            )
            assert [o.symbol for o in outcomes] == ["AAA", "BBB", "CCC"]
            for outcome in outcomes:
                _assert_same(old[outcome.symbol], outcome, visuals=True)


def test_batch_pooled_matches_sequential():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        repo, rec = _setup(tmp)
        old = _old_results(repo, rec, tmp, "30m")
        seen: list[tuple[int, int]] = []
        outcomes = run_symbol_batch(
            str(tmp),
            _spec(rec, "30m", max_workers=2),
            on_stock=lambda done, total: seen.append((done, total)),
        )
        assert [o.symbol for o in outcomes] == ["AAA", "BBB", "CCC"]
        assert sorted(seen)[-1] == (3, 3)
        for outcome in outcomes:
            _assert_same(old[outcome.symbol], outcome, visuals=False)


def test_batch_is_deterministic():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _, rec = _setup(tmp)
        first = run_symbol_batch(str(tmp), _spec(rec, "30m", max_workers=2))
        second = run_symbol_batch(str(tmp), _spec(rec, "30m", max_workers=2))
        for a, b in zip(first, second, strict=True):
            assert a.symbol == b.symbol
            assert a.error == b.error
            ta = [t.to_dict() for t in a.result.trades] if a.result else None
            tb = [t.to_dict() for t in b.result.trades] if b.result else None
            assert ta == tb
            ma = a.result.metrics if a.result else None
            mb = b.result.metrics if b.result else None
            assert ma == mb


def test_batch_error_isolation():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _, rec = _setup(tmp)
        spec = BatchSpec(
            strategy_id=rec.id,
            strategy_name=rec.name,
            strategy_version=rec.version,
            strategy_code=rec.code,
            symbols=("AAA", "MISSING", "CCC"),
            timeframe="30m",
            start_date="2026-01-01",
            end_date="2026-04-30",
            max_workers=2,
        )
        outcomes = run_symbol_batch(str(tmp), spec)
        by_symbol = {o.symbol: o for o in outcomes}
        assert by_symbol["AAA"].error is None
        assert by_symbol["CCC"].error is None
        assert by_symbol["MISSING"].error is not None
        assert by_symbol["MISSING"].result is None


def test_batch_cancel_marks_remaining():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _, rec = _setup(tmp)
        calls = {"n": 0}

        def _cancel() -> bool:
            calls["n"] += 1
            return True  # cancel immediately: first stock runs, rest cancelled

        outcomes = run_symbol_batch(
            str(tmp), _spec(rec, "30m", max_workers=1), should_cancel=_cancel
        )
        assert [o.symbol for o in outcomes] == ["AAA", "BBB", "CCC"]
        assert outcomes[1].error == "cancelled"
        assert outcomes[2].error == "cancelled"
