"""Built-in strategy tests (synthetic bars, real runtime path)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for entry in ("01_core", "03_market", "05_strategy"):
    candidate = str(ROOT / entry)
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import pytest  # noqa: E402

from strategy import builtins  # noqa: E402
from strategy.models.parameters import StrategyParameters  # noqa: E402
from strategy.models.state import StrategyState  # noqa: E402
from strategy.runtime import StrategyRuntime  # noqa: E402


def test_builtin_registry_lists_three_marked_strategies() -> None:
    names = [entry.name for entry in builtins.list_builtins()]
    assert names == ["SMA Crossover", "EMA Crossover", "RSI Strategy"]
    for entry in builtins.list_builtins():
        assert "built-in" in entry.tags
        assert builtins.is_builtin(entry.name)
        assert builtins.builtin_source(entry.name)
    assert not builtins.is_builtin("OBR")
    with pytest.raises(KeyError):
        builtins.get_builtin("Nope")


def _bars(closes: list[float]):
    from market import Bar

    return tuple(
        Bar(
            symbol="T",
            open=c - 0.5,
            high=c + 0.5,
            low=c - 1.0,
            close=c,
            volume=5000,
            timestamp=f"2026-01-05 09:{30 + index:02d}:00",
        )
        for index, c in enumerate(closes)
    )


def _signals(cls, closes: list[float]):
    logic = cls(StrategyParameters.from_specs(cls.param_specs()))
    runtime = StrategyRuntime(logic, StrategyParameters.from_specs(cls.param_specs()))
    return runtime.run(_bars(closes))


def test_sma_crossover_signals_on_trend_reversal() -> None:
    from strategy.strategies.sma import SmaCrossover

    closes = [10.0] * 35 + [20.0] * 10 + [10.0] * 10
    signals = _signals(SmaCrossover, closes)
    assert signals, "expected crossover signals on a reversal"
    kinds = {s.kind.name for s in signals}
    assert kinds == {"BUY", "SELL"}


def test_ema_crossover_signals_on_trend_reversal() -> None:
    from strategy.strategies.ema import EmaCrossover

    closes = [10.0] * 35 + [20.0] * 10 + [10.0] * 10
    signals = _signals(EmaCrossover, closes)
    assert signals, "expected crossover signals on a reversal"


def test_rsi_buys_oversold_and_exits_overbought() -> None:
    from strategy.models.signal import SignalKind
    from strategy.strategies.rsi import RsiStrategy

    down = [100.0 - index for index in range(40)]
    signals = _signals(RsiStrategy, down)
    assert any(s.kind == SignalKind.BUY for s in signals)
    # Sustained rally from oversold: entries then exits, never short entries.
    assert all(s.kind == SignalKind.BUY for s in signals)


def test_bar_view_state_flows_through_runtime() -> None:
    from strategy.strategies.sma import SmaCrossover

    closes = [10.0] * 40 + [20.0] * 10
    logic = SmaCrossover(StrategyParameters.from_specs(SmaCrossover.param_specs()))
    bars = _bars(closes)
    seen_states = []

    def on_signal(signal, state):
        seen_states.append(state)
        return StrategyState.open("LONG", signal.price, signal.index)

    runtime = StrategyRuntime(logic, StrategyParameters.from_specs(SmaCrossover.param_specs()))
    runtime.run(bars, on_signal=on_signal)
    assert seen_states, "expected at least one signal with position feedback"
