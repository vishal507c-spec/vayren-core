"""Optimization study tests — measured comparison, never auto-adoption."""

import pytest

from core.ai.memory.performance import PerformanceMemory
from core.ai.optimization import Candidate, OptimizationError, OptimizationStudy, RankedResult


def make_study() -> OptimizationStudy:
    performance = PerformanceMemory()
    study = OptimizationStudy(Candidate(label="current", description="baseline"), performance)
    study.add_candidate(Candidate(label="candidate-a", description="vectorized"))
    study.add_candidate(Candidate(label="candidate-b", description="cached"))
    return study


def test_candidates_include_current_and_alternatives() -> None:
    study = make_study()
    assert [candidate.label for candidate in study.candidates()] == [
        "candidate-a",
        "candidate-b",
        "current",
    ]


def test_duplicate_candidate_is_rejected() -> None:
    study = make_study()
    with pytest.raises(OptimizationError, match="candidate already registered"):
        study.add_candidate(Candidate(label="candidate-a"))


def test_benchmark_requires_known_candidate() -> None:
    study = make_study()
    with pytest.raises(OptimizationError, match="unknown candidate: ghost"):
        study.benchmark("ghost", latency_ms=1.0)


def test_compare_ranks_by_latency_ascending() -> None:
    study = make_study()
    study.benchmark("current", latency_ms=10.0)
    study.benchmark("candidate-a", latency_ms=4.0)
    study.benchmark("candidate-b", latency_ms=7.0)
    ranked = study.compare("latency_ms")
    assert [result.label for result in ranked] == ["candidate-a", "candidate-b", "current"]
    assert [result.rank for result in ranked] == [1, 2, 3]
    assert ranked[0].is_current is False
    assert ranked[2].is_current is True


def test_compare_ranks_by_throughput_descending() -> None:
    study = make_study()
    study.benchmark("current", throughput=100.0)
    study.benchmark("candidate-a", throughput=300.0)
    ranked = study.compare("throughput")
    assert ranked[0].label == "candidate-a"
    assert ranked[0].value == 300.0


def test_compare_skips_unmeasured_candidates() -> None:
    study = make_study()
    study.benchmark("current", latency_ms=10.0)
    ranked = study.compare("latency_ms")
    assert [result.label for result in ranked] == ["current"]


def test_recommend_returns_best_validated_candidate() -> None:
    study = make_study()
    study.benchmark("current", latency_ms=10.0)
    study.benchmark("candidate-a", latency_ms=4.0)
    recommendation = study.recommend("latency_ms")
    assert isinstance(recommendation, RankedResult)
    assert recommendation.label == "candidate-a"
    assert recommendation.value == 4.0


def test_adoption_is_never_automatic() -> None:
    study = make_study()
    study.benchmark("current", latency_ms=10.0)
    study.benchmark("candidate-a", latency_ms=4.0)
    with pytest.raises(
        OptimizationError, match="adoption requires a validated plan and sandbox approval"
    ):
        study.adopt()
