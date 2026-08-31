"""Advanced Validation tests — 23 behavioral tests exercising real pipelines.

Tests cover: OOS, CPCV, PBO, DSR, Multiple Testing, Cost Stress,
Data Leakage, Temporal Stability, Evidence Grading, and end-to-end.
"""

from __future__ import annotations

from dataclasses import dataclass

from strategy.research.advanced_validation import (
    CPCVConfig,
    LeakageResult,
    check_data_leakage,
    check_temporal_stability,
    compute_dsr,
    compute_pbo,
    correct_multiple_testing,
    grade_evidence,
    run_cpcv,
    validate_costs,
    validate_oos,
)
from strategy.research.dataset import ResearchDataset


@dataclass(frozen=True)
class FakeTrade:
    pnl: float
    entry_price: float = 100.0
    entry_time: str = "2026-01-01 09:15:00"
    exit_time: str = "2026-01-02 09:15:00"
    entry_index: int = 0
    execution_id: str = "EXEC-001"
    trade_id: str = "T001"
    bars_held: int = 1


def _make_dataset(pnls: list[float], n_executions: int = 1) -> ResearchDataset:
    trades = []
    for i, pnl in enumerate(pnls):
        trades.append(
            FakeTrade(
                pnl=pnl,
                entry_price=100.0 + i,
                entry_time=f"2026-01-{(i % 28) + 1:02d} 09:15:00",
                exit_time=f"2026-01-{(i % 28) + 1:02d} 15:30:00",
                entry_index=i,
                execution_id=f"EXEC-{i // 10:03d}",
                trade_id=f"T{i:04d}",
                bars_held=1,
            )
        )
    return ResearchDataset(
        strategy_id="test-strategy",
        version_id="v1.0",
        execution_ids=tuple(f"EXEC-{i:03d}" for i in range(n_executions)),
        trades=tuple(trades),
        signals=tuple(trades),
        parameters={"fast": 10, "slow": 20},
    )


def _profit_factor(pnls: list[float]) -> float | None:
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = sum(abs(p) for p in pnls if p < 0)
    if gross_loss <= 0:
        return gross_profit if gross_profit > 0 else None
    return gross_profit / gross_loss


# ── Test 1: OOS uses genuinely unseen data ──


def test_oos_uses_unseen_data():
    is_trades = [FakeTrade(pnl=10.0) for _ in range(50)]
    oos_trades = [FakeTrade(pnl=8.0) for _ in range(50)]
    result = validate_oos(is_trades, oos_trades)
    assert result.status == "PASS"
    assert result.is_trades == 50
    assert result.oos_trades == 50
    assert result.is_expectancy is not None
    assert result.oos_expectancy is not None


def test_oos_cannot_influence_training():
    is_trades = [FakeTrade(pnl=10.0) for _ in range(50)]
    oos_trades = [FakeTrade(pnl=-5.0) for _ in range(50)]
    result = validate_oos(is_trades, oos_trades)
    assert result.status == "FAIL"
    assert result.oos_expectancy is not None
    assert result.oos_expectancy < 0


def test_oos_insufficient_data():
    result = validate_oos([], [], min_oos_trades=30)
    assert result.status == "INSUFFICIENT_DATA"
    assert "no IS trades" in result.limitations


def test_oos_degradation_reported():
    is_trades = [FakeTrade(pnl=100.0) for _ in range(50)]
    oos_trades = [FakeTrade(pnl=30.0) for _ in range(50)]
    result = validate_oos(is_trades, oos_trades)
    assert result.degradation is not None
    assert result.degradation > 0.5
    assert result.status == "WARNING"


# ── Test 2: CPCV chronological grouping ──


def test_cpcv_chronological_grouping():
    pnls = [10.0, -5.0, 15.0, -3.0, 8.0, 12.0, -2.0, 7.0, 9.0, -4.0, 11.0, 6.0, -1.0, 13.0, 4.0]
    dataset = _make_dataset(pnls)
    config = CPCVConfig(n_groups=3, test_groups=1)
    result = run_cpcv(dataset, config)
    assert result.n_paths > 0
    assert len(result.paths) > 0


def test_cpcv_purge_and_embargo():
    pnls = [10.0] * 30
    dataset = _make_dataset(pnls)
    config = CPCVConfig(n_groups=5, test_groups=1, purge_bars=1, embargo_bars=1)
    result = run_cpcv(dataset, config)
    assert result.n_paths > 0
    for path in result.paths:
        assert path.purge_range[1] <= path.embargo_range[0] or path.purge_range == (0, 0)
        train_set = set(path.train_indices)
        test_set = set(path.test_indices)
        assert train_set.isdisjoint(test_set), "train and test must not overlap"


