"""Execution journal (JSONL audit trail) + stage latency telemetry.

Every decision answers WHAT/WHY/WHEN/WHICH: the journal is the permanent
record; latency tracking is pure measurement (no performance claims).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class JournalEntry:
    kind: str
    timestamp: str
    payload: dict[str, Any] = field(default_factory=dict)


class ExecutionJournal:
    """Append-only JSONL journal. One file per session; fsync-free stdlib IO."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._entries: list[JournalEntry] = []

    def record(self, kind: str, timestamp: str | None = None, **payload: Any) -> JournalEntry:
        entry = JournalEntry(kind=kind, timestamp=timestamp or utcnow_iso(), payload=dict(payload))
        self._entries.append(entry)
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps({"kind": entry.kind, "ts": entry.timestamp, **entry.payload}) + "\n"
                )
        return entry

    @property
    def entries(self) -> tuple[JournalEntry, ...]:
        return tuple(self._entries)

    def of_kind(self, kind: str) -> tuple[JournalEntry, ...]:
        return tuple(e for e in self._entries if e.kind == kind)


class LatencyTracker:
    """Stage latency recorder with p50/p95/p99/max summaries. Measurement only."""

    def __init__(self) -> None:
        self._samples: dict[str, list[float]] = {}

    def observe(self, stage: str, seconds: float) -> None:
        if seconds < 0:
            return
        self._samples.setdefault(stage, []).append(float(seconds))

    def summary(self, stage: str) -> dict[str, float | int]:
        samples = sorted(self._samples.get(stage, ()))
        if not samples:
            return {"n": 0}
        return {
            "n": len(samples),
            "p50": _pct(samples, 50),
            "p95": _pct(samples, 95),
            "p99": _pct(samples, 99),
            "max": samples[-1],
        }

    def stages(self) -> tuple[str, ...]:
        return tuple(sorted(self._samples))


def _pct(ordered: list[float], pct: float) -> float:
    if not ordered:
        return 0.0
    rank = min(len(ordered) - 1, max(0, int(pct / 100.0 * len(ordered))))
    return ordered[rank]


@dataclass
class StageTimer:
    """Manual stage timer: mark named checkpoints, read segment durations."""

    marks: list[tuple[str, float]] = field(default_factory=list)

    def mark(self, stage: str, epoch_seconds: float) -> None:
        self.marks.append((stage, float(epoch_seconds)))

    def segments(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for (first_stage, first_ts), (second_stage, second_ts) in zip(
            self.marks, self.marks[1:], strict=False
        ):
            out[f"{first_stage}->{second_stage}"] = max(0.0, second_ts - first_ts)
        return out
