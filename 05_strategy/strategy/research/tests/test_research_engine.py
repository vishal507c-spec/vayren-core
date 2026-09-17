"""Real research engine: fingerprints, validation, conditions, stats, compare, report."""

from types import SimpleNamespace

from strategy.research.compare import compare_experiments
from strategy.research.conditions import analyze_conditions
from strategy.research.data_validation import validate_bars
from strategy.research.experiment import (
    EXPERIMENT_STATUSES,
    create_experiment,
    is_stale,
)
from strategy.research.fingerprint import fingerprint_config, fingerprint_result
from strategy.research.report import build_report, conclude
from strategy.research.statistics import (
    compare_buy_hold,
    describe_evidence,
    overfitting_warnings,
    run_monte_carlo,
)
from strategy.research.walkforward import (
    split_trades_chronological,
    summarize_walkforward,
    walk_forward_windows,
)


def _trade(
    symbol="A",
    side="LONG",
    pnl=10.0,
    entry="2024-01-02 09:15:00",
    exit_="2024-01-02 15:15:00",
    held=20,
    reason="SIGNAL",
):
    return SimpleNamespace(
        symbol=symbol,
        side=side,
        entry_index=1,
        exit_index=2,
        entry_time=entry,
        exit_time=exit_,
        entry_price=100.0,
        exit_price=101.0,
        quantity=1.0,
        pnl=pnl,
        pnl_pct=1.0,
        commission=0.0,
        bars_held=held,
        exit_reason=reason,
        r_multiple=None,
    )


def _bar(timestamp, open_, high, low, close, volume=1000):
    return SimpleNamespace(
        timestamp=timestamp, open=open_, high=high, low=low, close=close, volume=volume
    )


# ── fingerprints ──────────────────────────────────────────


def test_config_fingerprint_deterministic_and_sensitive() -> None:
    config = {"strategy_id": "OBR", "symbols": ["A"], "parameters": {"p": 1.0}}
    assert fingerprint_config(config) == fingerprint_config(dict(config))
    changed = dict(config, parameters={"p": 1.5})
    assert fingerprint_config(changed) != fingerprint_config(config)


def test_result_fingerprint_changes_with_outcome() -> None:
    base = {"trade_count": 5, "net_pnl": 100.0}
    assert fingerprint_result(base) == fingerprint_result(dict(base))
    assert fingerprint_result({**base, "net_pnl": 101.0}) != fingerprint_result(base)


# ── experiment model ──────────────────────────────────────


def test_experiment_statuses_cover_lifecycle() -> None:
    for status in (
        "DRAFT",
        "READY",
        "VALIDATING",
        "RUNNING",
        "ANALYZING",
        "VALIDATING_RESULT",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        "STALE",
        "INVALID",
        "NO_DATA",
    ):
        assert status in EXPERIMENT_STATUSES


def test_experiment_roundtrip_with_full_spec() -> None:
    exp = create_experiment(
        "OBR",
        "v1",
        [],
        "breakout persists",
        {"timeframe": "5m"},
        research_question="Why?",
        universe="NIFTY 500",
        symbols=["A", "B"],
        timeframe="5m",
        start_date="2023-01-01",
        end_date="2024-01-01",
        side="LONG",
        initial_capital=100000.0,
    )
    assert exp.status == "DRAFT"
    assert exp.symbols == ("A", "B")
    assert exp.hypothesis.research_question == "Why?"
    clone = type(exp).from_dict(exp.to_dict())
    assert clone.to_dict() == exp.to_dict()


def test_legacy_experiment_dict_still_loads() -> None:
    from strategy.research.experiment import Experiment

    legacy = {
        "experiment_id": "EXP-OLD",
        "strategy_id": "sma",
        "version_id": "v1",
        "execution_ids": ["exec-1"],
        "hypothesis": {"text": "fast beats slow"},
        "configuration": {"k": 1},
        "result": None,
        "created_at": "",
    }
    exp = Experiment.from_dict(legacy)
    assert exp.status == "DRAFT"
    assert exp.hypothesis.text == "fast beats slow"
    assert exp.to_dict()["experiment_id"] == "EXP-OLD"


def test_stale_detection() -> None:
    exp = create_experiment("s", "v", [], "h", {})
    exp.config_fingerprint = "aaa"
    exp.executed_at = "2024-01-01T00:00:00"
    assert is_stale(exp, "bbb") is True
    assert is_stale(exp, "aaa") is False


# ── data validation ───────────────────────────────────────


def test_validate_bars_passes_on_clean_data() -> None:
    bars = {
        "A": [_bar(f"2024-01-0{d} 09:15:00", 100, 102, 99, 101) for d in range(1, 6)],
    }
    report = validate_bars(bars, ["A"], "2024-01-01", "2024-01-05")
    assert report.ok is True
    assert report.missing_symbols == ()