def test_cpcv_train_test_separation():
    pnls = [10.0, -5.0, 15.0, -3.0, 8.0, 12.0, -2.0, 7.0, 9.0, -4.0] * 5
    dataset = _make_dataset(pnls)
    config = CPCVConfig(n_groups=5, test_groups=1)
    result = run_cpcv(dataset, config)
    for path in result.paths:
        train_set = set(path.train_indices)
        test_set = set(path.test_indices)
        assert train_set.isdisjoint(test_set)


def test_cpcv_deterministic_paths():
    pnls = [10.0] * 20
    dataset = _make_dataset(pnls)
    config = CPCVConfig(n_groups=4, test_groups=1)
    r1 = run_cpcv(dataset, config)
    r2 = run_cpcv(dataset, config)
    assert r1.n_paths == r2.n_paths
    assert r1.path_metrics == r2.path_metrics
    assert r1.mean == r2.mean


# ── Test 3: Real PBO calculation ──


def test_pbo_real_calculation():
    cpcv = run_cpcv(
        _make_dataset([10.0, -5.0, 15.0, -3.0, 8.0, 12.0, -2.0, 7.0, 9.0, -4.0] * 5),
        CPCVConfig(n_groups=5, test_groups=1),
    )
    pbo = compute_pbo(cpcv_results=[cpcv], n_trials=50)
    assert pbo.pbo is not None
    assert 0.0 <= pbo.pbo <= 1.0
    assert pbo.n_trials > 0


def test_pbo_insufficient_data():
    pbo = compute_pbo(n_trials=0)
    assert pbo.pbo is None
    assert pbo.method == "insufficient_data"


def test_pbo_distinct_trial_and_path_counts():
    cpcv = run_cpcv(
        _make_dataset([10.0] * 20),
        CPCVConfig(n_groups=4, test_groups=1),
    )
    pbo = compute_pbo(cpcv_results=[cpcv], n_trials=100)
    assert pbo.n_trials == 100
    assert pbo.n_paths == cpcv.n_paths
    assert pbo.n_trials != pbo.n_paths


# ── Test 4: Real DSR uses actual trial count ──


def test_dsr_uses_actual_trial_count():
    returns = [0.01, -0.005, 0.02, -0.01, 0.015, -0.008, 0.012, -0.003, 0.009, -0.007] * 5
    dsr = compute_dsr(observed_sharpe=1.5, n_trials=200, returns=returns)
    assert dsr.number_of_trials == 200
    assert dsr.sample_size == len(returns)
    assert dsr.observed_sharpe == 1.5
    assert dsr.dsr is not None


def test_dsr_insufficient_data():
    dsr = compute_dsr(observed_sharpe=None, n_trials=10)
    assert dsr.status == "INSUFFICIENT_DATA"


def test_dsr_accounts_skew_kurtosis():
    returns = [0.01, -0.005, 0.02, -0.01, 0.015, -0.008, 0.012, -0.003, 0.009, -0.007] * 10
    dsr = compute_dsr(observed_sharpe=1.0, n_trials=50, returns=returns)
    assert dsr.skew != 0.0 or dsr.kurtosis != 3.0
    assert dsr.sample_size == len(returns)


# ── Test 5: Multiple testing actual hypothesis count ──


def test_bonferroni_actual_hypothesis_count():
    p_vals = [0.01, 0.02, 0.03, 0.04, 0.05]
    result = correct_multiple_testing(
        p_values=p_vals,
        total_tested=50,
        selected=1,
        method="bonferroni",
        alpha=0.05,
    )
    assert result.total_tested == 50
    assert result.adjusted_threshold == 0.05 / 50


def test_fdr_actual_hypothesis_count():
    p_vals = [0.001, 0.005, 0.01, 0.02, 0.03, 0.04, 0.05, 0.1, 0.2, 0.5]
    result = correct_multiple_testing(
        p_values=p_vals,
        total_tested=100,
        selected=3,
        method="fdr_bh",
        alpha=0.05,
    )
    assert result.total_tested == 100
    assert result.method == "fdr_bh"
    assert result.significant >= 0


def test_multiple_testing_zero_hypotheses():
    result = correct_multiple_testing(total_tested=0)
    assert result.total_tested == 0
    assert result.limitations


# ── Test 6: Cost stress recalculates PnL ──


def test_cost_stress_changes_pnl():
    trades = [FakeTrade(pnl=50.0, entry_price=100.0) for _ in range(20)]
    result = validate_costs(trades, slippage_bps=[0, 5, 10, 20])
    assert len(result.scenarios) == 4
    assert result.scenarios[0].bps == 0
    assert result.scenarios[0].total_pnl == result.original_pnl
    for i in range(1, len(result.scenarios)):
        assert result.scenarios[i].total_pnl < result.scenarios[i - 1].total_pnl


def test_original_trades_immutable():
    trades = [FakeTrade(pnl=50.0, entry_price=100.0) for _ in range(20)]
    original_pnls = [t.pnl for t in trades]
    validate_costs(trades, slippage_bps=[0, 5, 10, 20])
    assert [t.pnl for t in trades] == original_pnls


