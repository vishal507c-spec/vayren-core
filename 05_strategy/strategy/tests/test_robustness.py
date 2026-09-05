"""Robustness sensitivity — structured path and executor injection.

The variant-execution helper lives in backtest (``run_variant_backtest``);
research receives it as ``variant_executor`` so strategy never imports
backtest. These tests pin both paths without touching backtest.
"""

from typing import Any

from strategy.research.dataset import ResearchDataset
from strategy.research.robustness import run_parameter_sensitivity


def _dataset() -> ResearchDataset:
    return ResearchDataset(
        strategy_id="strat-1",
        version_id="v1",
        execution_ids=("exec-0",),
        trades=(),
        signals=(),
        parameters={"p": 1.0},
        data_identity={"symbol": "T", "timeframe": "1D"},
        metadata={},
    )


def test_structured_path_without_executor() -> None:
    results = run_parameter_sensitivity(_dataset(), "p", [0.9, 1.0, 1.1])
    assert len(results) == 3
    for res, val in zip(results, [0.9, 1.0, 1.1], strict=True):
        assert res.test_type == "parameter_sensitivity"
        assert res.input == {"param": "p", "value": val}
        assert res.stability in ("PASS", "WARNING", "FAIL")
        assert res.evidence["variant"] == val


def test_repository_registry_kwargs_still_accepted() -> None:
    # Backward-compatible signature: without an executor these fall back to
    # the structured path (no backtest is executed from strategy).
    results = run_parameter_sensitivity(
        _dataset(), "p", [2.0], repository=object(), registry=object()
    )
    assert len(results) == 1
    assert results[0].evidence["note"] == "Backtest execution required for real metrics"


def test_injected_executor_receives_forwarded_args() -> None:
    calls: list = []
    canned = _dataset()

    def fake_executor(
        repository: Any,
        registry: Any,
        dataset: ResearchDataset,
        execution_id: str,
        variant_params: dict[str, float],
    ) -> ResearchDataset:
        calls.append((repository, registry, dataset, execution_id, variant_params))
        return canned

    repo, reg = object(), object()
    results = run_parameter_sensitivity(
        _dataset(), "p", [0.5], repository=repo, registry=reg, variant_executor=fake_executor
    )
    assert len(calls) == 1
    assert calls[0][0] is repo
    assert calls[0][1] is reg
    assert calls[0][2].strategy_id == "strat-1"
    assert isinstance(calls[0][3], str) and calls[0][3]
    assert calls[0][4] == {"p": 0.5}
    assert len(results) == 1
    assert results[0].evidence["variant"] == 0.5
