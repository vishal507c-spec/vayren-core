"""Engineering memory — structured, searchable record of engineering decisions.

Captures: Problem, Hypothesis, Experiment, Change, Benchmark, Result,
Decision, Reason, and Evidence. Every entry is immutable and searchable;
nothing here ever modifies the system.
"""

from dataclasses import dataclass
from enum import Enum


class Decision(Enum):
    """Outcome of an engineering experiment."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"


@dataclass(frozen=True)
class EngineeringEntry:
    """One immutable engineering memory entry."""

    id: int
    problem: str
    hypothesis: str
    experiment: tuple[str, ...] = ()
    change: str = ""
    benchmark: tuple[str, ...] = ()
    result: str = ""
    decision: Decision = Decision.DEFERRED
    reason: str = ""
    evidence: tuple[str, ...] = ()

    def matches(self, query: str) -> bool:
        """Case-insensitive substring match across every text field."""
        needle = query.lower()
        return any(needle in field.lower() for field in self._text_fields())

    def _text_fields(self) -> tuple[str, ...]:
        return (
            self.problem,
            self.hypothesis,
            self.change,
            self.result,
            self.reason,
            *self.experiment,
            *self.benchmark,
            *self.evidence,
        )


class EngineeringMemory:
    """Searchable, deterministic store of engineering entries."""

    def __init__(self) -> None:
        self._entries: list[EngineeringEntry] = []
        self._next_id = 1

    def add(
        self,
        problem: str,
        hypothesis: str,
        *,
        experiment: tuple[str, ...] = (),
        change: str = "",
        benchmark: tuple[str, ...] = (),
        result: str = "",
        decision: Decision = Decision.DEFERRED,
        reason: str = "",
        evidence: tuple[str, ...] = (),
    ) -> EngineeringEntry:
        """Append a new entry with an auto-incrementing id."""
        entry = EngineeringEntry(
            id=self._next_id,
            problem=problem,
            hypothesis=hypothesis,
            experiment=experiment,
            change=change,
            benchmark=benchmark,
            result=result,
            decision=decision,
            reason=reason,
            evidence=evidence,
        )
        self._entries.append(entry)
        self._next_id += 1
        return entry

    def get(self, entry_id: int) -> EngineeringEntry:
        """Return one entry by id, or raise KeyError."""
        for entry in self._entries:
            if entry.id == entry_id:
                return entry
        msg = f"Engineering entry not found: {entry_id}"
        raise KeyError(msg)

    def search(self, query: str) -> tuple[EngineeringEntry, ...]:
        """Entries matching the query, ordered by id."""
        return tuple(entry for entry in self._entries if entry.matches(query))

    def decisions(self) -> tuple[EngineeringEntry, ...]:
        """Entries with a final decision, ordered by id."""
        return tuple(entry for entry in self._entries if entry.decision is not Decision.DEFERRED)

    def problems(self) -> tuple[str, ...]:
        """Unique problems in insertion order."""
        return tuple(dict.fromkeys(entry.problem for entry in self._entries))

    def __iter__(self):
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