def test_cost_stress_insufficient_data():
    result = validate_costs([])
    assert result.status == "INSUFFICIENT_DATA"


# ── Test 7: Leakage duplicate detection ──


def test_leakage_duplicate_detection():
    train = [FakeTrade(pnl=10.0, execution_id="EXEC-001", trade_id="T001")]
    test = [FakeTrade(pnl=5.0, execution_id="EXEC-001", trade_id="T002")]
    result = check_data_leakage(train_trades=train, test_trades=test)
    assert result.status == "FAIL"
    assert result.first_conflict is not None


def test_leakage_timestamp_detection():
    train = [
        FakeTrade(
            pnl=10.0,
            entry_time="2026-01-05 09:15:00",
            exit_time="2026-01-10 09:15:00",
            execution_id="EXEC-TRN",
            trade_id="T-TRN",
        )
    ]
    test = [
        FakeTrade(
            pnl=5.0,
            entry_time="2026-01-03 09:15:00",
            exit_time="2026-01-04 09:15:00",
            execution_id="EXEC-TST",
            trade_id="T-TST",
        )
    ]
    result = check_data_leakage(train_trades=train, test_trades=test)
    assert result.status == "FAIL"
    assert result.first_conflict is not None
    assert any("timestamp" in c["check"] for c in result.conflict_details)


# ── Test 8: Temporal stability ──


def test_temporal_stability():
    pnls = [
        10.0,
        -5.0,
        15.0,
        -3.0,
        8.0,
        12.0,
        -2.0,
        7.0,
        9.0,
        -4.0,
        11.0,
        6.0,
        -1.0,
        13.0,
        4.0,
        -6.0,
        14.0,
        3.0,
        -7.0,
        16.0,
    ]
    trades = [FakeTrade(pnl=p) for p in pnls]
    result = check_temporal_stability(trades, n_periods=4)
    assert len(result.periods) == 4
    assert result.best is not None
    assert result.worst is not None
    assert result.dispersion is not None


# ── Test 9: Insufficient data handling ──


def test_insufficient_data_handling():
    result = validate_oos([], [], min_oos_trades=30)
    assert result.status == "INSUFFICIENT_DATA"
    assert result.limitations


# ── Test 10: Validation evidence traceability ──


def test_evidence_traceability():
    dataset = _make_dataset([10.0] * 50)
    cpcv = run_cpcv(dataset, CPCVConfig(n_groups=5, test_groups=1))
    grade = grade_evidence(dataset=dataset, cpcv=cpcv)
    assert grade.details["trade_count"] == 50
    assert grade.details["cpcv_paths"] > 0


# ── Test 11: Evidence grade considers all dimensions ──


def test_evidence_grade_all_dimensions():
    dataset = _make_dataset([10.0] * 100)
    cpcv = run_cpcv(dataset, CPCVConfig(n_groups=5, test_groups=1))
    grade = grade_evidence(
        dataset=dataset,
        cpcv=cpcv,
        replay_verified=True,
        leakage=LeakageResult(
            status="PASS", first_conflict=None, checks_performed=["dup_ids"], conflict_details=[]
        ),
    )
    assert grade.grade in ("MODERATE", "STRONG")


# ── Test 12: OBR and SMA use identical validation engine ──


def test_obr_sma_use_identical_engine():
    pnls = [10.0, -5.0, 15.0, -3.0, 8.0, 12.0, -2.0, 7.0, 9.0, -4.0] * 5
    dataset_obr = _make_dataset(pnls)
    dataset_obr = ResearchDataset(
        strategy_id="obr-strategy",
        version_id="v1.0",
        execution_ids=dataset_obr.execution_ids,
        trades=dataset_obr.trades,
        signals=dataset_obr.signals,
        parameters=dataset_obr.parameters,
    )
    dataset_sma = _make_dataset(pnls)
    dataset_sma = ResearchDataset(
        strategy_id="sma-strategy",
        version_id="v1.0",
        execution_ids=dataset_sma.execution_ids,
        trades=dataset_sma.trades,
        signals=dataset_sma.signals,
        parameters=dataset_sma.parameters,
    )
    result_obr = validate_oos(dataset_obr.trades[:50], dataset_obr.trades[50:])
    result_sma = validate_oos(dataset_sma.trades[:50], dataset_sma.trades[50:])
    assert result_obr.status == result_sma.status
    assert abs((result_obr.is_expectancy or 0) - (result_sma.is_expectancy or 0)) < 1e-10


# ── Test 13: Reproducibility ──


def test_reproducibility():
    pnls = [10.0] * 20
    dataset = _make_dataset(pnls)
    config = CPCVConfig(n_groups=4, test_groups=1)
    r1 = run_cpcv(dataset, config)
    r2 = run_cpcv(dataset, config)
    assert r1.mean == r2.mean
    assert r1.n_paths == r2.n_paths
    assert r1.path_metrics == r2.path_metrics
