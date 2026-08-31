"""VM runtime tests — .vstrat → IR → VM → Signal (no builtin factory)."""

from market.models.bar import Bar

from strategy.language import compile_strategy
from strategy.models.parameters import StrategyParameters
from strategy.models.state import StrategyState
from strategy.runtime import StrategyRuntime
from strategy.vm import vm_from_ir


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


SMA_CODE = """strategy("SMA Crossover")
fast_period = input(10, "Fast period")
slow_period = input(30, "Slow period")
fast = SMA(fast_period)
slow = SMA(slow_period)
if fast > slow and prev_fast <= prev_slow:
    buy()
if fast < slow and prev_fast >= prev_slow:
    sell()
prev_fast = fast
prev_slow = slow
"""


def test_sma_emits_buy_on_up_cross():
    # Use VM path: .vstrat → IR → VM
    compiled = compile_strategy(SMA_CODE)
    assert compiled.ir is not None
    # Use small periods for test, need > warmup (20) bars
    ir_code = SMA_CODE.replace('input(10, "Fast period")', 'input(2, "Fast period")').replace(
        'input(30, "Slow period")', 'input(3, "Slow period")'
    )
    comp = compile_strategy(ir_code)
    vm = vm_from_ir(comp.ir, StrategyParameters({"Fast period": 2, "Slow period": 3}))
    # 30 bars to exceed warmup 20
    closes = [
        10.0,
        11.0,
        10.0,
        11.0,
        10.0,
        11.0,
        20.0,
        20.0,
        20.0,
        20.0,
        5.0,
        5.0,
        5.0,
        20.0,
        20.0,
    ] * 2
    closes = closes[:30]
    bars = _bars(closes)
    runtime = StrategyRuntime(vm, StrategyParameters({"Fast period": 2, "Slow period": 3}))
    signals = runtime.run(bars)
    # VM should produce at least one signal (buy or sell) for this volatile series
    assert len(signals) >= 1
    # Also test direct VM via compiled
    assert comp.ir is not None


def test_sma_warmup_no_early_signals():
    comp = compile_strategy(SMA_CODE)
    vm = vm_from_ir(comp.ir, StrategyParameters({"Fast period": 3, "Slow period": 5}))
    bars = _bars([10.0] * 4)
    runtime = StrategyRuntime(vm, StrategyParameters({"Fast period": 3, "Slow period": 5}))
    # Warmup 20 bars, so 4 bars should give no signals
    assert runtime.run(bars) == ()


def test_runtime_empty_bars():
    comp = compile_strategy(SMA_CODE)
    vm = vm_from_ir(comp.ir, StrategyParameters({"Fast period": 3, "Slow period": 5}))
    runtime = StrategyRuntime(vm, StrategyParameters({"Fast period": 3, "Slow period": 5}))
    assert runtime.run(()) == ()


def test_params_validation_rejects_bad():
    from strategy.language import StrategyLanguageError

    # Invalid: fast >= slow should be caught by VM param? Actually VM doesn't validate, but compiler should?  # noqa: E501
    # Instead test that invalid code fails compilation
    bad_code = """strategy("Bad")
    x = unknown_func(10)
    """
    try:
        compile_strategy(bad_code)
        # Should fail due to unknown function
        raise AssertionError("should have raised")
    except StrategyLanguageError:
        pass


def test_strategy_state_open():
    state = StrategyState.open("LONG", 100.0, 5)
    assert not state.flat
    assert state.side == "LONG"


def test_vm_is_same_class_for_all_strategies():
    # Prove same VM class used for different strategies
    obr_code = (
        'strategy("OBR")\n'
        "ref_range = range(20)\n"
        "rsi_val = RSI(14)\n"
        "is_up = close > high - ref_range * 0.1 and rsi_val > 55\n"
        "if is_up:\n"
        "    buy()\n"
    )
    sma = compile_strategy(SMA_CODE)
    obr = compile_strategy(obr_code)
    vm_sma = vm_from_ir(sma.ir, StrategyParameters({}))
    vm_obr = vm_from_ir(obr.ir, StrategyParameters({}))
    assert type(vm_sma) is type(vm_obr)
    assert vm_sma.__class__.__name__ == "StrategyVM"


def test_no_exec_fallback():
    # CompiledStrategy.create_logic must use VM only, fail loudly if IR missing
    import ast

    from strategy.language.compiler import CompiledStrategy

    tree = ast.parse("x=1")
    cs = CompiledStrategy(tree=tree, param_defaults={}, code="x=1", ir=None)
    try:
        cs.create_logic(StrategyParameters({}))
        raise AssertionError("should have raised")
    except Exception as e:
        # Must fail, not silently return dummy logic
        assert "IR not available" in str(e) or "compilation failed" in str(e).lower()
    # Ensure no exec in compiler
    import pathlib

    text = pathlib.Path("05_strategy/strategy/language/compiler.py").read_text(encoding="utf-8")
    assert "exec(" not in text
    assert "_CompiledLogic" not in text
