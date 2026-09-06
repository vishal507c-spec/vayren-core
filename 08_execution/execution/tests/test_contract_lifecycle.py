"""Contract inspector + lifecycle tests."""

import pytest
from strategy import StrategyParameters
from strategy.models.definition import StrategyDefinition
from strategy.strategies.sma import SmaCrossover

from execution.runtime.inspector import (
    describe_requirements,
    evaluate_signal_requirements,
    inspect_strategy,
)
from execution.runtime.lifecycle import LifecycleError, LifecycleState, StrategyLifecycle


def _definition() -> StrategyDefinition:
    return StrategyDefinition(
        id="sma-crossover",
        name="SMA",
        version="1.0",
        kind="sma",
        params=StrategyParameters({"fast_period": 2, "slow_period": 3, "min_volume": 0}),
    )


def test_inspect_sma_derives_warmup_params_and_defaults() -> None:
    logic = SmaCrossover(StrategyParameters({"fast_period": 2, "slow_period": 3}))
    contract = inspect_strategy(_definition(), logic)
    assert contract.strategy_id == "sma-crossover"
    assert contract.strategy_version == "1.0"
    assert contract.warmup_bars == logic.warmup() == 20
    assert set(contract.parameters) == {"fast_period", "slow_period", "min_volume"}
    assert contract.data_requirements == ("candle-close",)
    assert contract.order_types == ("MARKET",)
    assert contract.supports_paper is True
    assert contract.supports_live is False
    assert contract.complete  # no gaps


def test_missing_logic_is_reported_not_raised() -> None:
    contract = inspect_strategy(_definition(), None)
    assert not contract.complete
    assert "strategy_logic" in contract.missing
    text = describe_requirements(contract)
    assert "MISSING: strategy_logic" in text


def test_describe_answers_what_strategy_needs() -> None:
    logic = SmaCrossover(StrategyParameters({}))
    text = describe_requirements(inspect_strategy(_definition(), logic))
    assert "sma-crossover" in text
    assert "warmup: 20 bars" in text
    assert "candle-close" in text


def test_signal_requirements_cover_state() -> None:
    logic = SmaCrossover(StrategyParameters({}))
    requirements = evaluate_signal_requirements(inspect_strategy(_definition(), logic))
    assert "candle-close events" in requirements
    assert "position state per bar" in requirements


def test_lifecycle_happy_path() -> None:
    lifecycle = StrategyLifecycle()
    assert lifecycle.state == LifecycleState.CREATED
    assert not lifecycle.live
    for target in (
        LifecycleState.VALIDATING,
        LifecycleState.WARMING_UP,
        LifecycleState.READY,
        LifecycleState.RUNNING,
    ):
        lifecycle.transition(target)
    assert lifecycle.live
    lifecycle.transition(LifecycleState.PAUSED)
    assert not lifecycle.live
    lifecycle.transition(LifecycleState.RUNNING)
    lifecycle.transition(LifecycleState.STOPPING)
    lifecycle.transition(LifecycleState.STOPPED)
    assert lifecycle.state == LifecycleState.STOPPED


def test_lifecycle_rejects_illegal_jumps() -> None:
    lifecycle = StrategyLifecycle()
    with pytest.raises(LifecycleError):
        lifecycle.transition(LifecycleState.RUNNING)  # CREATED -> RUNNING skips validation
    with pytest.raises(LifecycleError):
        lifecycle.transition(LifecycleState.READY)
    lifecycle.transition(LifecycleState.VALIDATING)
    lifecycle.transition(LifecycleState.ERROR, reason="no warmup data")
    lifecycle.transition(LifecycleState.RECOVERING)
    lifecycle.transition(LifecycleState.VALIDATING)


def test_lifecycle_terminal_states_stick() -> None:
    lifecycle = StrategyLifecycle()
    lifecycle.transition(LifecycleState.STOPPED)
    with pytest.raises(LifecycleError):
        lifecycle.transition(LifecycleState.RUNNING)
    # STOPPED may only recover, never resume directly
    lifecycle.transition(LifecycleState.RECOVERING)
    assert lifecycle.state == LifecycleState.RECOVERING
