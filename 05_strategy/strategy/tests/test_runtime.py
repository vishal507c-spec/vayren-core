"""SMA crossover and strategy runtime tests."""

from market.models.bar import Bar

from strategy.builtins.sma_crossover import sma_crossover_factory
from strategy.models.parameters import StrategyParameters
from strategy.models.signal import SignalKind
from strategy.models.state import StrategyState
from strategy.runtime import StrategyRuntime


def _bars(closes: list[float]) -> tuple[Bar, ...]:
    return tuple(
        Bar(
            symbol="TEST",
            open=c,
            high=c + 1,
            low=c - 1,
            close=c,
            volume=1000,
            timestamp=f"2026-01-{i + 1:02d} 09:15:00",
        )
        for i, c in enumerate(closes)
    )


def test_sma_emits_buy_on_up_cross():
    closes = [10.0, 11.0, 10.0, 11.0, 10.0, 11.0, 20.0, 20.0, 20.0, 20.0, 5.0, 5.0, 5.0, 20.0, 20.0]
    bars = _bars(closes)
    logic = sma_crossover_factory(StrategyParameters({"fast_period": 2, "slow_period": 3}))
    runtime = StrategyRuntime(logic, StrategyParameters({"fast_period": 2, "slow_period": 3}))
    signals = runtime.run(bars)
    kinds = [s.kind for s in signals]
    assert SignalKind.BUY in kinds


def test_sma_warmup_no_early_signals():
    bars = _bars([10.0] * 4)
    logic = sma_crossover_factory(StrategyParameters({"fast_period": 3, "slow_period": 5}))
    runtime = StrategyRuntime(logic, StrategyParameters({"fast_period": 3, "slow_period": 5}))
    assert runtime.run(bars) == ()


def test_runtime_empty_bars():
    logic = sma_crossover_factory(StrategyParameters({"fast_period": 3, "slow_period": 5}))
    runtime = StrategyRuntime(logic, StrategyParameters({"fast_period": 3, "slow_period": 5}))
    assert runtime.run(()) == ()


def test_params_validation_rejects_bad():
    from strategy.models.parameters import ParameterError

    try:
        sma_crossover_factory(StrategyParameters({"fast_period": 10, "slow_period": 5}))
        raise AssertionError("should have raised")
    except (ValueError, ParameterError):
        pass


def test_strategy_state_open():
    state = StrategyState.open("LONG", 100.0, 5)
    assert not state.flat
    assert state.side == "LONG"
