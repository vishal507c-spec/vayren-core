"""SmaCrossover min_volume filter — default-off parity + skip behavior."""

from market.models.bar import Bar

from strategy.models.parameters import StrategyParameters
from strategy.models.state import StrategyState
from strategy.runtime import BarView
from strategy.strategies.sma import SmaCrossover


def _bars(closes: list[float], volumes: list[int] | None = None) -> tuple[Bar, ...]:
    vols = volumes if volumes is not None else [1000] * len(closes)
    return tuple(
        Bar(
            symbol="TEST",
            open=c,
            high=c + 1,
            low=c - 1,
            close=c,
            volume=v,
            timestamp=f"2026-01-{(i % 28) + 1:02d} 09:15:00",
        )
        for i, (c, v) in enumerate(zip(closes, vols, strict=True))
    )


def _run(strategy: SmaCrossover, bars: tuple[Bar, ...]) -> None:
    params = StrategyParameters(dict(strategy.params))
    for idx in range(len(bars)):
        strategy.on_bar(BarView(bars=bars, index=idx, params=params, state=StrategyState()))


def _closes() -> list[float]:
    return [10.0, 11.0, 10.0, 11.0, 10.0, 11.0, 20.0, 20.0, 20.0, 20.0, 5.0, 5.0]


def test_default_zero_matches_no_filter() -> None:
    bars = _bars(_closes())
    plain = SmaCrossover(StrategyParameters({"fast_period": 2, "slow_period": 3}))
    explicit = SmaCrossover(
        StrategyParameters({"fast_period": 2, "slow_period": 3, "min_volume": 0})
    )
    _run(plain, bars)
    _run(explicit, bars)
    assert (explicit.prev_fast, explicit.prev_slow) == (plain.prev_fast, plain.prev_slow)


def test_high_threshold_skips_everything_and_keeps_prev_none() -> None:
    strat = SmaCrossover(
        StrategyParameters({"fast_period": 2, "slow_period": 3, "min_volume": 999999})
    )
    _run(strat, _bars(_closes()))
    assert strat.prev_fast is None
    assert strat.prev_slow is None
    # base history still recorded every bar
    assert len(strat.closes) == len(_closes())


def test_partial_filter_skips_only_thin_bars() -> None:
    closes = _closes()
    volumes = [1000] * len(closes)
    volumes[3] = 1
    strat = SmaCrossover(
        StrategyParameters({"fast_period": 2, "slow_period": 3, "min_volume": 500})
    )
    _run(strat, _bars(closes, volumes))
    assert len(strat.closes) == len(closes)
    assert strat.prev_fast is not None


def test_min_volume_spec_registered() -> None:
    specs = {s.key: s for s in SmaCrossover.param_specs()}
    assert specs["min_volume"].default == 0
    assert specs["min_volume"].minimum == 0
