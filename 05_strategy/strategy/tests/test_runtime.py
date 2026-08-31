"""Python-native strategy runtime tests — no DSL, no VM."""

from market.models.bar import Bar

from strategy.language import compile_strategy
from strategy.models.parameters import StrategyParameters
from strategy.runtime import StrategyRuntime


def _bars(closes: list[float]) -> tuple[Bar, ...]:
    return tuple(
        Bar(symbol="TEST", open=c, high=c + 1, low=c - 1, close=c, volume=1000, timestamp=f"2026-01-{i + 1:02d} 09:15:00")
        for i, c in enumerate(closes)
    )


SMA_PYTHON_CODE = """
from strategy.strategies.base import PythonStrategy
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


def test_sma_emits_buy_on_up_cross():
    compiled = compile_strategy(SMA_PYTHON_CODE)
    assert compiled.strategy_class is not None
    strat = compiled.create_logic(StrategyParameters({"fast_period": 2, "slow_period": 3}))
    closes = [10, 11, 10, 11, 10, 11, 20, 20, 20, 20, 5, 5, 5, 20, 20] * 2
    closes = closes[:30]
    bars = _bars(closes)
    runtime = StrategyRuntime(strat, StrategyParameters({"fast_period": 2, "slow_period": 3}))
    signals = runtime.run(bars)
    assert len(signals) >= 1


def test_sma_warmup_no_early_signals():
    compiled = compile_strategy(SMA_PYTHON_CODE)
    strat = compiled.create_logic(StrategyParameters({"fast_period": 3, "slow_period": 5}))
    bars = _bars([10.0] * 4)
    runtime = StrategyRuntime(strat, StrategyParameters({"fast_period": 3, "slow_period": 5}))
    assert runtime.run(bars) == ()


def test_runtime_empty_bars():
    compiled = compile_strategy(SMA_PYTHON_CODE)
    strat = compiled.create_logic(StrategyParameters({"fast_period": 3, "slow_period": 5}))
    runtime = StrategyRuntime(strat, StrategyParameters({"fast_period": 3, "slow_period": 5}))
    assert runtime.run(()) == ()


def test_python_strategy_no_vm():
    import pathlib

    text = pathlib.Path("05_strategy/strategy/language/compiler.py").read_text(encoding="utf-8")
    assert "vm_from_ir" not in text
    assert "StrategyIR" not in text
    assert "vm.py" not in text.lower()
    # Ensure no .vstrat handling in storage
    storage = pathlib.Path("05_strategy/strategy/language/storage.py").read_text(encoding="utf-8")
    assert ".vstrat" not in storage
    assert ".py" in storage


def test_strategy_state_open():
    from strategy.models.state import StrategyState

    state = StrategyState.open("LONG", 100.0, 5)
    assert not state.flat
    assert state.side == "LONG"


def test_no_vstrat_dependency():
    import pathlib

    for p in pathlib.Path("05_strategy").rglob("*.py"):
        if "tests" in p.parts:
            continue
        if p.name == "test_runtime.py":
            continue
        text = p.read_text(encoding="utf-8")
        assert ".vstrat" not in text, f"{p} still references .vstrat"
        assert "StrategyIR" not in text, f"{p} still references StrategyIR"
        assert "vm_from_ir" not in text, f"{p} still references vm"