def test_validate_bars_reports_missing_symbols() -> None:
    report = validate_bars({}, ["XYZ"], "2024-01-01", "2024-01-05")
    assert report.ok is False
    assert report.missing_symbols == ("XYZ",)
    assert "XYZ" in report.summary


def test_validate_bars_rejects_bad_ohlc_duplicates_and_order() -> None:
    dup = "2024-01-02 09:15:00"
    bars = {
        "A": [
            _bar("2024-01-03 09:15:00", 100, 102, 99, 101),
            _bar(dup, 100, 90, 99, 101),  # high below low + unordered
            _bar(dup, 100, 102, 99, 101),  # duplicate
        ],
    }
    report = validate_bars(bars, ["A"])
    assert report.ok is False
    kinds = {issue.kind for item in report.symbols for issue in item.issues}
    assert {"duplicate", "unordered", "invalid_ohlc"} <= kinds


# ── conditions ────────────────────────────────────────────


def test_condition_buckets_come_from_real_trades() -> None:
    trades = [
        _trade("A", "LONG", 10.0, "2024-01-02 09:15:00", "2024-01-02 10:15:00"),
        _trade("A", "LONG", -4.0, "2024-02-06 11:15:00", "2024-02-06 12:15:00"),
        _trade("B", "SHORT", 6.0, "2024-01-03 09:15:00", "2024-01-03 10:15:00"),
    ]
    result = analyze_conditions(trades)
    assert result.trade_count == 3
    by_symbol = {b.key: b for b in result.buckets["SYMBOL"]}
    assert by_symbol["A"].n == 2
    assert by_symbol["A"].total_pnl == 6.0
    assert by_symbol["B"].total_pnl == 6.0
    sides = {b.key: b.n for b in result.buckets["SIDE"]}
    assert sides == {"LONG": 2, "SHORT": 1}
    assert "2024-01" in {b.key for b in result.buckets["MONTH"]}
    assert "Tue" in {b.key for b in result.buckets["DAY_OF_WEEK"]}
    assert any("GAP_SIZE" in item for item in result.unsupported)
    assert any("VOLATILITY_REGIME" in item for item in result.unsupported)


def test_regime_analysis_with_real_windows() -> None:
    closes = [100.0 + (i % 5) for i in range(40)]
    window = tuple(
        _bar(f"2024-01-{i + 1:02d} 09:15:00", c - 1, c + 1, c - 2, c) for i, c in enumerate(closes)
    )
    trades = [
        _trade("A", "LONG", 5.0, "2024-01-25 09:15:00", "2024-01-26 09:15:00"),
        _trade("A", "LONG", -2.0, "2024-01-27 09:15:00", "2024-01-28 09:15:00"),
        _trade("A", "SHORT", 3.0, "2024-01-29 09:15:00", "2024-01-30 09:15:00"),
        _trade("A", "LONG", 1.0, "2024-02-01 09:15:00", "2024-02-02 09:15:00"),
        _trade("A", "SHORT", -1.0, "2024-02-03 09:15:00", "2024-02-04 09:15:00"),
        _trade("A", "LONG", 2.0, "2024-02-05 09:15:00", "2024-02-06 09:15:00"),
    ]
    for position, trade in enumerate(trades):
        trade.entry_index = 21 + position
    result = analyze_conditions(trades, {"A": window})
    assert "VOLATILITY_REGIME" in result.buckets
    assert "TREND_REGIME" in result.buckets
    assert sum(b.n for b in result.buckets["VOLATILITY_REGIME"]) == len(trades)


# ── statistics ────────────────────────────────────────────


def test_evidence_stats_are_honest() -> None:
    empty = describe_evidence([])
    assert empty.status == "INSUFFICIENT"
    assert empty.mean is None
    trades = [_trade(pnl=p) for p in (10.0, -5.0, 8.0, -2.0, 6.0, 4.0, -3.0, 7.0)]
    stats = describe_evidence(trades)
    assert stats.n == 8
    assert stats.ci_low is not None and stats.ci_high is not None
    assert stats.mean is not None
    assert stats.ci_low < stats.mean < stats.ci_high
    assert stats.p_value is not None and 0.0 <= stats.p_value <= 1.0
    assert stats.cohens_d is not None


def test_monte_carlo_deterministic_and_insufficient_when_small() -> None:
    assert run_monte_carlo([_trade(pnl=1.0)]).status == "INSUFFICIENT"
    trades = [_trade(pnl=float(p)) for p in (5, -2, 3, -1, 4, -3, 2, 1)]
    first = run_monte_carlo(trades, n_paths=200, seed=7)
    second = run_monte_carlo(trades, n_paths=200, seed=7)
    assert first.status == "OK"
    assert first.total_pnl_p50 == second.total_pnl_p50
    assert first.max_dd_p95 == second.max_dd_p95


