"""Built-in strategy tests — permanently empty (OBR/OBR C1C4 only).

The system cleanup (2026-11-08) removed the SMA/EMA/RSI built-ins. These
tests pin the new contract: no built-ins exist, the registry ships
exactly OBR + OBR C1C4, and a local test strategy still drives the
real runtime path.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from strategy import builtins  # noqa: E402
from strategy.models.parameters import StrategyParameters  # noqa: E402
from strategy.models.state import StrategyState  # noqa: E402
from strategy.registry import KEEP_STRATEGY_NAMES, get_strategy_registry  # noqa: E402
from strategy.runtime import StrategyRuntime  # noqa: E402
from strategy.strategies.base import PythonStrategy  # noqa: E402


def test_builtin_registry_is_permanently_empty() -> None:
    assert builtins.list_builtins() == ()
    assert not builtins.is_builtin("SMA Crossover")
    assert not builtins.is_builtin("OBR")
    with pytest.raises(KeyError):
        builtins.get_builtin("SMA Crossover")
    with pytest.raises(KeyError):
        builtins.builtin_source("EMA Crossover")


def test_registry_ships_exactly_obr_and_obr_c1c4(tmp_path: Path) -> None:
    assert KEEP_STRATEGY_NAMES == ("OBR", "OBR C1C4")
    reg = get_strategy_registry(data_dir=tmp_path)
    names = {d.name for d in reg.list()}
    assert names <= set(KEEP_STRATEGY_NAMES)
    assert "OBR C1C4" in names
    # Removed strategies must never resolve.
    for gone in ("SMA Crossover", "EMA Crossover", "RSI Strategy"):
        assert not reg.contains(gone)


class _BuyOnUpside(PythonStrategy):
    """Local test-only strategy for the real runtime path."""

    @staticmethod
    def param_specs():
        return ()

    def on_bar_logic(self, view):
        if view.bar.close > view.bar.open:
            self.buy()


def _bars(closes: list[float]):
    from datetime import datetime, timedelta

    from market import Bar

    start = datetime(2026, 1, 5, 9, 30, 0)
    return tuple(
        Bar(
            symbol="T",
            open=c - 0.5,
            high=c + 0.5,
            low=c - 1.0,
            close=c,
            volume=5000,
            timestamp=(start + timedelta(minutes=index)).strftime("%Y-%m-%d %H:%M:%S"),
        )
        for index, c in enumerate(closes)
    )


def test_local_strategy_signals_through_real_runtime() -> None:
    logic = _BuyOnUpside(StrategyParameters({}))
    runtime = StrategyRuntime(logic, StrategyParameters({}))
    signals = runtime.run(_bars([10.0] * 30 + [11.0] * 10))
    kinds = {s.kind.name for s in signals}
    assert kinds == {"BUY"}


def test_bar_view_state_flows_through_runtime() -> None:
    closes = [10.0] * 40 + [20.0] * 10
    logic = _BuyOnUpside(StrategyParameters({}))
    bars = _bars(closes)
    seen_states = []

    def on_signal(signal, state):
        seen_states.append(state)
        return StrategyState.open("LONG", signal.price, signal.index)

    runtime = StrategyRuntime(logic, StrategyParameters({}))
    runtime.run(bars, on_signal=on_signal)
    assert seen_states, "expected at least one signal with position feedback"
