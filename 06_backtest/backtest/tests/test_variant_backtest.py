"""run_variant_backtest — config wiring, trade collection, registry fallback.

Uses a stubbed BacktestRunner (monkeypatched) so this tests the helper's
own logic, not the runner pipeline (covered by test_runner.py).
"""

from types import SimpleNamespace
from typing import Any

import pytest
from strategy import ResearchDataset

import backtest.runner as runner_module
from backtest.models.config import BacktestConfig
from backtest.runner import run_variant_backtest


def _dataset() -> ResearchDataset:
    return ResearchDataset(
        strategy_id="strat-1",
        version_id="v1",
        execution_ids=("exec-0",),
        trades=(),
        signals=("s1",),
        parameters={"p": 1.0},
        data_identity={
            "symbol": "T",
            "timeframe": "1D",
            "start_date": "2020-01-01",
            "end_date": "2020-12-31",
            "slippage_pct": 0.1,
            "commission_pct": 0.2,
        },
        metadata={"variant": 0.5, "param": "p"},
    )


class _FakeRegistry:
    def __init__(self, definition: object) -> None:
        self._definition = definition
        self.lookups: list[str] = []

    def get(self, key: str) -> object:
        self.lookups.append(key)
        return self._definition


class _StubRunner:
    last: dict[str, Any] | None = None

    def __init__(self, repository: object, registry: object) -> None:
        _StubRunner.last = {"repository": repository, "registry": registry}

    def run(
        self,
        config: BacktestConfig,
        strategy_ids: tuple[str, ...],
        on_progress: Any = None,
    ) -> Any:
        assert _StubRunner.last is not None
        _StubRunner.last.update(
            {"config": config, "strategy_ids": strategy_ids, "on_progress": on_progress}
        )
        return SimpleNamespace(results=[SimpleNamespace(trades=["t1", "t2"])])


@pytest.fixture
def stub_runner(monkeypatch: pytest.MonkeyPatch) -> type[_StubRunner]:
    _StubRunner.last = None
    monkeypatch.setattr(runner_module, "BacktestRunner", _StubRunner)
    return _StubRunner


def test_builds_config_and_collects_trades(stub_runner: type[_StubRunner]) -> None:
    repo, definition = object(), SimpleNamespace(id="strat-9")
    out = run_variant_backtest(repo, _FakeRegistry(definition), _dataset(), "exec-1", {"p": 0.5})

    assert stub_runner.last is not None
    assert stub_runner.last["repository"] is repo
    config = stub_runner.last["config"]
    assert isinstance(config, BacktestConfig)
    assert (config.symbol, config.timeframe) == ("T", "1D")
    assert (config.start_date, config.end_date) == ("2020-01-01", "2020-12-31")
    assert stub_runner.last["strategy_ids"] == ("strat-9",)

    assert isinstance(out, ResearchDataset)
    assert out.execution_ids[0] == "exec-1"
    assert out.trades == ("t1", "t2")
    assert out.signals == ("s1",)
    assert out.parameters == {"p": 0.5}
    assert out.metadata["backtest_executed"] is True


def test_registry_failure_falls_back_to_test_strategy(
    stub_runner: type[_StubRunner],
) -> None:
    class _FailThenTest(_FakeRegistry):
        def get(self, key: str) -> object:
            self.lookups.append(key)
            if key == "TestStrategy":
                return SimpleNamespace(id="test-strat")
            raise KeyError(key)

    out = run_variant_backtest(object(), _FailThenTest(None), _dataset(), "exec-2", {"p": 2.0})
    assert stub_runner.last is not None
    assert stub_runner.last["strategy_ids"] == ("test-strat",)
    assert out.execution_ids[0] == "exec-2"


def test_empty_result_yields_empty_trades(monkeypatch: pytest.MonkeyPatch) -> None:
    class _EmptyRunner(_StubRunner):
        def run(self, *_a: Any, **_k: Any) -> Any:
            return SimpleNamespace(results=[])

    monkeypatch.setattr(runner_module, "BacktestRunner", _EmptyRunner)
    out = run_variant_backtest(
        object(), _FakeRegistry(SimpleNamespace(id="s")), _dataset(), "exec-3", {"p": 3.0}
    )
    assert out.trades == ()
    assert out.metadata["backtest_executed"] is True
