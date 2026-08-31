"""Performance memory tests — measured facts only, no unmeasured claims."""

import pytest

from core.ai.memory.performance import METRICS, PerformanceMemory


def test_record_requires_at_least_one_measurement() -> None:
    memory = PerformanceMemory()
    with pytest.raises(ValueError, match="performance claims require measurements"):
        memory.record("market.query")


def test_record_with_measurement_is_stored() -> None:
    memory = PerformanceMemory()
    entry = memory.record("market.query", benchmark="query-1", latency_ms=2.5)
    assert entry.id == 1
    assert entry.subject == "market.query"
    assert entry.benchmark == "query-1"
    assert entry.latency_ms == 2.5
    assert entry.throughput is None
    assert len(memory) == 1


def test_metric_names_are_fixed() -> None:
    assert METRICS == (
        "latency_ms",
        "throughput",
        "cpu_percent",
        "memory_mb",
        "io_ops",
        "error_rate",
    )


def test_best_picks_lowest_for_latency() -> None:
    memory = PerformanceMemory()
    memory.record("load", latency_ms=10.0)
    memory.record("load", latency_ms=3.0)
    memory.record("load", latency_ms=7.0)
    best = memory.best("load", "latency_ms")
    assert best is not None
    assert best.latency_ms == 3.0


def test_best_picks_highest_for_throughput() -> None:
    memory = PerformanceMemory()
    memory.record("load", throughput=100.0)
    memory.record("load", throughput=500.0)
    best = memory.best("load", "throughput")
    assert best is not None
    assert best.throughput == 500.0


def test_best_skips_unmeasured_records() -> None:
    memory = PerformanceMemory()
    memory.record("load", latency_ms=4.0)
    memory.record("load", error_rate=0.01)
    best = memory.best("load", "latency_ms")
    assert best is not None
    assert best.latency_ms == 4.0


def test_best_unknown_metric_raises() -> None:
    memory = PerformanceMemory()
    memory.record("load", latency_ms=1.0)
    with pytest.raises(ValueError, match="unknown metric: flops"):
        memory.best("load", "flops")


def test_best_without_records_returns_none() -> None:
    memory = PerformanceMemory()
    assert memory.best("missing", "latency_ms") is None


def test_average_and_latest() -> None:
    memory = PerformanceMemory()
    memory.record("load", latency_ms=10.0)
    memory.record("load", latency_ms=20.0)
    assert memory.average("load", "latency_ms") == 15.0
    assert memory.average("load", "throughput") is None
    latest = memory.latest("load")
    assert latest is not None
    assert latest.latency_ms == 20.0
    assert memory.latest("missing") is None


def test_subjects_are_sorted() -> None:
    memory = PerformanceMemory()
    memory.record("zeta", latency_ms=1.0)
    memory.record("alpha", latency_ms=1.0)
    assert memory.subjects() == ("alpha", "zeta")


def test_summary_reports_best_measurements() -> None:
    memory = PerformanceMemory()
    memory.record("load", benchmark="b1", latency_ms=5.0)
    summary = memory.summary("load")
    assert "performance: load" in summary
    assert "latency_ms: best 5.0 (record 1, benchmark b1)" in summary
    assert "throughput" not in summary
