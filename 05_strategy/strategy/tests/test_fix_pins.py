"""Regression pins for the 05_strategy fix batch (S01-S56 scope).

Each test pins one fixed behavior so the bug cannot silently return.
All fixtures are synthetic; no bus, no SQL, no network.
"""

from __future__ import annotations

import sys
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for entry in ("01_core", "03_market", "05_strategy"):
    candidate = str(ROOT / entry)
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import pytest  # noqa: E402

from strategy.models.definition import StrategyDefinition  # noqa: E402
from strategy.models.form import BacktestForm, StrategyDraft  # noqa: E402
from strategy.models.parameters import (  # noqa: E402
    ParameterError,
    ParameterSpec,
    StrategyParameters,
)
from strategy.models.plot_event import (  # noqa: E402
    MarkerType,
    PlotEvent,
    PlotLifecycle,
    PlotType,
    PlotValidationError,
)
from strategy.models.signal import Signal, SignalKind  # noqa: E402
from strategy.models.state import StrategyState  # noqa: E402
from strategy.registry import StrategyRegistry, StrategyRegistryError  # noqa: E402
from strategy.runtime import BarView, StrategyRuntime  # noqa: E402
from strategy.strategies.base import PythonStrategy  # noqa: E402
from strategy.strategies.ema import EmaCrossover  # noqa: E402
from strategy.strategies.indicators import calc_rsi, calc_sma  # noqa: E402
from strategy.strategies.sma import SmaCrossover  # noqa: E402


