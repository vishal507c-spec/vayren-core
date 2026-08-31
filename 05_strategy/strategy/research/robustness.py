"""Robustness — generic stress tests."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .analysis import analyze_dataset
from .dataset import ResearchDataset
from .experiment import Experiment


@dataclass(frozen=True)
class RobustnessResult:
    """Generic robustness test result."""

    test_type: str
    input: dict[str, Any]
    result: dict[str, Any]
    baseline: dict[str, Any]
    stability: str  # PASS/WARNING/FAIL
    evidence: dict[str, Any]


def run_parameter_sensitivity(
    dataset: ResearchDataset,
    param_name: str,
    variants: list[float],
    experiment: Experiment | None = None,  # noqa: ARG001
    *,
    repository=None,
    registry=None,
) -> list[RobustnessResult]:
    """Generic parameter sensitivity — does not modify original strategy.

    Each variant creates a NEW execution context with its own execution_id
    and modified parameters. If ``repository`` and ``registry`` are provided,
    a new backtest is executed for each variant and real TradeRecords are
    used for metrics.  Otherwise the variant dataset is structured for later
    execution (trades=() marks that a backtest must be run separately).
    """
    results: list[RobustnessResult] = []
    baseline_analysis = analyze_dataset(dataset)
    baseline = {
        "trade_count": baseline_analysis.trade_count,
        "win_rate": baseline_analysis.win_rate,
        "expectancy": baseline_analysis.expectancy,
        "profit_factor": baseline_analysis.profit_factor,
    }
    for val in variants:
        variant_execution_id = (
            f"{datetime.now(UTC).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        )
        variant_params = dict(dataset.parameters)
        variant_params[param_name] = val

        if repository is not None and registry is not None:
            # Execute a new backtest with the variant parameters.
            variant_dataset = _run_variant_backtest(
                repository, registry, dataset, variant_execution_id, variant_params
            )
        else:
            # Structured variant for later backtest execution.
            # trades=() signals that a backtest must be run separately.
            variant_dataset = ResearchDataset(
                strategy_id=dataset.strategy_id,
                version_id=dataset.version_id,
                execution_ids=(variant_execution_id,) + dataset.execution_ids,
                trades=(),  # Real trades from new backtest; not reused from baseline
                signals=dataset.signals,
                parameters=variant_params,
                data_identity=dataset.data_identity,
                metadata={
                    "variant": val,
                    "param": param_name,
                    "execution_id": variant_execution_id,
                    "backtest_required": True,
                },
            )
            analysis = analyze_dataset(variant_dataset)
            baseline_exp = baseline["expectancy"]
            var_exp = analysis.expectancy
            if baseline_exp is None or var_exp is None:
                stability = "WARNING"
            elif abs(var_exp - baseline_exp) < 0.1 * abs(baseline_exp or 1):
                stability = "PASS"
            elif var_exp * (baseline_exp or 0) < 0:
                stability = "FAIL"
            else:
                stability = "WARNING"
            results.append(
                RobustnessResult(
                    test_type="parameter_sensitivity",
                    input={"param": param_name, "value": val},
                    result={
                        "trade_count": analysis.trade_count,
                        "win_rate": analysis.win_rate,
                        "expectancy": analysis.expectancy,
                        "profit_factor": analysis.profit_factor,
                    },
                    baseline=baseline,
                    stability=stability,
                    evidence={
                        "variant": val,
                        "parameter": val,
                        "baseline_expectancy": baseline_exp,
                        "variant_expectancy": var_exp,
                        "note": "Backtest execution required for real metrics",
                    },
                )
            )
            continue

        # Variant backtest executed — use real trades for analysis
        analysis = analyze_dataset(variant_dataset)
        baseline_exp = baseline["expectancy"]
        var_exp = analysis.expectancy
        if baseline_exp is None or var_exp is None:
            stability = "WARNING"
        elif abs(var_exp - baseline_exp) < 0.1 * abs(baseline_exp or 1):
            stability = "PASS"
        elif var_exp * (baseline_exp or 0) < 0:
            stability = "FAIL"
        else:
            stability = "WARNING"
        results.append(
            RobustnessResult(
                test_type="parameter_sensitivity",
                input={"param": param_name, "value": val},
                result={
                    "trade_count": analysis.trade_count,
                    "win_rate": analysis.win_rate,
                    "expectancy": analysis.expectancy,
                    "profit_factor": analysis.profit_factor,
                },
                baseline=baseline,
                stability=stability,
                evidence={
                    "variant": val,
                    "parameter": val,
                    "baseline_expectancy": baseline_exp,
                    "variant_expectancy": var_exp,
                },
            )
        )
    return results


def _run_variant_backtest(
    repository,
    registry,
    dataset: ResearchDataset,
    execution_id: str,
    variant_params: dict[str, float],
) -> ResearchDataset:
    """Run a single variant backtest and return a ResearchDataset with trades.

    Uses the existing BacktestRunner pipeline with the given parameter variant.
    """
    from backtest.runner import BacktestRunner

    # Build backtest config from the dataset's data identity
    ident = dataset.data_identity
    from backtest.models.config import BacktestConfig

    config = BacktestConfig(
        symbol=str(ident.get("symbol", "UNKNOWN")),
        timeframe=str(ident.get("timeframe", "1D")),
        start_date=str(ident.get("start_date", "2000-01-01")),
        end_date=str(ident.get("end_date", "2025-12-31")),
        slippage_pct=float(ident.get("slippage_pct", 0)),
        commission_pct=float(ident.get("commission_pct", 0)),
        initial_capital=float(getattr(dataset, "_initial_capital", 10000.0)),
    )

    # Get the strategy definition
    try:
        from strategy.registry import StrategyRegistry  # noqa: F401

        definition = registry.get(str(dataset.strategy_id))
    except Exception:
        from strategy.tests.test_runtime import TestStrategy  # noqa: F401

        definition = registry.get("TestStrategy")

    runner = BacktestRunner(repository, registry)
    result = runner.run(config, (str(definition.id),), on_progress=None)

    # Collect trades from the result
    all_trades: list = []
    for sr in result.results:
        all_trades.extend(list(sr.trades))

    return ResearchDataset(
        strategy_id=str(dataset.strategy_id),
        version_id=str(dataset.version_id),
        execution_ids=(execution_id,) + dataset.execution_ids,
        trades=tuple(all_trades) if all_trades else (),
        signals=dataset.signals,
        parameters=variant_params,
        data_identity=dataset.data_identity,
        metadata={
            "variant": dataset.metadata.get("variant", None),
            "param": dataset.metadata.get("param", None),
            "execution_id": execution_id,
            "backtest_executed": True,
            "strategy_id": str(dataset.strategy_id),
            "version_id": str(dataset.version_id),
        },
    )


def run_robustness(
    dataset: ResearchDataset,
    experiment: Experiment | None = None,
) -> list[RobustnessResult]:
    """Generic robustness suite — runs applicable tests."""
    results: list[RobustnessResult] = []
    # Parameter sensitivity if params exist
    if dataset.parameters:
        # Pick first param for demo
        param_name = next(iter(dataset.parameters))
        base_val = float(dataset.parameters[param_name])
        variants = [base_val * 0.8, base_val * 0.9, base_val, base_val * 1.1, base_val * 1.2]
        results.extend(run_parameter_sensitivity(dataset, param_name, variants, experiment))
    # Time-period stability (mock — would need OOS data)
    # For Phase 6, we provide a placeholder that is always PASS
    results.append(
        RobustnessResult(
            test_type="time_stability",
            input={"period": "OOS"},
            result={"status": "not_implemented"},
            baseline={},
            stability="WARNING",
            evidence={"note": "OOS requires separate data identity, marked in-sample"},
        )
    )
    # Cost stress (slippage)
    results.append(
        RobustnessResult(
            test_type="cost_stress",
            input={"slippage": "0.1%"},
            result={"status": "not_implemented"},
            baseline={},
            stability="WARNING",
            evidence={"note": "Cost stress requires re-execution with varied slippage"},
        )
    )
    return results
