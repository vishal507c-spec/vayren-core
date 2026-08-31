"""Performance memory — measured performance facts, never claims.

Components and workflows can store measured latency, throughput, CPU,
memory, I/O, and error rate. A record requires at least one measured
metric: unmeasured records are rejected.
"""

from dataclasses import dataclass

METRICS = (
    "latency_ms",
    "throughput",
    "cpu_percent",
    "memory_mb",
    "io_ops",
    "error_rate",
)

LOWER_IS_BETTER = frozenset({"latency_ms", "cpu_percent", "memory_mb", "io_ops", "error_rate"})


@dataclass(frozen=True)
class PerformanceRecord:
    """One measured performance sample of a component or workflow."""

    id: int
    subject: str
    benchmark: str = ""
    latency_ms: float | None = None
    throughput: float | None = None
    cpu_percent: float | None = None
    memory_mb: float | None = None
    io_ops: float | None = None
    error_rate: float | None = None


class PerformanceMemory:
    """Deterministic store of measured performance samples."""

    def __init__(self) -> None:
        self._records: list[PerformanceRecord] = []
        self._next_id = 1

    def record(
        self,
        subject: str,
        *,
        benchmark: str = "",
        latency_ms: float | None = None,
        throughput: float | None = None,
        cpu_percent: float | None = None,
        memory_mb: float | None = None,
        io_ops: float | None = None,
        error_rate: float | None = None,
    ) -> PerformanceRecord:
        """Store one measured sample; at least one metric is required."""
        values = (latency_ms, throughput, cpu_percent, memory_mb, io_ops, error_rate)
        if all(value is None for value in values):
            msg = "performance claims require measurements"
            raise ValueError(msg)
        entry = PerformanceRecord(
            id=self._next_id,
            subject=subject,
            benchmark=benchmark,
            latency_ms=latency_ms,
            throughput=throughput,
            cpu_percent=cpu_percent,
            memory_mb=memory_mb,
            io_ops=io_ops,
            error_rate=error_rate,
        )
        self._records.append(entry)
        self._next_id += 1
        return entry

    def for_subject(self, subject: str) -> tuple[PerformanceRecord, ...]:
        """Records of one subject, ordered by id."""
        return tuple(record for record in self._records if record.subject == subject)

    def latest(self, subject: str) -> PerformanceRecord | None:
        """The most recent record of one subject, or None."""
        records = self.for_subject(subject)
        return records[-1] if records else None

    def best(self, subject: str, metric: str) -> PerformanceRecord | None:
        """The best measured record of one subject for one metric, or None."""
        self._require_metric(metric)
        best_record: PerformanceRecord | None = None
        best_value: float | None = None
        for record in self.for_subject(subject):
            value = getattr(record, metric)
            if value is None:
                continue
            if best_value is None or self._better(value, best_value, metric):
                best_record = record
                best_value = value
        return best_record

    def average(self, subject: str, metric: str) -> float | None:
        """Mean of one metric over one subject's records, or None."""
        self._require_metric(metric)
        values = [
            getattr(record, metric)
            for record in self.for_subject(subject)
            if getattr(record, metric) is not None
        ]
        if not values:
            return None
        return sum(values) / len(values)

    def subjects(self) -> tuple[str, ...]:
        """Subjects with at least one record, sorted."""
        return tuple(sorted({record.subject for record in self._records}))

    def summary(self, subject: str) -> str:
        """Deterministic summary of one subject's measurements."""
        lines = [f"performance: {subject}"]
        for metric in METRICS:
            best = self.best(subject, metric)
            if best is not None:
                lines.append(
                    f"- {metric}: best {getattr(best, metric)} "
                    f"(record {best.id}, benchmark {best.benchmark or 'none'})"
                )
        return "\n".join(lines)

    def _require_metric(self, metric: str) -> None:
        if metric not in METRICS:
            msg = f"unknown metric: {metric}"
            raise ValueError(msg)

    @staticmethod
    def _better(candidate: float, current: float, metric: str) -> bool:
        if metric in LOWER_IS_BETTER:
            return candidate < current
        return candidate > current

    def __len__(self) -> int:
        return len(self._records)