def _bars(closes: list[float], start_minute: int = 0):
    from market import Bar

    start = datetime(2026, 1, 5, 9, 30, 0) + timedelta(minutes=start_minute)
    return tuple(
        Bar(
            symbol="T",
            open=c - 0.5,
            high=c + 0.5,
            low=c - 1.0,
            close=c,
            volume=5000,
            timestamp=(start + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"),
        )
        for i, c in enumerate(closes)
    )


def _view(bars, index: int = 0, state: StrategyState | None = None) -> BarView:
    return BarView(
        bars=bars,
        index=index,
        params=StrategyParameters({}),
        state=state or StrategyState(),
    )


# ── base: params / warmup / time-exit / SL-TP merge ──────────────────────


class _Noop(PythonStrategy):
    def on_bar_logic(self, view: BarView) -> None:  # noqa: ARG002
        return None


def test_base_rejects_invalid_params() -> None:
    bad: dict[str, float] = {"fast_period": "nan-value"}  # type: ignore[dict-item]
    with pytest.raises(ValueError):
        _Noop(bad)
    with pytest.raises(ValueError):
        _Noop({"max_history": 0})


def test_base_warmup_derives_from_periods() -> None:
    assert _Noop({"slow_period": 30, "fast_period": 10}).warmup() == 30
    assert _Noop({"rsi_period": 14}).warmup() == 14
    assert _Noop({}).warmup() == 20


def test_base_max_history_param_controls_deques() -> None:
    strat = _Noop({"max_history": 5})
    assert strat.closes.maxlen == 5


class _Direct(PythonStrategy):
    def on_bar_logic(self, view: BarView) -> Signal | None:
        self.stop_loss(9.0)
        self.take_profit(11.0)
        return Signal(index=view.index, timestamp="t", kind=SignalKind.BUY, price=10.0)


class _TimeExit(PythonStrategy):
    def __init__(self, exit_at: str = "00:00", **kw) -> None:
        super().__init__(kw.get("params"))
        self._exit_at = exit_at

    def on_bar_logic(self, view: BarView) -> None:  # noqa: ARG002
        self.time_exit(self._exit_at)
        return None


class _BuyThenExit(PythonStrategy):
    def on_bar_logic(self, view: BarView) -> None:  # noqa: ARG002
        self.buy()
        self.time_exit("00:00")
        return None


def test_base_merges_pending_sltp_into_direct_signal() -> None:
    strat = _Direct({})
    signal = strat.on_bar(_view(_bars([10.0, 10.5])))
    assert signal is not None and signal.stop_loss == 9.0 and signal.take_profit == 11.0


def test_time_exit_fires_only_without_logic_signal() -> None:
    bars = _bars([10.0, 10.5])
    open_state = StrategyState.open("LONG", 10.0, 0)
    # Logic pending buy wins; time-exit must not clobber it into a SELL.
    strat = _BuyThenExit({})
    signal = strat.on_bar(_view(bars, 1, open_state))
    assert signal is not None and signal.kind == SignalKind.BUY
    # Pure time-exit with an open position exits.
    strat2 = _TimeExit()
    signal2 = strat2.on_bar(_view(bars, 1, open_state))
    assert signal2 is not None and signal2.kind == SignalKind.SELL
    # Flat: no exit signal.
    assert strat2.on_bar(_view(bars, 1, StrategyState())) is None
    # Malformed exit time is ignored, never raises.
    strat3 = _TimeExit("25:99")
    assert strat3.on_bar(_view(bars, 1, open_state)) is None


def test_base_plot_rejects_nonfinite() -> None:
    strat = _Noop({})
    strat.on_bar(_view(_bars([10.0])))
    with pytest.raises(PlotValidationError):
        strat.plot(float("nan"), "line")
    strat.set_chart_series_meta("line", {"color": "red"})
    assert strat.get_chart_series_meta()["line"] == {"color": "red"}


def test_base_update_plot_future_bar_raises() -> None:
    strat = _Noop({})
    strat.on_bar(_view(_bars([10.0, 10.5])))
    eid = strat.plot_marker(1, 10.0, MarkerType.DOT, plot_id="m")
    with pytest.raises(ValueError):
        strat.update_plot(eid, bar_index=7)
    assert strat.update_plot("missing-id") is None


# ── runtime: window / validation / on_signal guard ──────────────────────


def test_bar_view_window_and_lookahead_guard() -> None:
    bars = _bars([1.0, 2.0, 3.0, 4.0])
    view = _view(bars, 2)
    assert len(view.window) == 3
    assert view[2].close == 3.0
    assert view[-1].close == 3.0
    with pytest.raises(IndexError):
        view[3]
    with pytest.raises(IndexError):
        view[99]


def test_runtime_rejects_bad_signal_and_keeps_state_on_none() -> None:
    class _BadIndex(PythonStrategy):
        def on_bar_logic(self, view: BarView) -> Signal | None:  # noqa: ARG002
            return Signal(index=999, timestamp="t", kind=SignalKind.BUY, price=1.0)

    class _BadPrice(PythonStrategy):
        def on_bar_logic(self, view: BarView) -> Signal | None:
            return Signal(index=view.index, timestamp="t", kind=SignalKind.BUY, price=float("nan"))

    bars = _bars([1.0] * 25)  # default warmup is 20: validation bites past it
    with pytest.raises(ValueError):
        StrategyRuntime(_BadIndex({}), StrategyParameters({})).run(bars)
    with pytest.raises(ValueError):
        StrategyRuntime(_BadPrice({}), StrategyParameters({})).run(bars)

    seen: list[StrategyState] = []

    def _on_signal(_signal: Signal, state: StrategyState):  # noqa: ARG001
        seen.append(state)
        return None  # state must stay unchanged

    rt = StrategyRuntime(_TimeExit(), StrategyParameters({}))
    out = rt.run(bars, on_signal=_on_signal)
    assert out == ()  # flat throughout: time-exit never fires
    assert seen == []


def test_runtime_warms_logic_through_warmup_bars() -> None:
    class _Counter(PythonStrategy):
        def __init__(self, params=None) -> None:
            super().__init__(params)
            self.calls = 0

        def on_bar_logic(self, view: BarView) -> None:  # noqa: ARG002
            self.calls += 1
            return None

        def warmup(self) -> int:
            return 3

    logic = _Counter({})
    bars = _bars([1.0, 2.0, 3.0, 4.0, 5.0])
    assert StrategyRuntime(logic, StrategyParameters({})).run(bars) == ()
    assert logic.calls == len(bars)


# ── registry ────────────────────────────────────────────────────────────


def _dummy_factory(params: StrategyParameters):
    return _Noop(params)


def _registry() -> StrategyRegistry:
    reg = StrategyRegistry()
    reg.register_kind("dummy", _dummy_factory, SmaCrossover.param_specs())
    return reg


def test_registry_duplicate_kind_and_guards() -> None:
    reg = _registry()
    with pytest.raises(StrategyRegistryError):
        reg.register_kind("dummy", _dummy_factory, ())
    assert reg.has_kind("dummy")
    assert "dummy" in reg.kinds
    reg.update_kind("dummy", _dummy_factory, SmaCrossover.param_specs())
    with pytest.raises(StrategyRegistryError):
        reg.update_kind("nope", _dummy_factory, ())
    reg.unregister_kind("dummy")
    assert not reg.has_kind("dummy")
    with pytest.raises(StrategyRegistryError):
        reg.unregister_kind("dummy")


def test_registry_allocation_and_duplicate_ids() -> None:
    reg = _registry()
    definition = StrategyDefinition(
        id="a",
        name="A",
        version="1.0",
        kind="dummy",
        params=StrategyParameters.from_specs(SmaCrossover.param_specs()),
    )
    reg.register_definition(definition)
    with pytest.raises(ValueError):
        reg.set_allocation("a", 150.0)
    with pytest.raises(ValueError):
        reg.set_allocation("a", -1.0)
    reg.set_allocation("a", 25.0)
    assert reg.get("a").allocation_pct == 25.0
    with pytest.raises(StrategyRegistryError):
        reg.duplicate("a", "a")
    copy = reg.duplicate("a")
    assert copy.id == "a-copy" and not copy.enabled


# ── parameters ──────────────────────────────────────────────────────────


def test_parameters_immutable_and_specs_validated() -> None:
    params = StrategyParameters({"a": 1.0})
    with pytest.raises(ParameterError):
        params._values = {}  # type: ignore[assignment]
    with pytest.raises(TypeError):
        params["b"] = 2.0  # type: ignore[index]
    with pytest.raises(ParameterError):
        ParameterSpec(key="", label="x", default=1, minimum=0, maximum=2)
    with pytest.raises(ParameterError):
        ParameterSpec(key="k", label="x", default=1, minimum=5, maximum=2)
    with pytest.raises(ParameterError):
        ParameterSpec(key="k", label="x", default=99, minimum=0, maximum=2)
    with pytest.raises(ParameterError):
        ParameterSpec(key="k", label="x", default=1, minimum=0, maximum=2, decimals=-1)


# ── indicators / sma / ema ─────────────────────────────────────────────


def test_indicator_period_guards() -> None:
    with pytest.raises(ValueError):
        calc_sma(deque([1.0]), 0)
    with pytest.raises(ValueError):
        calc_rsi(deque([1.0, 2.0]), -3)
    assert "not Wilder" in (calc_rsi.__doc__ or "")


def test_crossover_fast_slow_validated() -> None:
    bars = _bars([10.0, 11.0])
    with pytest.raises(ValueError):
        SmaCrossover({"fast_period": 30, "slow_period": 10}).on_bar_logic(_view(bars, 1))
    with pytest.raises(ValueError):
        EmaCrossover({"fast_period": 26, "slow_period": 12}).on_bar_logic(_view(bars, 1))


def test_ema_stays_silent_until_warmed() -> None:
    strat = EmaCrossover({"fast_period": 2, "slow_period": 5})
    bars = _bars([10.0] * 10)
    runtime = StrategyRuntime(strat, StrategyParameters({}))
    assert runtime.run(bars) == ()


# ── models: definition / form / state / discovery ───────────────────────


def test_model_guards() -> None:
    from strategy.research.discovery import create_discovery

    with pytest.raises(ValueError):
        StrategyDefinition(
            id="",
            name="n",
            version="1",
            kind="k",
            params=StrategyParameters({}),
        )
    with pytest.raises(ValueError):
        StrategyDefinition(
            id="i",
            name="n",
            version="1",
            kind="k",
            params=StrategyParameters({}),
            allocation_pct=101.0,
        )
    with pytest.raises(ValueError):
        BacktestForm(
            strategy_id="s",
            timeframe="15m",
            start_date="2026-02-01",
            end_date="2026-01-01",
            initial_capital=100.0,
            slippage_pct=0.0,
            commission_pct=0.0,
        )
    with pytest.raises(ValueError):
        BacktestForm(
            strategy_id="s",
            timeframe="15m",
            start_date="2026-01-01",
            end_date="2026-01-02",
            initial_capital=-5.0,
            slippage_pct=0.0,
            commission_pct=0.0,
        )
    with pytest.raises(ValueError):
        StrategyDraft(name="", version="1", kind="k", params={}, allocation_pct=10.0)
    assert StrategyState(side="long", flat=False).side == "LONG"
    with pytest.raises(ValueError):
        StrategyState(side="SIDEWAYS", flat=False)
    with pytest.raises(ValueError):
        StrategyState(flat=True, side="LONG")
    assert create_discovery("s", "v", "e", {}, "eff", confidence="HIGH").confidence == "high"
    with pytest.raises(ValueError):
        create_discovery("s", "v", "e", {}, "eff", confidence="certain")


# ── plot events ─────────────────────────────────────────────────────────


def test_plot_event_guards() -> None:
    with pytest.raises(PlotValidationError):
        PlotEvent(
            event_id="e",
            source_strategy="s",
            plot_id="z",
            plot_type=PlotType.ZONE,
            start_bar=5,
            end_bar=2,
            start_price=1.0,
            end_price=2.0,
        )
    removed = PlotEvent(
        event_id="e",
        source_strategy="s",
        plot_id="m",
        plot_type=PlotType.MARKER,
        bar_index=1,
        price=1.0,
        marker_type=MarkerType.DOT,
        lifecycle=PlotLifecycle.REMOVED,
    )
    assert removed.with_update(price=2.0).lifecycle == PlotLifecycle.REMOVED
    with pytest.raises(PlotValidationError):
        PlotEvent.from_dict(
            {
                "event_id": "e",
                "source_strategy": "s",
                "plot_id": "m",
                "plot_type": "MARKER",
                "bar_index": 1,
                "price": 1.0,
                "marker_type": "DOT",
                "metadata": [["only-one"]],
            }
        )
