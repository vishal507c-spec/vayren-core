"""Architecture optimization — comparing alternative implementations.

Current implementation is benchmarked against candidates A, B, C; results
are compared and the best validated candidate is reported. Studies never
replace production implementations: adoption requires a validated plan and
sandbox approval (guarded self-optimization).
"""

from dataclasses import dataclass

from core.ai.memory.performance import (
    LOWER_IS_BETTER,
    METRICS,
    PerformanceMemory,
    PerformanceRecord,
)


@dataclass(frozen=True)
class Candidate:
    """One implementation under evaluation. Never executed here."""

    label: str
    description: str = ""
    implementation: object | None = None


@dataclass(frozen=True)
class RankedResult:
    """One candidate ranked by a measured metric."""

    label: str
    value: float
    is_current: bool
    rank: int


class OptimizationError(ValueError):
    """Raised when an optimization operation is not allowed."""


class OptimizationStudy:
    """Compares implementations by measured performance. Never auto-adopts."""

    def __init__(
        self,
        current: Candidate,
        performance: PerformanceMemory,
    ) -> None:
        self._current = current
        self._performance = performance
        self._candidates: dict[str, Candidate] = {current.label: current}

    def add_candidate(self, candidate: Candidate) -> None:
        """Register an alternative candidate; duplicate labels are rejected."""
        if candidate.label in self._candidates:
            msg = f"candidate already registered: {candidate.label}"
            raise OptimizationError(msg)
        self._candidates[candidate.label] = candidate

    def candidates(self) -> tuple[Candidate, ...]:
        """All candidates, ordered by label."""
        return tuple(self._candidates[label] for label in sorted(self._candidates))

    def benchmark(self, label: str, **metrics: float) -> PerformanceRecord:
        """Benchmark one candidate; results go to performance memory."""
        if label not in self._candidates:
            msg = f"unknown candidate: {label}"
            raise OptimizationError(msg)
        unknown = set(metrics) - set(METRICS)
        if unknown:
            msg = "unknown metrics: " + ", ".join(sorted(unknown))
            raise OptimizationError(msg)
        if all(value is None for value in metrics.values()):
            msg = "benchmark requires at least one measurement"
            raise OptimizationError(msg)
        return self._performance.record(
            label,
            latency_ms=metrics.get("latency_ms"),
            throughput=metrics.get("throughput"),
            cpu_percent=metrics.get("cpu_percent"),
            memory_mb=metrics.get("memory_mb"),
            io_ops=metrics.get("io_ops"),
            error_rate=metrics.get("error_rate"),
        )

    def compare(self, metric: str) -> tuple[RankedResult, ...]:
        """Rank measured candidates by one metric, best first."""
        ranked: list[tuple[float, str]] = []
        for label in self._candidates:
            best = self._performance.best(label, metric)
            if best is None:
                continue
            ranked.append((getattr(best, metric), label))
        reverse = metric not in LOWER_IS_BETTER
        ranked.sort(key=lambda item: item[0], reverse=reverse)
        return tuple(
            RankedResult(
                label=label,
                value=value,
                is_current=label == self._current.label,
                rank=index + 1,
            )
            for index, (value, label) in enumerate(ranked)
        )

    def recommend(self, metric: str) -> RankedResult | None:
        """The best validated candidate for one metric, or None."""
        ranked = self.compare(metric)
        return ranked[0] if ranked else None

    def adopt(self) -> None:
        """Never allowed: adoption requires a validated plan and approval."""
        msg = "adoption requires a validated plan and sandbox approval; studies never auto-adopt"
        raise OptimizationError(msg)