def test_benchmark_needs_windows_and_capital() -> None:
    missing = compare_buy_hold(100.0, 10000.0, None, ["A"])
    assert missing.status == "UNAVAILABLE"
    assert missing.strategy_return_pct == 1.0
    no_capital = compare_buy_hold(100.0, 0.0, {}, [])
    assert no_capital.status == "UNAVAILABLE"
    window = (
        _bar("2024-01-01 09:15:00", 100, 101, 99, 100),
        _bar("2024-01-02 09:15:00", 100, 112, 100, 110),
    )
    bench = compare_buy_hold(500.0, 10000.0, {"A": window}, ["A"])
    assert bench.status == "OK"
    assert bench.benchmark_return_pct == 10.0
    assert bench.excess_return_pct == 5.0 - 10.0


def test_overfitting_warnings_trigger_only() -> None:
    assert any("small sample" in w for w in overfitting_warnings(5))
    assert any("multiple-comparison" in w for w in overfitting_warnings(100, 25))
    assert any("in-sample only" in w for w in overfitting_warnings(100, 1, False))
    assert overfitting_warnings(100, 1, True) == ()


# ── walk-forward ──────────────────────────────────────────


def test_walk_forward_windows_partition_range() -> None:
    folds = walk_forward_windows("2024-01-01", "2024-12-31", 3)
    assert len(folds) == 3
    assert folds[0].train_start == "2024-01-01"
    assert folds[-1].test_end == "2024-12-31"
    for fold in folds:
        assert fold.train_start <= fold.train_end < fold.test_start <= fold.test_end


def test_walkforward_summary_grades_consistency() -> None:
    assert summarize_walkforward([1.0, 2.0, 1.5]).status == "CONSISTENT"
    assert summarize_walkforward([-1.0, -2.0]).status == "FAILING"
    assert summarize_walkforward([1.0, -1.0]).status == "MIXED"
    assert summarize_walkforward([None, None]).status == "INSUFFICIENT"


def test_chronological_split_never_shuffles() -> None:
    trades = [_trade(entry=f"2024-01-{d:02d} 09:15:00") for d in range(1, 11)]
    inside, outside = split_trades_chronological(trades, 0.7)
    assert len(inside) == 7 and len(outside) == 3
    assert inside[-1].entry_time < outside[0].entry_time


# ── compare + report ──────────────────────────────────────


def _experiment_dict(exp_id, strategy="OBR", timeframe="5m", net_pnl=100.0, params=None):
    return {
        "experiment_id": exp_id,
        "strategy_id": strategy,
        "version_id": "v1",
        "configuration": {
            "strategy_id": strategy,
            "symbols": ["A"],
            "timeframe": timeframe,
            "start_date": "2024-01-01",
            "end_date": "2024-03-31",
            "parameters": params or {"p": 1.0},
            "slippage_pct": 0.02,
            "commission_pct": 0.03,
        },
        "result_summary": {"trade_count": 10, "net_pnl": net_pnl},
        "status": "COMPLETED",
    }


def test_compare_flags_incompatible_setups() -> None:
    left = _experiment_dict("EXP-A")
    same = _experiment_dict("EXP-B", net_pnl=150.0)
    result = compare_experiments(left, same)
    assert result.config_differences == ()
    assert result.verdict.startswith("EXP-B ahead")
    other_tf = _experiment_dict("EXP-C", timeframe="15m")
    divergent = compare_experiments(left, other_tf)
    assert "timeframe" in divergent.config_differences
    assert any("timeframe" in w for w in divergent.warnings)


def test_report_conclusion_never_overstates() -> None:
    assert conclude(0, None, False, False, None).startswith("Insufficient sample")
    assert conclude(5, 0.01, False, False, None).startswith("Insufficient sample")
    assert conclude(100, 0.001, True, True, True).startswith("Performance is unstable")
    assert conclude(100, 0.3, False, False, None).startswith("Evidence is inconclusive")
    assert conclude(100, 0.01, False, True, True).startswith("Evidence supports a persistent")
    assert conclude(100, 0.01, False, False, None).endswith("confirmation still required.")


def test_build_report_covers_all_sections() -> None:
    report = build_report(
        _experiment_dict("EXP-A"),
        {"trade_count": 50},
        {},
        [],
        {"has_oos": False},
        {"p_value": 0.2},
        {},
        None,
        ["limitation one"],
    )
    for section in (
        "research_question",
        "hypothesis",
        "strategy",
        "dataset",
        "universe",
        "configuration",
        "methodology",
        "signals",
        "trades",
        "performance",
        "risk",
        "regime_analysis",
        "robustness",
        "out_of_sample",
        "statistical_evidence",
        "limitations",
        "conclusion",
        "reproducibility",
    ):
        assert section in report
    assert report["limitations"] == ["limitation one"]
